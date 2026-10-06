from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from native_launcher import TranslatorController  # noqa: E402

ENGLISH = ("The harbor was silent, and the wind crossed the old stone wall. "
           "She had not seen him since the winter.\n\n") * 8
FRENCH = ("Le port était silencieux, et le vent traversait le vieux mur de pierre. "
          "Elle ne l'avait pas vu depuis l'hiver.\n\n") * 8


class LanguageControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def owner(self, root, restore=False):
        owner = TranslatorController(
            workspace=root / 'jobs',
            settings=QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat),
            restore=restore)
        self.addCleanup(owner.shutdown)
        owner.reader_timer.stop()
        return owner

    def test_direction_card_reflects_detection_and_manual_choice(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            owner = self.owner(root)
            source = root / 'english.txt'
            source.write_text(ENGLISH, encoding='utf-8')
            owner._select_source(source)
            self.assertIn('英语', owner.languageLabel)
            self.assertIn('简体中文', owner.languageLabel)
            self.assertTrue(owner.languageAuto)
            self.assertEqual(owner.languageNotice, '')

            owner.setLanguageDirection('法语', '英语', False)
            self.assertEqual(owner.languageLabel, '法语 → 英语')
            self.assertFalse(owner.languageAuto)
            self.assertEqual(owner.sourceLanguage, '法语')
            self.assertEqual(owner.targetLanguage, '英语')

    def test_background_inspection_updates_the_card(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            owner = self.owner(root)
            source = root / 'french.txt'
            source.write_text(FRENCH, encoding='utf-8')
            owner._select_source(source, reader_async=True)
            self.assertTrue(owner.languageAuto)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline and not owner.detectedLanguage:
                self.app.processEvents()
                time.sleep(.01)
            self.assertEqual(owner.detectedLanguage, '法语')
            self.assertIn('法语', owner.languageLabel)
            self.assertIn('自动识别', owner.languageLabel)

    def test_switching_books_keeps_each_direction(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            owner = self.owner(root)
            first = root / 'first.txt'
            first.write_text(ENGLISH, encoding='utf-8')
            second = root / 'second.txt'
            second.write_text(FRENCH, encoding='utf-8')
            owner._select_source(first)
            owner.setLanguageDirection('法语', '英语', False)
            owner._select_source(second)
            self.assertTrue(owner.languageAuto)
            self.assertIn('法语', owner.detectedLanguage)
            owner._select_source(first)
            self.assertEqual(owner.languageLabel, '法语 → 英语')
            self.assertFalse(owner.languageAuto)

    def test_reopen_shows_the_direction_saved_in_the_task_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            owner = self.owner(root)
            source = root / 'story.txt'
            source.write_text(FRENCH, encoding='utf-8')
            owner._select_source(source)
            owner.setLanguageDirection('法语', '英语', False)
            with patch.object(owner, '_route_ready', return_value=True):
                project = owner._prepare_job()
            config = yaml.safe_load((project / '翻译任务.yaml').read_text(encoding='utf-8'))
            self.assertEqual(config['languages'], {'source': '法语', 'target': '英语'})
            self.assertEqual(owner.languageLabel, '法语 → 英语')
            self.assertEqual(owner._managed_output(project, config).endswith('.en.txt'), True)

            reopened = self.owner(root, restore=False)
            reopened._select_source(source, project=project)
            self.assertEqual(reopened.languageLabel, '法语 → 英语')
            self.assertEqual(reopened.targetLanguage, '英语')

    def test_ambiguous_source_keeps_auto_until_manual_choice(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            owner = self.owner(root)
            source = root / 'ambiguous.txt'
            source.write_text(('Mara arrived. Iven waited. Letter came. '
                               'North harbor. Silver register.\n\n') * 8, encoding='utf-8')
            owner._select_source(source)
            self.assertTrue(owner.languageAuto)
            self.assertEqual(owner.detectedLanguage, '')
            self.assertIn('手动', owner.languageNotice)
            owner.setUiLocale('en')
            self.assertIn('manually', owner.languageNotice)
            self.assertNotIn('正文以拉丁字母为主', owner.languageNotice)
            owner.setUiLocale('zh-CN')
            owner.setLanguageDirection('法语', '简体中文', False)
            self.assertEqual(owner.languageLabel, '法语 → 简体中文')
            self.assertEqual(owner.languageNotice, '')

    def test_script_clash_is_explained_but_not_silently_rewritten(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            owner = self.owner(root)
            source = root / 'chinese.txt'
            source.write_text('港口一片寂静，海风吹过古老的石墙。' * 30, encoding='utf-8')
            owner._select_source(source)
            owner.setLanguageDirection('英语', '简体中文', False)
            self.assertEqual(owner.languageLabel, '英语 → 简体中文')
            self.assertIn('文字系统不同', owner.languageNotice)

    def test_checkpointed_task_refuses_in_place_change_and_offers_new_edition(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            owner = self.owner(root)
            source = root / 'story.txt'
            source.write_text(ENGLISH, encoding='utf-8')
            owner._select_source(source)
            with patch.object(owner, '_route_ready', return_value=True):
                project = owner._prepare_job()
            state = project / '译文/.hy-direct-state.json'
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(json.dumps({'chunks': [{'start': 1, 'end': 1}]}), encoding='utf-8')
            owner._select_source(source, project=project)
            self.assertTrue(owner.languageLocked)
            saved = owner.languageLabel
            owner.setLanguageDirection('法语', '英语', False)
            self.assertEqual(owner.languageLabel, saved)
            self.assertIn('新建另一译本', owner.languageNotice)

            owner.newEditionWithLanguage('法语', '英语', False)
            self.assertFalse(owner.languageLocked)
            self.assertEqual(owner.languageLabel, '法语 → 英语')
            self.assertNotEqual(owner.jobPath, str(project))

    def test_invalid_language_cannot_change_or_create_an_edition(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            owner = self.owner(root)
            source = root / 'story.txt'
            source.write_text(ENGLISH, encoding='utf-8')
            owner._select_source(source)
            original_job = owner.jobPath
            original_direction = owner.languageLabel
            self.assertFalse(owner.setLanguageDirection('英语', '', False))
            self.assertEqual(owner.languageLabel, original_direction)
            self.assertFalse(owner.newEditionWithLanguage('英语', '', False))
            self.assertFalse(owner.newEditionWithLanguage('', '法语', False))
            self.assertEqual(owner.jobPath, original_job)
            self.assertEqual(owner.languageLabel, original_direction)

    def test_each_book_keeps_its_own_saved_choice_after_restart(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / 'first.txt'
            first.write_text(ENGLISH, encoding='utf-8')
            owner = self.owner(root)
            owner._select_source(first)
            owner.setLanguageDirection('德语', '英语', False)
            owner.shutdown()

            reopened = self.owner(root, restore=False)
            reopened._select_source(first)
            self.assertEqual(reopened.languageLabel, '德语 → 英语')
            self.assertFalse(reopened.languageAuto)


if __name__ == '__main__':
    unittest.main()
