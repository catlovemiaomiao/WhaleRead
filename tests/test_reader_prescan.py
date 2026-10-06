from __future__ import annotations
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "runtime/lib"))
from PySide6.QtCore import QSettings, QUrl
from PySide6.QtWidgets import QApplication
import glossary_prescan as gs
import glossary as gl
from reader import ReaderModel, paragraphs
from native_launcher import TranslatorController
import yaml


class PrescanTests(unittest.TestCase):
    def test_candidates_span_book_and_filter_common_words(self):
        segments = ["Mara reached North Harbor. The tide was low.", "Mara spoke to Iven at North Harbor.", "Iven returned. Mara stayed at North Harbor."]
        candidates, meta = gs.index_candidates(segments)
        names = {c['source']: c for c in candidates}
        self.assertIn("Mara", names)
        self.assertIn("North Harbor", names)
        self.assertNotIn("Harbor", names)
        self.assertNotIn("North", names)
        self.assertNotIn("The", names)
        self.assertEqual([c['segment'] for c in names['Mara']['contexts']], [1, 2, 3])
        self.assertEqual(meta['segments_scanned'], 3)
        self.assertEqual(gs.index_candidates(["春子は東京へ帰った。"])[1]['strategy'], "fulltext")

    def test_default_mode_makes_no_request(self):
        self.assertEqual(gl.prepare_glossary(Path('.'), {"glossary": {"mode": "none"}}, [], "h", lambda _: self.fail("request"), lambda _: None), [])

    def test_resume_freeze_and_no_rescan(self):
        segments = [" ".join(f"Name{chr(65+i)} arrived." for i in range(20))] * 3
        cfg = {"languages": {"source": "英语", "target": "简体中文"}, "glossary": {"mode": "auto", "strategy": gs.VERSION}}
        candidates, _ = gs.index_candidates(segments)
        calls = []
        def request(prompt):
            batch = json.loads(prompt.split("候选与原文证据：\n", 1)[1])
            calls.append(batch)
            if len(calls) == 2:
                raise TimeoutError("offline")
            return json.dumps([{"source": row["source"], "target": "译名" + row["source"], "note": ""} for row in batch]), .1
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with self.assertRaises(TimeoutError):
                gl.prepare_glossary(root, cfg, segments, 'same', request, lambda _: None)
            self.assertFalse((root / '术语表.auto.json').exists())
            rows = gl.prepare_glossary(root, cfg, segments, 'same', request, lambda _: None)
            self.assertEqual(len(rows), len(candidates))
            frozen = json.loads((root / '术语表.auto.json').read_text())
            self.assertEqual(frozen['status'], 'locked')
            self.assertEqual(gl.prepare_glossary(root, cfg, segments, 'same', lambda _: self.fail('rescan'), lambda _: None), rows)
            with self.assertRaises(ValueError):
                gl.prepare_glossary(root, cfg, segments, 'different', request, lambda _: None)

    def test_rejects_fabricated_names_english_targets_and_conflicts(self):
        batch = [{'source': 'Mara', 'contexts': [{'text': 'Mara met Iven.'}]}]
        for rows in [[{'source': 'Iven', 'target': '伊文'}], [{'source': 'Mara', 'target': 'Mara'}],
                     [{'source': 'Mara', 'target': '玛拉'}, {'source': 'Mara', 'target': '马拉'}]]:
            with self.assertRaises(ValueError):
                gs.validate_review(json.dumps(rows), batch, '简体中文')

    def test_one_english_target_is_isolated_without_losing_valid_names(self):
        candidates = [{'source': name, 'contexts': [{'text': 'Mara reached the Gate. Get inside.'}]} for name in ['Gate', 'Get', 'Mara']]
        cfg = {'languages': {'source': '英语', 'target': '简体中文'}, 'glossary': {'mode': 'auto', 'strategy': gs.VERSION}}
        calls, events = [], []
        def request(prompt):
            batch = json.loads(prompt.split('候选与原文证据：\n')[1])
            calls.append([r['source'] for r in batch])
            return json.dumps([{'source': r['source'], 'target': 'Gate' if r['source'] == 'Gate' else '玛拉'} for r in batch]), .1
        with tempfile.TemporaryDirectory() as raw, patch.object(gs, 'index_candidates', return_value=(candidates, {'strategy': 'capitalized-contexts'})):
            root = Path(raw)
            rows = gs.prepare(root, cfg, [], 'same', request, events.append)
            self.assertEqual(calls, [['Gate', 'Mara'], ['Gate']])
            self.assertEqual([r['source'] for r in rows], ['Mara'])
            report = json.loads((root/'译名待核对.json').read_text())
            self.assertEqual(report['entries'][0]['source'], 'Gate')
            self.assertEqual(events[-1]['pending_review'], 1)
            self.assertEqual(gs.prepare(root, cfg, [], 'same', lambda _: self.fail('locked table rescan'), events.append), rows)

    def test_network_failure_during_retry_retains_good_sibling(self):
        candidates = [{'source': name, 'contexts': [{'text': 'Mara reached the Gate.'}]} for name in ['Gate', 'Mara']]
        cfg = {'languages': {'source': '英语', 'target': '简体中文'}, 'glossary': {'mode': 'auto', 'strategy': gs.VERSION}}
        calls = []
        def failed(prompt):
            calls.append(prompt)
            if len(calls) == 2:
                raise TimeoutError('network unavailable')
            return '[{"source":"Gate","target":"Gate"},{"source":"Mara","target":"玛拉"}]', .1
        with tempfile.TemporaryDirectory() as raw, patch.object(gs, 'index_candidates', return_value=(candidates, {'strategy':'capitalized-contexts'})):
            root = Path(raw)
            with self.assertRaises(TimeoutError):
                gs.prepare(root, cfg, [], 'h', failed, lambda _: None)
            state = json.loads((root/'.hy-name-review.json').read_text())
            self.assertEqual(state['items']['mara'][0]['target'], '玛拉')
            def resumed(prompt):
                batch = json.loads(prompt.split('候选与原文证据：\n')[1])
                self.assertEqual([r['source'] for r in batch], ['Gate'])
                return '[{"source":"Gate","target":"城门"}]', .1
            rows = gs.prepare(root, cfg, [], 'h', resumed, lambda _: None)
            self.assertEqual(len(rows), 2)


class ReaderControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @classmethod
    def wait_reader(cls, controller, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            cls.app.processEvents()
            if not any(column.loading or getattr(column, '_refresh_token', '')
                       for column in controller.readingColumns):
                cls.app.processEvents()
                return
            time.sleep(.01)
        raise AssertionError('reader background load did not finish')

    def test_epub_line_height_is_bounded_and_persisted(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
            controller = TranslatorController(workspace=root / 'jobs', settings=settings, restore=False)
            self.assertAlmostEqual(controller.epubLineHeight, 1.85)
            controller.epubLineHeight = 2.2
            self.assertAlmostEqual(controller.epubLineHeight, 2.2)
            self.assertAlmostEqual(controller.readingLineHeight, 2.2)
            controller.epubLineHeight = 9
            self.assertAlmostEqual(controller.epubLineHeight, 2.6)
            controller.epubLineHeight = 1
            self.assertAlmostEqual(controller.epubLineHeight, 1.3)
            controller.epubLineHeight = 'invalid'
            self.assertAlmostEqual(controller.epubLineHeight, 1.3)
            settings.sync()
            restored = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
            self.assertAlmostEqual(float(restored.value('reader/epub_line_height')), 1.3)
            self.assertAlmostEqual(float(restored.value('reader/line_height')), 1.3)
            controller.reader_timer.stop()

    def test_reading_line_height_migrates_legacy_epub_setting(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
            settings.setValue('reader/epub_line_height', 2.15)
            controller = TranslatorController(workspace=root / 'jobs', settings=settings, restore=False)
            self.assertAlmostEqual(controller.readingLineHeight, 2.15)
            controller.readingLineHeight = 2.3
            self.assertAlmostEqual(controller.epubLineHeight, 2.3)
            controller.reader_timer.stop()

    def test_reading_brightness_is_bounded_and_persisted(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
            controller = TranslatorController(workspace=root / 'jobs', settings=settings, restore=False)
            self.assertAlmostEqual(controller.readingBrightness, 1.0)
            controller.readingBrightness = .75
            self.assertAlmostEqual(controller.readingBrightness, .75)
            controller.readingBrightness = 9
            self.assertAlmostEqual(controller.readingBrightness, 1.1)
            controller.readingBrightness = 0
            self.assertAlmostEqual(controller.readingBrightness, .6)
            controller.readingBrightness = 'invalid'
            self.assertAlmostEqual(controller.readingBrightness, .6)
            settings.sync()
            restored = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
            self.assertAlmostEqual(float(restored.value('reader/brightness')), .6)
            controller.reader_timer.stop()

    def test_append_no_reset_plaintext_search_and_final_rename(self):
        model = ReaderModel()
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'book.txt.partial'
            path.write_text('第一章 归来\n\n玛拉回到北港。\n<script>stay literal</script>')
            model.load(path, identity='book')
            self.assertEqual(model.count, 3)
            self.assertEqual(model.search('北港'), 1)
            self.assertEqual(model.chapters[0]['position'], 0)
            resets = []
            model.modelReset.connect(lambda: resets.append(True))
            reset_starts = []
            model.resetStarting.connect(lambda: reset_starts.append(True))
            path.write_text(path.read_text() + '\n新的译文。')
            model.load(path, identity='book')
            self.assertEqual(model.count, 4)
            self.assertEqual(resets, [])
            self.assertEqual(reset_starts, [])
            path.write_text(path.read_text().replace('玛拉回到北港。', '玛拉回到了北港。'))
            model.load(path, identity='book')
            self.assertEqual(len(resets), 1)
            self.assertEqual(len(reset_starts), 1)
            final = Path(raw) / 'book.txt'
            path.rename(final)
            model.load(final, identity='book')
            self.assertEqual(len(resets), 1)
            self.assertIn('<script>', model.rows[2])
            self.assertTrue(all(len(row) <= 1400 for row in paragraphs('超' * 10000)))

    def test_internal_editions_share_book_progress_and_external_txt_does_not(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); job = root / 'job'; translated = job / '译文'; review = job / '译后校对'
            translated.mkdir(parents=True); review.mkdir()
            original = translated / 'story.zh-CN.txt'
            original.write_text('第一章\n第一段。\n第二段。')
            settings = QSettings(str(root / 's.ini'), QSettings.Format.IniFormat)
            controller = TranslatorController(workspace=root / 'jobs', settings=settings, restore=False)
            controller._job_path = str(job); controller._output_path = str(original)
            controller._refresh_reader()
            managed_identity = controller._reader_identity
            self.assertTrue(managed_identity.startswith('book:'))
            controller.saveReadingPosition(2)

            current = review / 'story.当前阅读版.txt'
            current.write_text('第一章\n第一段已校订。\n第二段。')
            self.assertTrue(controller.openReadingFile(str(current)))
            self.assertEqual(controller._reader_identity, managed_identity)
            self.assertEqual(controller.savedReadingPosition, 2)

            external = root / 'other.txt'; external.write_text('序章\n另一本书。')
            self.assertTrue(controller.openReadingFile(str(external)))
            self.assertTrue(controller._reader_identity.startswith('book:'))
            self.assertNotEqual(controller._reader_identity, managed_identity)
            self.assertEqual(controller.savedReadingPosition, 0)
            controller.reader_timer.stop()

    def test_switching_source_detaches_the_previous_book_from_reader(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            controller = TranslatorController(workspace=root / 'jobs', settings=QSettings(str(root/'s.ini'), QSettings.Format.IniFormat), restore=False)
            first = root / 'first.txt'; first.write_text('First source text. ' * 20)
            second = root / 'second.txt'; second.write_text('Second source text. ' * 20)
            controller._select_source(first)
            old_read = root / 'first.zh-CN.txt'; old_read.write_text('第一章\n上一本文本。')
            self.assertTrue(controller.openReadingFile(str(old_read)))
            self.assertTrue(controller.readingExternal)
            self.assertEqual(controller.reader.rows[-1], '上一本文本。')

            controller._select_source(second)
            self.assertFalse(controller.readingExternal)
            self.assertEqual(controller.reader.count, 0)
            self.assertFalse(controller.settings.contains('last_read_file'))
            self.assertEqual(controller.readerTitle, 'second')

            new_job = Path(controller.jobPath); (new_job / '译文').mkdir(parents=True)
            new_output = new_job / '译文/second.zh-CN.txt.partial'
            new_output.write_text('第一章\n新书首批译文。')
            controller._job_path = str(new_job); controller._output_path = str(new_output)
            controller._refresh_reader()
            self.wait_reader(controller)
            self.assertEqual(controller.reader.rows[-1], '新书首批译文。')
            controller.reader_timer.stop()

    def test_restore_rejects_an_edition_from_another_managed_book(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); workspace = root / 'jobs'
            current_job = workspace / 'current'; other_job = workspace / 'other'
            (current_job / '译后校对').mkdir(parents=True)
            (other_job / '译后校对').mkdir(parents=True)
            current = current_job / '译后校对/current.txt'; current.write_text('当前书。')
            stale = other_job / '译后校对/stale.txt'; stale.write_text('上一书。')
            external = root / 'external.txt'; external.write_text('独立阅读。')
            controller = TranslatorController(workspace=workspace, settings=QSettings(str(root/'s.ini'), QSettings.Format.IniFormat), restore=False)
            controller._job_path = str(current_job)
            self.assertTrue(controller._can_restore_reading(current))
            self.assertFalse(controller._can_restore_reading(stale))
            self.assertTrue(controller._can_restore_reading(external))
            controller.reader_timer.stop()

    def test_fresh_defaults_resume_truth_new_edition_and_source_change(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
            controller = TranslatorController(workspace=root / 'jobs', settings=settings, restore=False)
            self.assertFalse(controller.autoGlossary)
            source = root / 'story.txt'
            source.write_text('Mara went to North Harbor. Iven followed her. ' * 5)
            controller._select_source(source)
            controller.autoGlossary = True
            with patch('native_launcher.ollama_models', return_value={'jingdu-hy-mt2:7b-q4'}):
                project = controller._prepare_job()
            config = yaml.safe_load((project / '翻译任务.yaml').read_text())
            self.assertEqual(config['glossary'], {'mode': 'auto', 'file': '术语表.json', 'strategy': gs.VERSION})
            (project / '.hy-name-review.json').write_text('{}')
            controller._select_source(source)
            self.assertTrue(controller.autoGlossary)
            self.assertTrue(controller.taskLocked)
            controller.autoGlossary = False
            self.assertTrue(controller.autoGlossary)
            controller.newEdition()
            self.assertFalse(controller.autoGlossary)
            self.assertFalse(controller.taskLocked)
            self.assertNotEqual(controller._job_for_source(source), project)
            self.assertTrue((project / '.hy-name-review.json').exists())
            other = root / 'other.txt'; other.write_text('A different story. ' * 10)
            controller.autoGlossary = True
            controller._select_source(other)
            self.assertFalse(controller.autoGlossary)
            controller.reader_timer.stop()

    def test_reader_position_and_running_source_guard(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            controller = TranslatorController(workspace=root / 'jobs', settings=QSettings(str(root/'s.ini'), QSettings.Format.IniFormat), restore=False)
            path = root / 'a.txt'; path.write_text('原文')
            controller._source_path = str(path)
            controller._running = True
            controller._select_source(Path('/nonexistent'))
            self.assertEqual(controller.sourcePath, str(path))
            controller._reader_identity = 'test'
            controller.saveReadingPosition(153)
            self.assertEqual(controller.savedReadingPosition, 153)
            controller._running = False
            controller.reader_timer.stop()

    def test_epub_source_picker_and_drop_do_not_fall_through_to_reader(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            controller = TranslatorController(
                workspace=root / 'jobs',
                settings=QSettings(str(root / 's.ini'), QSettings.Format.IniFormat),
                restore=False,
            )
            epub = root / 'replacement.epub'
            epub.write_bytes(b'picker-routing-fixture')
            column_id = controller.activeReadingColumnId

            with patch('native_launcher.QFileDialog.getOpenFileName', return_value=(str(epub), '')), \
                    patch.object(controller, '_select_source') as select_source, \
                    patch.object(controller, 'openPathInColumn') as open_reader:
                controller.pickSource()
                select_source.assert_called_once_with(epub, reader_async=True)
                open_reader.assert_not_called()

            with patch('native_launcher.QFileDialog.getOpenFileName', return_value=(str(epub), '')), \
                    patch.object(controller, '_select_source') as select_source, \
                    patch.object(controller, 'importShelfBookInColumnAsync') as open_reader:
                controller.pickShelfBook()
                open_reader.assert_called_once_with(column_id, str(epub))
                select_source.assert_not_called()

            with patch('native_launcher.QFileDialog.getOpenFileName', return_value=(str(epub), '')), \
                    patch.object(controller, '_select_source') as select_source, \
                    patch.object(controller, 'importShelfBookInColumnAsync') as open_reader:
                controller.pickReadingFile()
                open_reader.assert_called_once_with(column_id, str(epub))
                select_source.assert_not_called()

            with patch.object(controller, '_select_source') as select_source, \
                    patch.object(controller, 'openPathInColumn') as open_reader:
                controller.acceptDrop(QUrl.fromLocalFile(str(epub)).toString())
                select_source.assert_called_once_with(epub, reader_async=True)
                open_reader.assert_not_called()
            controller.shutdown()

    def test_open_txt_without_api_and_without_changing_translation(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            controller = TranslatorController(workspace=root/'jobs', settings=QSettings(str(root/'s.ini'), QSettings.Format.IniFormat), restore=False)
            controller._source_path = 'current-original.txt'
            controller._job_path = 'current-task'
            controller._running = True
            for encoding in ('utf-8', 'utf-8-sig', 'utf-16', 'gb18030'):
                book = root / (encoding + '.txt')
                data = '第一章\n玛拉回到了北港。'.encode(encoding)
                book.write_bytes(data)
                with patch('native_launcher.auth_ready', return_value=False), patch('native_launcher.QFileDialog.getOpenFileName', return_value=(str(book), '')):
                    controller.pickReadingFile()
                self.wait_reader(controller)
                self.assertEqual(controller.reader.rows, ['第一章', '玛拉回到了北港。'])
                self.assertEqual(controller.sourcePath, 'current-original.txt')
                self.assertEqual(controller.jobPath, 'current-task')
                self.assertTrue(controller.running)
                self.assertEqual(book.read_bytes(), data)
            last_title = controller.readerTitle
            with patch('native_launcher.QFileDialog.getOpenFileName', return_value=('', '')):
                controller.pickReadingFile()
            self.assertEqual(controller.readerTitle, last_title)
            controller._running = False
            controller.reader_timer.stop()


if __name__ == '__main__':
    unittest.main()
