"""Local presets use exact Ollama tags and retain saved translation routes."""
from pathlib import Path
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'runtime/lib')]

import yaml
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from credential_store import MemoryCredentialStore
from native_launcher import TranslatorController, model_route
from ui_messages import MessageCode


class LocalModelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='whaleread-local-presets-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = QSettings(str(self.root / 'settings.ini'), QSettings.IniFormat)
        self.tags = patch('native_launcher.ollama_models', return_value=set())
        self.installed = self.tags.start()
        self.addCleanup(self.tags.stop)
        self.owner = self.make_owner()

    def make_owner(self):
        owner = TranslatorController(workspace=self.root / 'Tasks', settings=self.settings,
                                     credential_store=MemoryCredentialStore(), restore=False)
        self.addCleanup(owner.shutdown)
        return owner

    def test_18b_is_not_ready_when_only_7b_is_installed_and_probe_sends_nothing(self):
        self.installed.return_value = {'jingdu-hy-mt2:7b-q4'}
        self.owner.translationProfile = 'local_1_8b'
        self.assertFalse(self.owner.modelReady)
        self.assertEqual(self.owner.modelStatusCode, MessageCode.MODEL_LOCAL_NOT_LOADED)
        with patch('native_launcher.QProcess') as process:
            self.owner.probeTranslation('The harbor was quiet.')
        process.assert_not_called()
        self.installed.return_value = {'jingdu-hy-mt2:1.8b-q8'}
        self.owner.refreshModelStatus()
        self.assertTrue(self.owner.modelReady)
        self.assertEqual(self.owner.modelStatusCode, MessageCode.MODEL_LOCAL_READY)
        self.assertIn('1.8B', self.owner.modelDisplayName)
        self.owner.translationProfile = 'local_7b'
        self.assertFalse(self.owner.modelReady)

    def test_18b_task_uses_small_chunks_and_restores_its_locked_route(self):
        self.installed.return_value = {'jingdu-hy-mt2:1.8b-q8', 'jingdu-hy-mt2:7b-q4'}
        self.owner.translationProfile = 'local_1_8b'
        source = self.root / 'fictional.txt'
        source.write_text('Mara walked to the quiet harbor. She carried a sealed letter.\n\n'
                          'The boat would return after sunset.', encoding='utf-8')
        self.owner._select_source(source)
        project = self.owner._prepare_job()
        config_path = project / '翻译任务.yaml'
        before = config_path.read_bytes()
        config = yaml.safe_load(before)
        self.assertEqual(config['engine']['profile'], 'local_1_8b')
        self.assertEqual(config['engine']['model'], 'jingdu-hy-mt2:1.8b-q8')
        self.assertEqual(config['chunking']['parallel_requests'], 1)
        self.assertLessEqual(config['chunking']['max_source_chars'], 1400)
        self.assertLessEqual(config['chunking']['max_segments'], 8)
        # A saved checkpoint locks an edition. Reopening must not adopt the
        # default 7B or rewrite the previous route/chunking configuration.
        (project / '.hy-name-review.json').write_text('{}')
        self.owner.shutdown()
        restored = self.make_owner()
        restored._select_source(source)
        self.assertTrue(restored.taskLocked)
        self.assertEqual(restored.translationProfile, 'local_1_8b')
        restored.translationProfile = 'local_7b'
        self.assertEqual(restored.translationProfile, 'local_1_8b')
        self.assertEqual(restored._prepare_job(), project)
        self.assertEqual(config_path.read_bytes(), before)
        restored._bound_engine['model'] = 'jingdu-hy-mt2:7b-q4'
        with self.assertRaises(ValueError):
            restored.translation_route()

    def test_legacy_helper_and_key_import_cannot_turn_18b_into_another_route(self):
        self.assertEqual(model_route('local_1_8b')['model'], 'jingdu-hy-mt2:1.8b-q8')
        self.owner.providers.select('ask', 'local_1_8b')
        with patch('data_settings.choose_open_file') as choose:
            self.owner.dataSettings.importLegacyAskKey()
        choose.assert_not_called()
        self.assertFalse(self.owner.providers.feature_route('ask')['auth_required'])

    def test_register_only_18b_from_a_directory_with_spaces_without_7b(self):
        model_dir = self.root / 'fictional models'
        model_dir.mkdir()
        weight = model_dir / 'Hy-MT2-1.8B-Q8_0.gguf'
        weight.write_bytes(b'fixture: not a real model')
        log = self.root / 'commands.jsonl'
        fake = self.root / 'fake-ollama'
        fake.write_text('#!' + sys.executable + '\n'
            'import json,sys\nfrom pathlib import Path\n'
            f'log=Path({str(log)!r})\n'
            "row={'args':sys.argv[1:]}\n"
            "if sys.argv[1]=='create': row['modelfile']=Path(sys.argv[4]).read_text()\n"
            "with log.open('a') as out: out.write(json.dumps(row)+'\\n')\n")
        fake.chmod(0o700)
        env = dict(os.environ, OLLAMA_BIN=str(fake), HY_SETUP_PYTHON=sys.executable)
        command = ['zsh', str(ROOT / 'scripts/register_local_models.sh'),
                   '1.8b', '--model-dir', str(model_dir)]
        result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(rows[0]['args'][:2], ['create', 'jingdu-hy-mt2:1.8b-q8'])
        self.assertEqual(rows[0]['modelfile'].splitlines()[0],
                         'FROM ' + json.dumps(str(weight.resolve()), ensure_ascii=False))
        self.assertIn('PARAMETER num_ctx 16384', rows[0]['modelfile'])
        self.assertEqual(rows[1]['args'], ['list'])
        self.assertEqual(list(model_dir.iterdir()), [weight])
        self.assertFalse(Path(rows[0]['args'][3]).exists())
        before = log.read_bytes()
        missing = subprocess.run(command[:2] + ['all', '--model-dir', str(model_dir)],
                                 env=env, capture_output=True, text=True, timeout=10)
        self.assertNotEqual(missing.returncode, 0)
        self.assertEqual(log.read_bytes(), before, 'preflight must finish before either model is registered')


if __name__ == '__main__':
    unittest.main()
