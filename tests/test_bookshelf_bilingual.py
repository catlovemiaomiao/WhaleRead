from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
import unicodedata
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from test_post_edit import pe, make_book, SOURCES, TARGETS
from test_review_gate_annotations import set_translation_extent
import annotations as notes
from native_launcher import TranslatorController
from bookshelf import Bookshelf
from book_identity import (SCHEMA as BOOK_IDENTITY_SCHEMA, canonical_path,
                           legacy_path_id, reader_token, task_identity,
                           text_sha256)
from reader import paragraphs
from storage_context import StorageContext


class LibraryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def owner(self, root, restore=False):
        owner = TranslatorController(workspace=root / 'jobs',
            settings=QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat), restore=restore)
        self.addCleanup(owner.shutdown)
        return owner

    def wait_reader(self, owner, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if not any(column.loading or getattr(column, '_refresh_token', '')
                       for column in owner.readingColumns):
                self.app.processEvents()
                return
            time.sleep(.01)
        self.fail('reader background load did not finish')

    def task(self, root, name, targets=None):
        job = root / 'jobs' / name
        job.mkdir(parents=True)
        make_book(job, targets)
        # These fixtures model different books that happen to reuse compact
        # sample prose. Give each its own imported-asset identity while keeping
        # the extracted text hash truthful.
        config_path = job / '翻译任务.yaml'
        config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
        source = job / '原文/story.txt'
        asset_hash = hashlib.sha256(name.encode('utf-8') + b'\0' + source.read_bytes()).hexdigest()
        config['source']['identity'] = {
            'schema': BOOK_IDENTITY_SCHEMA,
            'sha256': asset_hash,
            'assets_sha256': [asset_hash],
            'text_sha256': text_sha256([source.read_text(encoding='utf-8')]),
        }
        config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
        return job

    def test_two_books_retain_corrected_edition_alignment_and_independent_positions(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            first = self.task(root, 'first')
            second = self.task(root, 'second', [text.replace('伊文', '伊凡') for text in TARGETS])
            owner = self.owner(root)
            owner._select_source(first / '原文/story.txt', project=first)
            first_id = owner.bookshelf[0]['id']
            book = pe.snapshot(first)
            machine = first / '译文/story.zh-CN.txt'
            flag = notes.add(first, machine, book, pe.engine, owner.reader.rows, 0, 0, 2, '马拉', '', '人名')
            revised = TARGETS[0].replace('马拉', '玛拉')
            notes.save_edit(first, flag['id'], revised, book, pe.engine)
            current = notes.publish_edits(first, [flag['id']], book, pe.engine)['output']
            owner.openReadingFile(current)
            owner.readerBilingual = True
            owner.saveReadingPosition(2)
            self.assertEqual(owner.reader.source_rows, SOURCES)
            owner._select_source(second / '原文/story.txt', project=second)
            second_id = owner.bookshelf[1]['id']
            self.assertEqual(owner.reader.rows[1], TARGETS[1].replace('伊文', '伊凡'))
            self.assertEqual(owner.savedReadingPosition, 0)
            owner.saveReadingPosition(1)
            owner.openShelfBook(first_id)
            self.assertEqual(owner.reader.rows[0], revised)
            self.assertEqual(owner.savedReadingPosition, 2)
            self.assertEqual(owner.reader.source_rows, SOURCES)
            owner.openShelfBook(second_id)
            self.assertEqual(owner.savedReadingPosition, 1)
            self.assertEqual(len(owner.bookshelf), 2)

    def test_remove_current_book_survives_restart_without_touching_files(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(); job = self.task(root, 'first')
            owner = self.owner(root, restore=True)
            identity = owner.bookshelf[0]['id']
            owner.openShelfBook(identity)
            def hashes():
                return {str(p.relative_to(job)): hashlib.sha256(p.read_bytes()).hexdigest()
                        for p in job.rglob('*') if p.is_file()}
            before = hashes()
            owner.removeShelfBook(identity)
            self.assertEqual(owner.bookshelf, [])
            self.assertEqual(owner.reader.rows, TARGETS)
            owner.shutdown(); owner.settings.sync()
            restored = self.owner(root, restore=True)
            self.assertEqual(restored.bookshelf, [])
            self.assertEqual(hashes(), before)
            # An explicit selection can put the book back on the shelf.
            restored._select_source(job / '原文/story.txt', project=job)
            self.assertEqual(len(restored.bookshelf), 1)

    def test_bilingual_hot_update_and_external_txt_never_guess_alignment(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(); job = self.task(root, 'first')
            machine = set_translation_extent(job, 2)
            owner = self.owner(root)
            owner._select_source(job / '原文/story.txt', project=job)
            owner.readerBilingual = True
            self.assertEqual(owner.reader.source_rows, SOURCES[:2])
            owner._output_path = str(set_translation_extent(job, 4))
            owner._refresh_reader()
            self.wait_reader(owner)
            self.assertEqual(owner.reader.source_rows, SOURCES)
            external = root / '独立.txt'; external.write_text(TARGETS[0])
            owner.openReadingFile(str(external))
            self.assertFalse(owner.reader.hasSources)
            self.assertIn('独立 TXT', owner.bilingualMessage)
            self.assertEqual(len(owner.bookshelf), 2)
            owner.openShelfBook(owner.bookshelf[0]['id'])
            self.assertEqual(owner.reader.source_rows, SOURCES)
            self.assertFalse(owner._read_file, 'standalone TXT leaked into the task session')

    def test_multiline_translation_keeps_whole_source_attached_to_its_segment(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            targets = list(TARGETS); targets[0] = targets[0].replace('她把', '\n她把')
            make_book(root, targets)
            book = pe.snapshot(root); machine = root / '译文/story.zh-CN.txt'
            rows = paragraphs(machine.read_text())
            sources = notes.bilingual_rows(root, machine, book, pe.engine, rows)
            self.assertEqual(len(sources), 5)
            self.assertEqual(sources[:3], [SOURCES[0], '', SOURCES[1]])

    def test_font_and_bilingual_preferences_survive_restart(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(); owner = self.owner(root)
            family = owner.readerFonts[-1]['family']
            owner.readerFontFamily = family; owner.readerBilingual = True
            owner.settings.sync()
            restored = self.owner(root)
            self.assertEqual(restored.readerFontFamily, family)
            self.assertTrue(restored.readerBilingual)
            owner.readerFontFamily = 'not-installed'
            self.assertEqual(owner.readerFontFamily, family)

    def test_moved_import_still_opens_preserved_task_source_and_translation(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(); job = self.task(root, 'first')
            imported = root / 'import.txt'; imported.write_text('\n\n'.join(SOURCES))
            owner = self.owner(root)
            owner._select_source(imported, project=job)
            identity = owner.bookshelf[0]['id']
            imported.rename(root / 'moved.txt')
            self.assertTrue(owner.bookshelf[0]['available'])
            owner.openShelfBook(identity)
            self.assertEqual(owner.reader.rows, TARGETS)
            self.assertEqual(owner.jobPath, str(job))

    def test_same_file_bytes_keep_one_source_identity_after_rename(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            first = root / '母がいた.txt'
            moved = root / '母か\u3099いた-copy.txt'
            first.write_text('同じ本文です。\n二段目です。', encoding='utf-8')
            moved.write_bytes(first.read_bytes())
            owner = self.owner(root)
            first_id = owner.library.remember(first)
            first_reader = owner.library.rows[0]['reader_identity']
            second_id = owner.library.remember(moved)
            self.assertEqual(first_id, second_id)
            self.assertEqual(len(owner.library.rows), 1)
            self.assertEqual(owner.library.rows[0]['reader_identity'], first_reader)
            self.assertEqual(owner.library.rows[0]['source'], canonical_path(moved))
            self.assertEqual(len(owner.library.rows[0]['locations']), 2)
            moved.write_text('真正修改后的正文。', encoding='utf-8')
            self.assertNotEqual(owner.library.remember(moved), first_id)

    def test_bookshelf_import_uses_matching_translation_task_by_content_hash(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            job = self.task(root, 'translated-book')
            retained = job / '原文/story.txt'
            digest = hashlib.sha256(retained.read_bytes()).hexdigest()
            config_path = job / '翻译任务.yaml'
            config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
            config['source']['identity'] = {
                'schema': BOOK_IDENTITY_SCHEMA,
                'sha256': digest,
                'assets_sha256': [digest],
                'text_sha256': text_sha256([retained.read_text(encoding='utf-8')]),
            }
            config_path.write_text(
                yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
            imported = root / '书架添加的原著.txt'
            imported.write_bytes(retained.read_bytes())

            owner = self.owner(root)
            task_id = owner.library.remember(
                retained, job=str(job), progress=1, enrich=False)
            column = owner._active_column()
            owner.importShelfBookInColumnAsync(column.columnId, str(imported))
            self.wait_reader(owner)

            self.assertEqual(column.bookId, task_id)
            self.assertEqual(column.readingJobPath, str(job))
            self.assertFalse(column.readingExternal)
            self.assertEqual(column.reader.rows, TARGETS)
            self.assertEqual(column.reader.source_rows, SOURCES)
            self.assertEqual(len(owner.bookshelf), 1)
            self.assertEqual(owner.bookshelf[0]['detail'], '译本已完成')

    def test_bookshelf_import_hashes_off_the_gui_thread(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            imported = root / 'large-enough-to-observe.txt'
            imported.write_text('\n\n'.join(SOURCES), encoding='utf-8')
            owner = self.owner(root)
            column = owner._active_column()
            main_thread = threading.get_ident()
            worker_threads = []
            started = threading.Event()
            release = threading.Event()
            from book_identity import file_sha256 as real_file_sha256

            def controlled_hash(path):
                worker_threads.append(threading.get_ident())
                started.set()
                release.wait(3)
                return real_file_sha256(path)

            try:
                with patch('reading_sessions.file_sha256', side_effect=controlled_hash):
                    began = time.monotonic()
                    owner.importShelfBookInColumnAsync(column.columnId, str(imported))
                    self.assertLess(time.monotonic() - began, .2)
                    self.assertTrue(started.wait(1))
                    self.assertTrue(column.loading)
                    self.assertNotEqual(worker_threads, [main_thread])
                    release.set()
                    self.wait_reader(owner)
            finally:
                release.set()

            self.assertTrue(column.bookId.startswith('source:'))
            self.assertEqual(column.reader.rows, SOURCES)

    def test_late_bookshelf_hash_result_cannot_replace_newer_selection(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            first = root / 'first.txt'
            second = root / 'second.txt'
            first.write_text('第一本书。', encoding='utf-8')
            second.write_text('第二本书。', encoding='utf-8')
            owner = self.owner(root)
            column = owner._active_column()
            first_started = threading.Event()
            release_first = threading.Event()
            from book_identity import file_sha256 as real_file_sha256

            def controlled_hash(path):
                if Path(path) == first:
                    first_started.set()
                    release_first.wait(3)
                return real_file_sha256(path)

            try:
                with patch('reading_sessions.file_sha256', side_effect=controlled_hash):
                    owner.importShelfBookInColumnAsync(column.columnId, str(first))
                    self.assertTrue(first_started.wait(1))
                    owner.importShelfBookInColumnAsync(column.columnId, str(second))
                    release_first.set()
                    self.wait_reader(owner)
            finally:
                release_first.set()

            self.assertEqual(column.reader.rows, ['第二本书。'])
            self.assertEqual(Path(column.readingPath), second)
            self.assertEqual(
                [Path(row['source']) for row in owner.library.rows], [second])

    def test_bookshelf_hash_match_prefers_active_translation_edition(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            first = self.task(root, 'edition-one')
            second = self.task(root, 'edition-two')
            retained = first / '原文/story.txt'
            digest = hashlib.sha256(retained.read_bytes()).hexdigest()
            for job in (first, second):
                source = job / '原文/story.txt'
                config_path = job / '翻译任务.yaml'
                config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
                config['source']['identity'] = {
                    'schema': BOOK_IDENTITY_SCHEMA,
                    'sha256': digest,
                    'assets_sha256': [digest],
                    'text_sha256': text_sha256([source.read_text(encoding='utf-8')]),
                }
                config_path.write_text(
                    yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
            imported = root / '同一本原著.txt'
            imported.write_bytes(retained.read_bytes())

            owner = self.owner(root)
            first_id = owner.library.remember(
                first / '原文/story.txt', job=str(first), progress=1, enrich=False)
            owner.library.remember(
                second / '原文/story.txt', job=str(second), progress=1, enrich=False)
            owner._job_path = str(first)
            column = owner._active_column()
            owner.importShelfBookInColumnAsync(column.columnId, str(imported))
            self.wait_reader(owner)

            self.assertEqual(column.bookId, first_id)
            self.assertEqual(column.readingJobPath, str(first))
            self.assertEqual(owner.bookshelf[0]['id'], first_id)

    def test_bookshelf_import_never_binds_same_title_with_different_bytes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            job = self.task(root, 'same-title')
            retained = job / '原文/story.txt'
            digest = hashlib.sha256(retained.read_bytes()).hexdigest()
            config_path = job / '翻译任务.yaml'
            config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
            config['source']['identity'] = {
                'schema': BOOK_IDENTITY_SCHEMA,
                'sha256': digest,
                'assets_sha256': [digest],
                'text_sha256': text_sha256([retained.read_text(encoding='utf-8')]),
            }
            config_path.write_text(
                yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
            imported = root / 'story.txt'
            imported.write_text('标题相同，但这是内容不同的另一本书。', encoding='utf-8')

            owner = self.owner(root)
            owner.library.remember(
                retained, job=str(job), progress=1, enrich=False)
            column = owner._active_column()
            owner.importShelfBookInColumnAsync(column.columnId, str(imported))
            self.wait_reader(owner)

            self.assertTrue(column.bookId.startswith('source:'))
            self.assertFalse(column.readingJobPath)
            self.assertTrue(column.readingExternal)
            self.assertEqual(column.reader.rows, ['标题相同，但这是内容不同的另一本书。'])

    def test_source_picker_finds_existing_task_from_the_same_full_hash(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            job = self.task(root, 'existing-task')
            retained = job / '原文/story.txt'
            digest = hashlib.sha256(retained.read_bytes()).hexdigest()
            config_path = job / '翻译任务.yaml'
            config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
            config['source']['identity'] = {
                'schema': BOOK_IDENTITY_SCHEMA,
                'sha256': digest,
                'assets_sha256': [digest],
                'text_sha256': text_sha256([retained.read_text(encoding='utf-8')]),
            }
            config_path.write_text(
                yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
            selected = root / '改名后的原著.txt'
            selected.write_bytes(retained.read_bytes())

            owner = self.owner(root)
            task_id = owner.library.remember(
                retained, job=str(job), progress=1, enrich=False)
            owner.settings.remove('editions/' + digest[:10])
            owner._select_source(selected, inspect=False)

            self.assertEqual(owner.jobPath, str(job))
            self.assertEqual(owner._active_book, task_id)

    def test_two_translation_tasks_share_book_state_but_keep_distinct_editions(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            first = self.task(root, 'edition-one')
            second = self.task(root, 'edition-two')
            first_config = yaml.safe_load((first / '翻译任务.yaml').read_text(encoding='utf-8'))
            second_config = yaml.safe_load((second / '翻译任务.yaml').read_text(encoding='utf-8'))
            second_config['source']['identity'] = dict(first_config['source']['identity'])
            (second / '翻译任务.yaml').write_text(
                yaml.safe_dump(second_config, allow_unicode=True), encoding='utf-8')
            owner = self.owner(root)
            first_id = owner.library.remember(first / '原文/story.txt', job=str(first))
            second_id = owner.library.remember(second / '原文/story.txt', job=str(second))
            self.assertNotEqual(first_id, second_id)
            rows = [row for row in owner.library.rows if row['id'] in {first_id, second_id}]
            self.assertEqual(len(rows), 2)
            self.assertEqual(len({row['reader_identity'] for row in rows}), 1)
            self.assertEqual(len(owner.bookshelf), 1)
            self.assertTrue((first / '.whaleread-identity.json').is_file())
            self.assertTrue((second / '.whaleread-identity.json').is_file())

    def test_task_identity_survives_unicode_spelling_and_folder_move(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            job = self.task(root, '温泉へ行ったら母がいた')
            source = job / '原文/story.txt'
            owner = self.owner(root)
            identity = owner.library.remember(source, job=str(job))
            nfd_job = unicodedata.normalize('NFD', str(job))
            nfd_source = unicodedata.normalize('NFD', str(source))
            self.assertEqual(owner.library.key('task', nfd_job, nfd_source), identity)

            moved = job.with_name('renamed-task')
            job.rename(moved)
            moved_source = moved / '原文/story.txt'
            self.assertEqual(owner.library.key('task', moved, moved_source), identity)
            marker = json.loads((moved / '.whaleread-identity.json').read_text())
            self.assertEqual(identity.rsplit(':', 1)[-1], marker['task_id'])

    def test_prestart_task_id_is_replaced_by_persisted_id_without_duplicate_row(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            source = root / 'book.txt'
            source.write_text('这是一本尚未开始翻译的书。')
            future_job = root / 'jobs/book-task'
            owner = self.owner(root)
            provisional = owner.library.remember(source, job=str(future_job))
            digest = owner.library.rows[0]['book_hash']
            future_job.mkdir(parents=True)
            persisted = task_identity(future_job, digest)
            final = owner.library.remember(source, job=str(future_job))
            self.assertNotEqual(provisional, final)
            self.assertEqual(final, f'task:{digest}:{persisted}')
            self.assertEqual(len(owner.library.rows), 1)
            self.assertEqual(owner.library.resolve_id(provisional), final)

    def test_path_era_library_and_notes_migrate_without_overwriting_rollback_keys(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            job = self.task(root, 'legacy-book')
            source = job / '原文/story.txt'
            settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
            old_id = legacy_path_id('task', job)
            old_reader = str(job)
            old_token = reader_token(old_reader)
            legacy_rows = [{'id': old_id, 'kind': 'task', 'title': 'Legacy',
                            'source': str(source), 'job': str(job), 'progress': 1}]
            settings.setValue('library/books_v1', json.dumps(legacy_rows, ensure_ascii=False))
            settings.setValue('library/imported_existing_v1', True)
            settings.setValue('reading/' + old_token, 3)
            settings.setValue('reader/mode_book/' + old_token, 'bilingual')
            settings.setValue('reader/bookmarks_v1/' + old_token,
                              json.dumps([{'id': 'bookmark', 'segment': 2}]))
            settings.setValue('reader/paragraph_notes_v1/' + old_token,
                              json.dumps([{'id': 'note', 'document_id': old_token,
                                           'anchor': {'kind': 'source-segment', 'segment': 2}}]))
            settings.setValue('reader/columns_v2', json.dumps([
                {**legacy_rows[0], 'column_id': 'legacy-column',
                 'reading_mode': 'bilingual'}], ensure_ascii=False))
            settings.setValue('reader/active_column_v2', 'legacy-column')
            settings.sync()

            storage = StorageContext.isolated(
                root / 'migration-storage', purpose='qa',
                settings_scope=str(Path(settings.fileName()).resolve()))
            # Constructing the GUI-side index must never touch a protected
            # source file before the first app window can be shown.
            with patch.object(Bookshelf, 'content_hash',
                              side_effect=AssertionError('startup performed source I/O')):
                library = Bookshelf(settings, storage)
            self.assertEqual(library.rows[0]['id'], old_id)
            self.assertFalse((job / '.whaleread-identity.json').exists())
            mapping = library.promote_all()
            self.assertTrue(mapping[old_id].startswith('task:'))
            self.assertTrue((job / '.whaleread-identity.json').is_file())

            owner = self.owner(root, restore=True)
            self.wait_reader(owner)
            column = owner.readingColumns[0]
            new_token = reader_token(column._reader_identity)
            self.assertTrue(column.bookId.startswith('task:'))
            self.assertTrue(column._reader_identity.startswith('book:'))
            self.assertEqual(column.savedReadingPosition, 3)
            self.assertEqual(column.readingMode, 'bilingual')
            self.assertEqual(column.bookmarks[0]['id'], 'bookmark')
            self.assertEqual(column.paragraphNotes[0]['document_id'], new_token)
            self.assertEqual(json.loads(settings.value('library/books_v1')), legacy_rows)
            self.assertTrue(settings.contains('library/books_v2'))
            self.assertTrue(settings.contains('reader/columns_v4'))
