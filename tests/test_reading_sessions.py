from __future__ import annotations

import hashlib
import json
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import yaml
import test_bookshelf_bilingual as library_tests
from test_post_edit import pe, SOURCES, TARGETS
from test_review_gate_annotations import set_translation_extent
import annotations as notes
import shiboken6 as sip


class ReadingSessionTests(unittest.TestCase):
    owner = library_tests.LibraryTests.owner
    task = library_tests.LibraryTests.task

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def wait_reader(self, owner, timeout=4):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            busy = any(column.loading or getattr(column, '_refresh_token', '')
                       for column in owner.readingColumns)
            if not busy:
                self.app.processEvents()
                return
            time.sleep(.01)
        self.fail('reader background load did not finish')

    def test_epub_refresh_holds_visible_document_until_navigation(self):
        from epub_session import EpubSession
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            owner = self.owner(root)
            column = owner._ensure_column()
            session = column.epub
            path = root / 'book.epub'
            path.write_bytes(b'one')
            old = dict(root=str(root / 'cache-one'), digest='one', source_hash='one',
                       chapters=[dict(path='c.xhtml', title='旧目录')])
            new = dict(root=str(root / 'cache-two'), digest='two', source_hash='two',
                       chapters=[dict(path='c.xhtml', title='新目录')])
            for cached in (old, new):
                Path(cached['root']).mkdir()
                (Path(cached['root']) / 'c.xhtml').write_text('<html><body><p>Book</p></body></html>')
            with patch('epub_session.open_epub', return_value=old):
                session.open(path)
            session.savePosition(.42)
            before = session.state['url']
            path.write_bytes(b'new checkpoint')
            with patch('epub_session.open_epub', return_value=new):
                session.open(path)
            self.assertEqual(session.state['url'], before)
            self.assertEqual(session.fraction, .42)
            self.assertTrue(session.state['updateAvailable'])
            session.navigate(0, .1)
            self.assertNotEqual(session.state['url'], before)
            self.assertEqual(session.fraction, .1)
            self.assertFalse(session.state['updateAvailable'])
            session.close()

    def test_managed_epub_progress_moves_to_content_identity_without_erasing_legacy(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            owner = self.owner(root)
            session = owner._ensure_column().epub
            job = root / 'jobs/book'
            path = job / '译文/book.zh-CN.epub'
            path.parent.mkdir(parents=True)
            path.write_bytes(b'epub')
            (job / '.epub-source.json').write_text(json.dumps({
                'source': '原文/book.epub', 'sha256': 'a' * 64,
            }))
            cache = root / 'cache'
            cache.mkdir()
            for name in ('one.xhtml', 'two.xhtml'):
                (cache / name).write_text('<html><body>book</body></html>')
            old_root = 'epub/' + hashlib.sha256(str(path).encode()).hexdigest() + '/'
            marks = [{'chapter': 1, 'fraction': .37, 'title': '第二章 · 37%'}]
            owner.settings.setValue(old_root + 'chapter', 1)
            owner.settings.setValue(old_root + 'fraction', .37)
            owner.settings.setValue(old_root + 'marks', json.dumps(marks, ensure_ascii=False))
            prepared = {
                'path': str(path),
                'signature': (str(path), 1),
                'source_hash': 'b' * 64,
                'translation_source_hash': 'a' * 64,
                'logical_identity': 'book:' + 'a' * 64,
                'edition_identity': 'translation',
                'book': {
                    'root': str(cache), 'digest': 'digest', 'source_hash': 'b' * 64,
                    'chapters': [
                        {'path': 'one.xhtml', 'title': '第一章'},
                        {'path': 'two.xhtml', 'title': '第二章'},
                    ],
                },
            }
            self.assertTrue(session.accept_prepared(prepared, defer_same_path=False))
            self.assertEqual((session.chapter, session.fraction), (1, .37))
            self.assertEqual(session.state['marks'], marks)
            new_root = session.key('chapter').removesuffix('chapter')
            self.assertNotEqual(new_root, old_root)
            self.assertTrue(owner.settings.contains(new_root + 'chapter'))
            self.assertTrue(owner.settings.contains(old_root + 'chapter'))
            session.close()

    def test_epub_session_keeps_other_books_warm_but_discards_superseded_generation(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            owner = self.owner(root)
            session = owner._ensure_column().epub

            def prepared(path, cache_root, signature):
                cache_root.mkdir(parents=True)
                (cache_root / 'c.xhtml').write_text('<html><body>cache</body></html>')
                return dict(path=str(path), signature=(str(path), signature),
                            source_hash=str(signature), translation_source_hash='',
                            book=dict(root=str(cache_root), digest=str(signature),
                                      source_hash=str(signature),
                                      chapters=[dict(path='c.xhtml', title='chapter')]))

            first_root = root / 'cache/book-first/edition-original/generation-one'
            second_root = root / 'cache/book-second/edition-original/generation-one'
            second_new = root / 'cache/book-second/edition-original/generation-two'
            first = prepared(root / 'first.epub', first_root, 1)
            second = prepared(root / 'second.epub', second_root, 2)
            updated = prepared(root / 'second.epub', second_new, 3)

            session.accept_prepared(first, defer_same_path=False)
            session.accept_prepared(second, defer_same_path=False)
            session.releasePreviousCache()
            self.assertTrue(first_root.is_dir(), 'another book must keep its latest warm cache')

            session.accept_prepared(updated, defer_same_path=False)
            session.releasePreviousCache()
            self.assertFalse(second_root.exists(), 'same-edition old generation must be reclaimed')
            self.assertTrue(second_new.is_dir())
            session.close()
            self.assertTrue(second_new.is_dir(), 'closing keeps the latest warm cache')

    def test_prepared_epub_is_leased_before_gui_accepts_it(self):
        from PySide6.QtCore import QUrl
        from epub_session import prepare_epub
        from epub_reader import clear_epub_cache
        from test_epub_reader import write_epub
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            owner = self.owner(root)
            source = root / 'book.epub'
            write_epub(source)
            prepared = prepare_epub(
                source, cache_identity='task-one', edition_identity='original',
                storage_context=owner.storage_context)
            cache_root = Path(prepared['book']['root'])
            clear_epub_cache(owner.storage_context.cache_root)
            self.assertTrue(cache_root.is_dir(), 'queued GUI payload must hold a lease')
            session = owner._ensure_column().epub
            self.assertTrue(session.accept_prepared(prepared, defer_same_path=False))
            self.assertTrue(Path(QUrl(session.state['url']).toLocalFile()).is_file())
            session.close()
            clear_epub_cache(owner.storage_context.cache_root)
            self.assertFalse(cache_root.exists())

    def test_reviewed_epub_hot_update_preserves_package_and_keeps_text_ledger(self):
        from epub_session import release_prepared
        from epub_translation import attach, export
        from reader import paragraphs
        from reader_loader import epub_work_signature, is_epub_review_text, prepare_book
        from storage_context import StorageContext
        from task_config import extract_epub

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            job = self.task(root, 'reviewed-epub')
            source = root / 'source.epub'
            body = ''.join(f'<p>{text}</p>' for text in SOURCES)
            with zipfile.ZipFile(source, 'w') as archive:
                archive.writestr('mimetype', 'application/epub+zip')
                archive.writestr(
                    'META-INF/container.xml',
                    '<container><rootfiles><rootfile full-path="content.opf"/>'
                    '</rootfiles></container>',
                )
                archive.writestr(
                    'content.opf',
                    '<package><metadata><title>Fixture</title></metadata><manifest>'
                    '<item id="chapter" href="chapter.xhtml" media-type="application/xhtml+xml"/>'
                    '<item id="style" href="style.css" media-type="text/css"/>'
                    '<item id="image" href="cover.png" media-type="image/png"/>'
                    '</manifest><spine><itemref idref="chapter"/></spine></package>',
                )
                archive.writestr(
                    'chapter.xhtml',
                    '<html><head><title></title><link rel="stylesheet" href="style.css"/>'
                    f'</head><body><img src="cover.png"/>{body}</body></html>',
                )
                archive.writestr('style.css', 'p { line-height: 1.8; }')
                archive.writestr('cover.png', b'untouched review image')

            attach(job, source)
            book = pe.snapshot(job)
            machine_text = job / '译文/story.zh-CN.txt'
            machine_epub = job / '译文/story.zh-CN.epub'
            export(job, machine_epub, book['segments'], book['translated'],
                   language_name='简体中文')
            source_before = source.read_bytes()
            machine_before = machine_epub.read_bytes()

            rows = paragraphs(machine_text.read_text(encoding='utf-8'))
            flag = notes.add(
                job, machine_text, book, pe.engine, rows,
                0, 0, 2, '马拉', '', '人名')
            revised = TARGETS[0].replace('马拉', '玛拉')
            notes.save_edit(job, flag['id'], revised, book, pe.engine)
            current = Path(notes.publish_edits(
                job, [flag['id']], book, pe.engine)['output'])
            current_before = current.read_bytes()

            descriptor = {
                'id': 'task:reviewed-epub', 'kind': 'task', 'title': 'Fixture',
                'source': str(source), 'job': str(job),
                'reader_identity': 'book:reviewed-epub',
            }
            unrelated = job / '译后校对/notes.txt'
            unrelated.write_text(current.read_text(encoding='utf-8'), encoding='utf-8')
            self.assertFalse(is_epub_review_text(descriptor, unrelated))
            storage = StorageContext.isolated(root / 'reader-storage')
            payload = prepare_book(
                Path(__file__).resolve().parents[1], descriptor, str(current),
                str(machine_text), storage)
            try:
                self.assertEqual(payload['kind'], 'epub')
                self.assertTrue(payload['translated'])
                companion = current.with_suffix('.epub')
                self.assertEqual(Path(payload['reading_path']), companion)
                self.assertTrue(companion.is_file())
                with zipfile.ZipFile(companion) as archive:
                    self.assertEqual(archive.read('cover.png'), b'untouched review image')
                    self.assertEqual(archive.read('style.css'), b'p { line-height: 1.8; }')
                    chapter = archive.read('chapter.xhtml').decode('utf-8')
                    self.assertIn('<img src="cover.png"', chapter)
                rendered = extract_epub(companion)
                self.assertIn(revised, rendered)
                self.assertNotIn(TARGETS[0], rendered)
                self.assertEqual(source.read_bytes(), source_before)
                self.assertEqual(machine_epub.read_bytes(), machine_before)
                self.assertEqual(current.read_bytes(), current_before)
                signature = epub_work_signature(
                    descriptor, str(current), str(machine_text))
                self.assertEqual(signature[0], 'reviewed-epub')
                self.assertIsNotNone(signature[2])
            finally:
                release_prepared(payload.get('prepared'))

            # The post-edit action starts with only the verified TXT ledger.
            # Its regular UI entry point must rebuild the derived EPUB off the
            # GUI thread instead of silently reopening the TXT renderer.
            companion.unlink()
            owner = self.owner(root)
            owner.settings.setValue('reader/groups_v3_migrated', True)
            owner._job_path = str(job)
            owner._source_path = str(source)
            owner._output_path = str(machine_text)
            owner.library.remember(
                source, job=str(job), reading_file=str(current), progress=1,
                enrich=False)
            self.assertTrue(owner.openReadingFile(str(current)))
            self.wait_reader(owner)
            column = owner._active_column()
            self.assertEqual(column._read_file, str(current))
            self.assertTrue(column.epubActive)
            self.assertEqual(Path(column.epub.path), current.with_suffix('.epub'))
            self.assertEqual(column.reader.count, 0)
            self.assertTrue(column.setReadingMode('original'))
            deadline = time.monotonic() + 4
            while (column.epubPeer is None or column.epubPeer.loading) and time.monotonic() < deadline:
                self.app.processEvents()
                time.sleep(.01)
            self.app.processEvents()
            self.assertIsNotNone(column.epubPeer)
            self.assertTrue(column.epubPeer.epubActive)
            self.assertEqual(Path(column.epubPeer.epub.path).parent, job / '原文')
            self.assertEqual(column.readingMode, 'original')
            self.assertTrue(column.setReadingMode('bilingual'))
            self.assertEqual(column.readingMode, 'bilingual')
            self.assertTrue(column.setReadingMode('translated'))
            owner.shutdown()
            owner.settings.sync()

            restored = self.owner(root, restore=True)
            self.wait_reader(restored)
            restored_column = restored._active_column()
            self.assertEqual(restored_column._read_file, str(current))
            self.assertTrue(restored_column.epubActive)
            self.assertEqual(
                Path(restored_column.epub.path), current.with_suffix('.epub'))
            self.assertEqual(restored_column.reader.count, 0)

    def test_async_shelf_switch_keeps_old_book_visible_until_worker_finishes(self):
        import reading_sessions as sessions
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            first, second = self.task(root, 'first'), self.task(root, 'second')
            owner = self.owner(root)
            owner._select_source(first / '原文/story.txt', project=first)
            column = owner._active_column()
            old_identity = column.bookId
            new_identity = owner.library.remember(
                second / '原文/story.txt', job=str(second))
            real_prepare = sessions.prepare_book
            started, release = threading.Event(), threading.Event()

            def delayed(*args):
                started.set()
                release.wait(3)
                return real_prepare(*args)

            try:
                with patch('reading_sessions.prepare_book', side_effect=delayed):
                    before = time.perf_counter()
                    owner.openShelfBookAsync(new_identity)
                    elapsed = time.perf_counter() - before
                    self.assertLess(elapsed, .15)
                    self.assertTrue(column.loading)
                    self.assertEqual(column.bookId, old_identity)
                    self.assertTrue(started.wait(1))
                    release.set()
                    deadline = time.monotonic() + 4
                    while column.loading and time.monotonic() < deadline:
                        self.app.processEvents()
                        time.sleep(.01)
                    self.assertFalse(column.loading)
                    self.assertEqual(column.bookId, new_identity)
                    self.assertEqual(column.reader.rows, TARGETS)
            finally:
                release.set()
                owner.shutdown()

    def test_epub_loading_signal_does_not_rebuild_annotation_alignment(self):
        """QML property reads during an EPUB switch must stay snapshot-free."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            job = self.task(root, 'epub-active')
            owner = self.owner(root)
            try:
                owner._select_source(job / '原文/story.txt', project=job)
                column = owner._active_column()
                epub = root / 'translated.epub'
                epub.write_bytes(b'isolated epub fixture')
                column._epub_translation_path = str(epub)
                column._alignment_signature = None
                # EPUB rendering owns the visible rows; the flow reader is empty.
                column.reader.clear()
                bookmark = {'id': 'bookmark', 'segment': 2, 'offset': 0,
                            'row': 1, 'text': TARGETS[1]}
                note = {'id': 'note', 'document_id': column._document_id(),
                        'anchor': {'kind': 'source-segment', 'segment': 2,
                                   'offset': 0, 'chapter': 'chapter.xhtml', 'block': 1},
                        'content': 'keep'}
                owner.settings.setValue(column._key('reader/bookmarks_v1/'),
                                        json.dumps([bookmark], ensure_ascii=False))
                owner.settings.setValue(column._note_key(),
                                        json.dumps([note], ensure_ascii=False))
                owner.settings.sync()
                observed = []
                column.changed.connect(
                    lambda: observed.append((column.bookmarks, column.paragraphNotes)))
                with patch.object(
                        pe, 'snapshot',
                        side_effect=AssertionError('EPUB property read rebuilt snapshot')):
                    column.begin_loading('switch-token', 'translated')
                self.assertTrue(observed)
                bookmarks, notes = observed[-1]
                self.assertEqual(bookmarks[0]['id'], 'bookmark')
                self.assertEqual(bookmarks[0]['position'], -1)
                self.assertEqual(notes[0]['id'], 'note')
                self.assertEqual(notes[0]['position'], -1)
                self.assertEqual(
                    column.paragraphNoteForEpubAnchor(note['anchor'])['id'], 'note')
                self.assertEqual(column.savedReadingPosition, 0)
            finally:
                owner.shutdown()

    def test_async_restore_does_not_overwrite_saved_position_with_empty_viewport(self):
        from reader_loader import prepare_book
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            job = self.task(root, 'first')
            owner = self.owner(root)
            owner._select_source(job / '原文/story.txt', project=job)
            column = owner._active_column()
            column.saveReadingPosition(2)
            descriptor = dict(column._book)
            reading_file = owner._reading_file_for_row(descriptor)
            payload = prepare_book(
                owner.root, descriptor, reading_file,
                owner._machine_path_for_book(descriptor), owner.storage_context)
            overwrites = []

            # ReaderPane.qml saves visibleRow from this signal. During startup
            # that row is still zero until the background payload is accepted.
            column.readingAboutToChange.connect(
                lambda: (overwrites.append(True), column.saveReadingPosition(0)))
            column.begin_loading('restore-token', descriptor['title'])
            self.assertTrue(column.accept_prepared(
                descriptor, reading_file, payload, 'restore-token'))
            self.assertEqual(overwrites, [])
            self.assertEqual(column.savedReadingPosition, 2)

    def test_restored_descriptor_never_reads_task_config_on_gui_thread(self):
        from reading_sessions import ReaderColumn
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            first = self.task(root, 'first')
            second = self.task(root, 'second')
            owner = self.owner(root)
            owner._select_source(first / '原文/story.txt', project=first)
            identity = owner.library.remember(second / '原文/story.txt', job=str(second))
            descriptor = next(dict(row) for row in owner.library.rows
                              if row['id'] == identity)
            column = ReaderColumn(owner, descriptor, autoload=False)
            with (patch.object(Path, 'read_text',
                               side_effect=AssertionError('GUI property performed source I/O')),
                  patch.object(Path, 'resolve',
                               side_effect=AssertionError('GUI property traversed source path'))):
                self.assertEqual(column.readingMachinePath, '')
                self.assertEqual(column.serialize()['machine_path'], '')

    def test_async_source_selection_defers_reader_and_text_inspection(self):
        import reading_sessions as sessions
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            first, second = self.task(root, 'first'), self.task(root, 'second')
            owner = self.owner(root)
            owner._select_source(first / '原文/story.txt', project=first)
            column = owner._active_column()
            old_identity = column.bookId
            real_prepare = sessions.prepare_book
            started, release = threading.Event(), threading.Event()

            def delayed(*args):
                started.set()
                release.wait(3)
                return real_prepare(*args)

            try:
                with patch('reading_sessions.prepare_book', side_effect=delayed):
                    before = time.perf_counter()
                    owner._select_source(
                        second / '原文/story.txt', project=second,
                        reader_async=True)
                    self.assertLess(time.perf_counter() - before, .15)
                    self.assertTrue(column.loading)
                    self.assertEqual(column.bookId, old_identity)
                    self.assertIn('自动识别语言', owner.sourceInfo)
                    self.assertTrue(started.wait(1))
                    release.set()
                    deadline = time.monotonic() + 4
                    while (column.loading or '字符' not in owner.sourceInfo) and time.monotonic() < deadline:
                        self.app.processEvents()
                        time.sleep(.01)
                    self.assertFalse(column.loading)
                    self.assertNotEqual(column.bookId, old_identity)
                    self.assertIn('字符', owner.sourceInfo)
            finally:
                release.set()
                owner.shutdown()

    def test_cache_settings_tasks_are_async_and_report_clear_result(self):
        with tempfile.TemporaryDirectory() as raw:
            owner = self.owner(Path(raw).resolve())

            def wait_for_idle():
                deadline = time.monotonic() + 3
                while owner.readerCacheBusy and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(.01)
                self.assertFalse(owner.readerCacheBusy)

            try:
                with patch('reading_sessions.cache_stats', return_value={
                        'bytes': 2048, 'files': 2, 'generations': 1, 'books': 1}):
                    owner.refreshReaderCacheStats()
                    self.assertTrue(owner.readerCacheBusy)
                    wait_for_idle()
                    self.assertIn('2.0 KB', owner.readerCacheSummary)
                with patch('reading_sessions.clear_epub_cache', return_value={
                        'bytes': 128, 'files': 1, 'generations': 1, 'books': 1,
                        'freed': 1920, 'remaining': 128}):
                    owner.clearReaderCache()
                    self.assertTrue(owner.readerCacheBusy)
                    wait_for_idle()
                    self.assertIn('已清理 1.9 KB', owner.readerCacheSummary)
                    self.assertIn('当前阅读保留 128 B', owner.readerCacheSummary)
            finally:
                owner.shutdown()

    def test_epub_quote_maps_to_verified_txt_and_rejects_wrong_source(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            job = self.task(root, 'book')
            owner = self.owner(root)
            owner._select_source(job / '原文/story.txt', project=job)
            result = owner.post_editor.locateEpubQuote(SOURCES[0], TARGETS[0][:3], '1')
            self.assertEqual(result['row'], 0)
            self.assertTrue(owner.post_editor.addAnnotation(result['row'], result['start'], result['end'], result['quote'], 'EPUB 测试', '疑似误译'))
            self.assertEqual(len(owner.post_editor.epubMarks(str(job))), 1)
            self.assertEqual(owner.post_editor.locateEpubQuote('wrong source', TARGETS[0][:3], '1'), {})
            self.assertEqual(owner.post_editor.locateEpubQuote(SOURCES[0], '不存在的文字', '1'), {})

    def test_epub_quote_uses_verified_rows_when_txt_model_is_not_rendered(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            job = self.task(root, 'book')
            owner = self.owner(root)
            try:
                owner._select_source(job / '原文/story.txt', project=job)
                column = owner._active_column()
                column._epub_translation_path = str(job / '译文/story.zh-CN.epub')
                column.reader.clear()
                owner._sync_owner_from_column(column)

                result = owner.post_editor.locateEpubQuote(
                    SOURCES[0], TARGETS[0][:3], '1')
                self.assertEqual(result['row'], 0)
                self.assertTrue(owner.post_editor.addAnnotation(
                    result['row'], result['start'], result['end'], result['quote'],
                    '真实 EPUB 映射', '疑似误译'))
                self.assertEqual(len(owner.post_editor.epubMarks(str(job))), 1)
            finally:
                owner.shutdown()

    def test_original_comparison_never_becomes_task_reading_default(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            job = self.task(root, 'book')
            owner = self.owner(root)
            source = job / '原文/story.txt'
            owner._select_source(source, project=job)
            task_id = owner._active_column().bookId
            other = owner.addReadingColumn()
            owner.openPathInColumn(other, str(source))
            self.assertEqual(owner._active_column().readingJobPath, '')
            owner._save_book_session()
            row = next(r for r in owner.library.rows if r['id'] == task_id)
            self.assertFalse(row.get('reading_file'))
            # Migrate a shelf preference contaminated by older versions.
            row['reading_file'] = str(source)
            owner.openShelfBook(task_id)
            self.assertEqual(owner._active_column().readingJobPath, str(job))
            self.assertNotEqual(owner._active_column().readingPath, str(source))
            self.assertEqual(owner._active_column().reader.rows, TARGETS)

    def test_read_current_translation_recovers_removed_entry_without_stopping_task(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            job = self.task(root, 'book')
            owner = self.owner(root)
            source = job / '原文/story.txt'
            owner._select_source(source, project=job)
            task_id = owner._active_column().bookId
            owner.removeShelfBook(task_id)
            owner.openPathInColumn(owner._active_column().columnId, str(source))
            count = len(owner.readingColumns)
            owner._running = True
            owner.followTranslation()
            self.assertEqual(owner._active_column().bookId, task_id)
            self.assertEqual(owner._active_column().reader.rows, TARGETS)
            self.assertEqual(len(owner.readingColumns), count)
            self.assertTrue(owner._running)
            owner._running = False

    def test_legacy_original_alias_is_hidden_and_removed_with_book_without_deleting_files(self):
        import json
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            job = self.task(root, 'book')
            source = job / '原文/story.txt'
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            config_path = job / '翻译任务.yaml'
            config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
            config['source']['identity']['sha256'] = digest
            config['source']['identity']['assets_sha256'] = [digest]
            config_path.write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
            (job / '.epub-source.json').write_text(json.dumps(
                {'source': '原文/story.txt', 'sha256': digest}))
            owner = self.owner(root)
            original_id = owner.library.remember(source)
            task_id = owner.library.remember(source, job=str(job))
            original_bytes = source.read_bytes()
            checkpoint = (job / '译文/.hy-direct-state.json').read_bytes()
            self.assertEqual([r['id'] for r in owner.bookshelf], [task_id])
            self.assertEqual(len(owner.library.rows), 2, 'Display grouping must not erase legacy index records')
            owner.removeShelfBook(task_id)
            self.assertEqual(owner.bookshelf, [])
            self.assertIn(original_id, owner.library.removed)
            self.assertEqual(source.read_bytes(), original_bytes)
            self.assertEqual((job / '译文/.hy-direct-state.json').read_bytes(), checkpoint)

    def test_epub_side_preference_only_moves_visual_positions_and_persists(self):
        with tempfile.TemporaryDirectory() as raw:
            owner = self.owner(Path(raw).resolve())
            original = owner._ensure_column()
            owner.addReadingColumn()
            translated = owner.readingColumns[1]
            original._epub_translation_path = ''
            translated._epub_translation_path = 'translated.epub'
            identities = [c.columnId for c in owner.readingColumns]
            original.epub.fraction, translated.epub.fraction = .24, .68
            with patch.object(owner, '_epub_pair', return_value=[original, translated]):
                self.assertTrue(owner.epubOriginalOnLeft)
                self.assertEqual(owner.epubColumnOrder, {identities[0]: 0, identities[1]: 1})
                owner.swapEpubSides()
                self.assertFalse(owner.epubOriginalOnLeft)
                self.assertEqual(owner.epubColumnOrder, {identities[0]: 0, identities[1]: 1})
                self.assertEqual([c.columnId for c in owner.readingColumns], identities)
                self.assertEqual((original.epub.fraction, translated.epub.fraction), (.24, .68))
                from PySide6.QtCore import QSettings
                saved = QSettings(owner.settings.fileName(), QSettings.Format.IniFormat)
                self.assertFalse(saved.value('reader/epub_original_on_left', True, type=bool))
                owner.swapEpubSides()
                self.assertTrue(owner.epubOriginalOnLeft)

    def test_reading_mode_and_side_order_are_shared_by_txt_and_epub(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(); job = self.task(root, 'book')
            owner = self.owner(root); owner._select_source(job / '原文/story.txt', project=job)
            column = owner._active_column()
            self.assertEqual(column.readingMode, 'translated')
            self.assertTrue(column.setReadingMode('bilingual'))
            self.assertTrue(column.reader.hasSources)
            self.assertTrue(column.readerBilingual)
            self.assertTrue(owner.readingOriginalOnLeft)
            owner.swapReadingSides()
            self.assertFalse(owner.readingOriginalOnLeft)
            self.assertFalse(owner.epubOriginalOnLeft)
            self.assertTrue(column.setReadingMode('original'))
            owner.settings.sync()
            restored = self.owner(root, restore=True)
            self.wait_reader(restored)
            restored_column = restored._active_column()
            self.assertEqual(restored_column.readingMode, 'original')
            self.assertTrue(restored_column.reader.hasSources)
            self.assertFalse(restored.readingOriginalOnLeft)

    def test_paragraph_notes_follow_source_anchor_and_never_cross_books(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(); first = self.task(root, 'first'); second = self.task(root, 'second')
            owner = self.owner(root); owner._select_source(first / '原文/story.txt', project=first)
            first_id = owner._active_column().bookId
            note_id = owner._active_column().saveParagraphNote(2, 'rose', '后文需要回看')
            note = owner._active_column().paragraphNoteAt(2)
            self.assertEqual(note['id'], note_id)
            self.assertEqual(note['anchor']['segment'], 3)
            self.assertEqual(note['content'], '后文需要回看')

            # Reflowing an earlier translated segment must not detach a note
            # from its source segment.
            book = pe.snapshot(first); machine = first / '译文/story.zh-CN.txt'
            flag = notes.add(first, machine, book, pe.engine, owner.reader.rows, 0, 0, 2, '马拉', '', '人名')
            notes.save_edit(first, flag['id'], TARGETS[0].replace('她把', '\n她把'), book, pe.engine)
            current = notes.publish_edits(first, [flag['id']], book, pe.engine)['output']
            owner.openReadingFile(current)
            self.assertEqual(owner._active_column().paragraphNoteById(note_id)['content'], '后文需要回看')
            self.assertEqual(owner._active_column().paragraphNotes[0]['position'], 3)

            second_id = owner.library.remember(second / '原文/story.txt', job=str(second))
            owner.openShelfBook(second_id)
            self.assertEqual(owner._active_column().paragraphNotes, [])
            other_note = owner._active_column().saveParagraphNote(2, 'blue', 'B 书笔记')
            self.assertNotEqual(other_note, note_id)
            self.assertEqual(len(owner._active_column().paragraphNotes), 1)
            owner.openShelfBook(first_id)
            self.assertEqual([row['id'] for row in owner._active_column().paragraphNotes], [note_id])

    def test_ai_answer_appends_to_frozen_book_anchor_after_switching_books(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(); first = self.task(root, 'first'); second = self.task(root, 'second')
            owner = self.owner(root); owner._select_source(first / '原文/story.txt', project=first)
            column = owner._active_column(); first_id = column.bookId
            note_id = column.saveParagraphNote(2, 'rose', '我原来写的内容。')
            quote = column.reader.rows[2][:4]
            owner.askAI.openSelection(column.columnId, 2, quote)
            self.assertEqual(owner.askAI.origin['document_id'], column._document_id())

            second_id = owner.library.remember(second / '原文/story.txt', job=str(second))
            owner.openShelfBook(second_id)
            owner.askAI._question = '这里为什么这样写？'
            owner.askAI.answer = '这是一次用于测试的回答。'
            owner.askAI.answer_id = 'frozen-origin-turn'
            owner.askAI._answer_locale = 'en'
            self.assertTrue(owner.askAI.state['canSaveToNote'])
            self.assertTrue(owner.askAI.saveAnswerToNote())
            self.assertTrue(owner.askAI.state['noteSaved'])
            self.assertEqual(owner._active_column().paragraphNotes, [])

            owner.openShelfBook(first_id)
            saved = owner._active_column().paragraphNoteById(note_id)
            self.assertEqual(saved['color'], 'rose')
            self.assertIn('我原来写的内容。', saved['content'])
            self.assertIn('问：这里为什么这样写？', saved['content'])
            self.assertIn('AI 回答：这是一次用于测试的回答。', saved['content'])
            self.assertEqual({'frozen-origin-turn': 'en'}, saved['ask_entry_locales'])
            self.assertFalse(owner.askAI.saveAnswerToNote())
            self.assertEqual(saved['content'].count('AI 回答：'), 1)
            owner.shutdown()

    def test_paragraph_notes_and_ai_append_have_no_character_limit(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(); job = self.task(root, 'book')
            owner = self.owner(root); owner._select_source(job / '原文/story.txt', project=job)
            column = owner._active_column()
            original = '原有随笔。' * 1200
            note_id = column.saveParagraphNote(2, 'blue', original)
            self.assertEqual(column.paragraphNoteById(note_id)['content'], original)

            quote = column.reader.rows[2][:4]
            owner.askAI.openSelection(column.columnId, 2, quote)
            owner.askAI._question = '请完整解释这一段。'
            owner.askAI.answer = '较长回答。' * 1400
            owner.askAI.answer_id = 'long-answer-turn'
            self.assertTrue(owner.askAI.saveAnswerToNote())
            saved = column.paragraphNoteById(note_id)
            self.assertGreater(len(saved['content']), 10000)
            self.assertTrue(saved['content'].endswith(owner.askAI.answer))
            owner.shutdown()

    def test_epub_notes_share_book_identity_between_editions(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(); job = self.task(root, 'book')
            owner = self.owner(root); owner._select_source(job / '原文/story.txt', project=job)
            translated = owner._active_column()
            anchor = {'kind': 'source-segment', 'segment': 2, 'offset': 0,
                      'segments': '2', 'source': SOURCES[1], 'chapter': 'chapter.xhtml',
                      'block': 4, 'edition': 'translation'}
            identity = translated.saveEpubParagraphNote(anchor, 'green', '一个页边想法')
            self.assertEqual(translated.paragraphNoteById(identity)['document_id'], translated._document_id())
            self.assertEqual(translated.paragraphNoteForEpubAnchor(anchor)['id'], identity)

            # A corrupt legacy anchor must not hide valid notes or block a new
            # save for this book.
            stored = translated._note_rows()
            stored.append({'id': 'broken', 'document_id': translated._document_id(),
                           'anchor': {'kind': 'source-segment', 'segment': 'not-a-number'}})
            owner.settings.setValue(translated._note_key(), json.dumps(stored, ensure_ascii=False))
            self.assertEqual(translated.paragraphNoteForEpubAnchor(anchor)['id'], identity)
            second_anchor = {**anchor, 'segment': 3, 'segments': '3'}
            self.assertTrue(translated.saveEpubParagraphNote(second_anchor, 'blue', '第二条'))
            peer = translated.__class__(owner, {'id': 'original', 'kind': 'text',
                                      'source': str(job / '原文/story.txt'), 'reading_file': str(job / '原文/story.txt')})
            peer._reader_identity = translated._reader_identity
            self.assertEqual(peer.paragraphNoteById(identity)['content'], '一个页边想法')

    def test_columns_show_two_books_at_once_while_translation_keeps_running(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            first = self.task(root, 'first'); second = self.task(root, 'second')
            owner = self.owner(root)
            first_id = owner.library.remember(first / '原文/story.txt', job=str(first))
            second_id = owner.library.remember(second / '原文/story.txt', job=str(second))
            owner._select_source(first / '原文/story.txt', project=first)
            first_column = owner.readingColumns[0]
            first_column.readerBilingual = True
            first_column.saveReadingPosition(2)
            owner._running = True
            task = (owner.jobPath, owner.sourcePath, owner._output_path, owner._process, owner._progress)
            owner.addReadingColumn()
            owner.openShelfBook(second_id)
            second_column = owner.readingColumns[1]
            self.assertEqual(len(owner.readingColumns), 2)
            self.assertIsNot(first_column.reader, second_column.reader)
            self.assertEqual(first_column.reader.rows, TARGETS)
            self.assertEqual(second_column.reader.rows, TARGETS)
            self.assertEqual(owner.readingJobPath, str(second))
            self.assertEqual(first_column.reader.source_rows, SOURCES)
            second_column.saveReadingPosition(1)
            owner._refresh_reader()
            self.assertEqual((owner.jobPath, owner.sourcePath, owner._output_path, owner._process, owner._progress), task)
            self.assertFalse(owner.manageReadingBook())
            first_column.activate()
            self.assertEqual(first_column.savedReadingPosition, 2)
            second_column.activate()
            self.assertEqual(second_column.savedReadingPosition, 1)
            owner._running = False
            owner.shutdown(); owner.settings.sync()
            restored = self.owner(root, restore=True)
            self.assertEqual(len(restored.readingColumns), 2)
            for column in restored.readingColumns:
                self.assertEqual(
                    sip.getCppPointer(column.reader.parent()),
                    sip.getCppPointer(column),
                    'restored ReaderModel must be owned by its live column',
                )
            self.assertEqual(restored.jobPath, str(first))
            self.assertEqual(restored.readingJobPath, str(second))
            self.assertEqual([column.savedReadingPosition for column in restored.readingColumns], [2, 1])
            self.assertTrue(restored.manageReadingBook())
            self.assertEqual(restored.jobPath, str(second))

    def test_shelf_replaces_only_active_column_even_when_book_is_open_elsewhere(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            first = self.task(root, 'first'); second = self.task(root, 'second')
            owner = self.owner(root)
            owner._select_source(first / '原文/story.txt', project=first)
            first_id = owner.readingColumns[0].bookId
            second_id = owner.library.remember(second / '原文/story.txt', job=str(second))
            identity = owner.activeReadingColumnId
            owner.saveReadingPosition(2)
            owner.openShelfBook(second_id)
            self.assertEqual(len(owner.readingColumns), 1)
            self.assertEqual(owner.activeReadingColumnId, identity)
            self.assertEqual(owner.readingColumns[0].bookId, second_id)
            owner.openShelfBook(first_id)
            self.assertEqual(owner.savedReadingPosition, 2)
            owner.addReadingColumn()
            new_id = owner.activeReadingColumnId
            owner.openShelfBook(second_id)
            owner.openShelfBook(first_id)
            self.assertEqual(len(owner.readingColumns), 2)
            self.assertEqual(owner.activeReadingColumnId, new_id)
            self.assertTrue(all(c.bookId == first_id for c in owner.readingColumns))
            self.assertEqual(owner.jobPath, str(first))

    def test_background_append_does_not_replace_other_book_and_updates_on_return(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(); first = self.task(root, 'first'); second = self.task(root, 'second')
            set_translation_extent(first, 2)
            owner = self.owner(root)
            owner._select_source(first / '原文/story.txt', project=first)
            first_column = owner.readingColumns[0]
            second_id = owner.library.remember(second / '原文/story.txt', job=str(second))
            owner.addReadingColumn()
            owner.openShelfBook(second_id)
            second_column = owner.readingColumns[1]
            previous = list(second_column.reader.rows)
            owner._output_path = str(set_translation_extent(first, 4))
            owner._refresh_reader()
            self.wait_reader(owner)
            self.assertEqual(owner.readingJobPath, str(second))
            self.assertEqual(second_column.reader.rows, previous)
            self.assertEqual(first_column.reader.rows, TARGETS)

    def test_explicit_empty_column_persists_and_is_not_hijacked_by_translation_progress(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(); first = self.task(root, 'first')
            owner = self.owner(root); owner._select_source(first / '原文/story.txt', project=first)
            blank_id = owner.addReadingColumn()
            blank = owner.readingColumns[-1]
            self.assertEqual(blank.columnId, blank_id)
            self.assertTrue(blank.empty)
            owner._output_path = str(first / '译文/story.zh-CN.txt')
            owner._refresh_reader()
            self.assertTrue(blank.empty)
            self.assertEqual(blank.reader.count, 0)
            owner.shutdown(); owner.settings.sync()
            restored = self.owner(root, restore=True)
            self.wait_reader(restored)
            self.assertEqual(len(restored.readingColumns), 2)
            self.assertTrue(restored.readingColumns[-1].empty)
            first_column, blank_column = restored.readingColumns
            first_column.activate()
            restored.removeReadingColumn(blank_column.columnId)
            self.assertEqual(restored.activeReadingColumnId, first_column.columnId)

    def test_bookmarks_follow_segment_after_edit_and_are_book_local_and_persistent(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(); first = self.task(root, 'first'); second = self.task(root, 'second')
            owner = self.owner(root); owner._select_source(first / '原文/story.txt', project=first)
            first_id = owner.readingTabs[0]['id']
            owner.addBookmark(2, '重读这里')
            owner.addBookmark(2, '第三段')
            self.assertEqual(len(owner.bookmarks), 1)
            identity = owner.bookmarks[0]['id']
            self.assertEqual(owner.bookmarks[0]['segment'], 3)
            book = pe.snapshot(first); machine = first / '译文/story.zh-CN.txt'
            flag = notes.add(first, machine, book, pe.engine, owner.reader.rows, 0, 0, 2, '马拉', '', '人名')
            notes.save_edit(first, flag['id'], TARGETS[0].replace('她把', '\n她把'), book, pe.engine)
            current = notes.publish_edits(first, [flag['id']], book, pe.engine)['output']
            owner.openReadingFile(current)
            self.assertEqual(owner.bookmarks[0]['position'], 3)
            second_id = owner.library.remember(second / '原文/story.txt', job=str(second))
            owner.openShelfBook(second_id)
            self.assertEqual(owner.bookmarks, [])
            owner.openShelfBook(first_id)
            owner.shutdown(); owner.settings.sync()
            restored = self.owner(root, restore=True)
            self.wait_reader(restored)
            self.assertEqual(restored.bookmarks[0]['title'], '第三段')
            self.assertEqual(restored.bookmarks[0]['position'], 3)
            restored.removeBookmark(identity)
            self.assertEqual(restored.bookmarks, [])

    def test_close_remove_and_restore_empty_tabs_never_delete_files(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(); first = self.task(root, 'first')
            owner = self.owner(root); owner._select_source(first / '原文/story.txt', project=first)
            identity = owner.readingTabs[0]['id']
            before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in first.rglob('*') if p.is_file()}
            owner.addBookmark(1, '')
            owner.removeShelfBook(identity)
            self.assertEqual(len(owner.readingTabs), 1)
            owner.closeReadingTab(identity)
            self.assertEqual(owner.readingTabs, [])
            owner._refresh_reader()
            self.assertEqual(owner.reader.count, 0)
            owner.shutdown(); owner.settings.sync()
            restored = self.owner(root, restore=True)
            self.assertEqual(restored.readingTabs, [])
            self.assertEqual(restored.bookshelf, [])
            self.assertEqual(restored.reader.count, 0)
            self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in first.rglob('*') if p.is_file()})

    def test_managed_txt_resolves_other_task_for_bilingual_and_annotation(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(); first = self.task(root, 'first'); second = self.task(root, 'second')
            owner = self.owner(root); owner._select_source(first / '原文/story.txt', project=first)
            owner.openReadingFile(str(second / '译文/story.zh-CN.txt'))
            owner.readerBilingual = True
            self.assertEqual(owner.jobPath, str(first))
            self.assertEqual(owner.readingJobPath, str(second))
            self.assertEqual(owner.reader.source_rows, SOURCES)
            self.assertTrue(owner.post_editor.addAnnotation(0, 0, 2, '马拉', 'test', '人名'))
            self.assertEqual(notes.load(first)['items'], [])
            self.assertEqual(len(notes.load(second)['items']), 1)

    def test_unrelated_txt_bookmark_does_not_guess_after_text_removed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve(); owner = self.owner(root)
            text = root / 'notes.txt'; text.write_text('第一段\n第二段\n第三段')
            owner.openReadingFile(str(text)); owner.addBookmark(1, '')
            text.write_text('新增段\n第一段\n第二段\n第三段'); owner._refresh_reader()
            self.wait_reader(owner)
            self.assertEqual(owner.bookmarks[0]['position'], 2)
            text.write_text('新增段\n第一段\n另一段\n第三段'); owner._refresh_reader()
            self.wait_reader(owner)
            self.assertEqual(owner.bookmarks[0]['position'], -1)
