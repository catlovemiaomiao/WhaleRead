import base64
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime/lib'))
from PySide6.QtCore import QSettings
from file_access import FileAccessManager, FileAccessError, MacBookmarks, worker_leases
from legacy_import import import_settings, restore
from task_config import extract_source_text


class Lease:
    def __init__(self, path, *, active=True, stale=False):
        self.path, self.active, self.stale = Path(path), active, stale
        self.closed = False

    def close(self):
        self.closed = True


class Bookmarks:
    def __init__(self):
        self.leases = []
        self.active = True
        self.stale = False
        self.moved = None
        self.created = 0
        self.transferred = []
        self.resolution_modes = []

    def create(self, path, read_only=False):
        self.created += 1
        return str(path).encode()

    def transfer(self, lease):
        self.transferred.append(lease.path)
        return b'ephemeral:' + str(lease.path).encode()

    def resolve(self, raw, *, persistent=True):
        self.resolution_modes.append(persistent)
        if not persistent:
            if not raw.startswith(b'ephemeral:'):
                raise FileAccessError('Persistent bookmark received by another identity')
            raw = raw[len(b'ephemeral:'):]
        lease = Lease(self.moved or raw.decode(), active=self.active, stale=self.stale)
        self.leases.append(lease)
        return lease


class FileAccessTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.settings = QSettings(str(self.root / 'settings.ini'), QSettings.IniFormat)
        self.api = Bookmarks()
        self.manager = FileAccessManager(self.settings, internal_roots=[self.root / 'container'],
                                         strict=True, api=self.api)
        self.addCleanup(self.manager.close)

    def test_external_path_without_user_grant_fails_closed(self):
        with self.assertRaises(FileAccessError):
            self.manager.ensure(self.root / 'external.txt')
        self.assertEqual(self.api.created, 0)
        self.assertEqual(self.manager.ensure(self.root / 'container/task.txt'), self.root / 'container/task.txt')

    def test_folder_grant_covers_only_descendants_and_reopens(self):
        folder = self.root / 'books'
        folder.mkdir()
        self.manager.selected(folder, read_only=True)
        self.assertEqual(self.manager.ensure(folder / 'fiction.txt'), folder / 'fiction.txt')
        with self.assertRaises(FileAccessError):
            self.manager.ensure(self.root / 'books-other/fiction.txt')
        self.manager.close()
        reopened = FileAccessManager(QSettings(self.settings.fileName(), QSettings.IniFormat),
                                     strict=True, api=self.api)
        try:
            self.assertEqual(reopened.ensure(folder / 'fiction.txt'), folder / 'fiction.txt')
        finally:
            reopened.close()
        self.assertTrue(all(lease.closed for lease in self.api.leases))

    def test_stale_moved_folder_refreshes_bookmark_without_changing_library_ids(self):
        original = self.root / 'books'
        original.mkdir()
        self.settings.setValue('library/books_v2', '[{"id":"content-stable","source":"old"}]')
        self.api.moved, self.api.stale = self.root / 'moved', True
        self.manager.selected(original)
        self.assertEqual(self.manager.ensure(original / 'chapter.txt'), self.root / 'moved/chapter.txt')
        self.assertEqual(self.api.created, 2)
        self.assertIn('content-stable', self.settings.value('library/books_v2'))

    def test_failed_scope_cannot_be_used_or_sent_to_worker(self):
        self.api.active = False
        with self.assertRaises(FileAccessError):
            self.manager.selected(self.root / 'not-authorized.txt')
        self.assertFalse(self.manager.worker_grants())
        self.assertTrue(self.api.leases[0].closed)

    def test_child_receives_ephemeral_capability_while_persistent_bookmark_is_unchanged(self):
        path = self.root / 'fiction.txt'
        self.manager.selected(path, read_only=True)
        persisted = self.settings.value(FileAccessManager.KEY)
        original = self.manager._rows[str(path)]['bookmark']
        grants = self.manager.worker_grants()
        self.assertNotEqual(grants, [original])
        self.assertEqual(self.api.transferred, [path])
        self.assertEqual(self.settings.value(FileAccessManager.KEY), persisted)
        with patch('file_access.MacBookmarks', return_value=self.api), patch('file_access.is_sandboxed', return_value=True):
            with self.assertRaises(FileAccessError):
                worker_leases([original])
            leases = worker_leases(grants)
        self.assertEqual(leases[0].path, path)
        self.assertEqual(self.api.resolution_modes, [True, False, False])
        for lease in leases:
            lease.close()

    def test_child_refuses_inactive_scope_and_closes_every_acquired_lease(self):
        grants = [base64.b64encode(b'ephemeral:' + str(self.root / 'fiction.txt').encode()).decode()]
        self.api.active = False
        with patch('file_access.MacBookmarks', return_value=self.api), patch('file_access.is_sandboxed', return_value=True):
            with self.assertRaises(FileAccessError):
                worker_leases(grants)
        self.assertTrue(all(lease.closed for lease in self.api.leases))

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS native bookmark test')
    def test_native_bookmark_roundtrip_preserves_readable_synthetic_file(self):
        path = self.root / 'fiction.txt'
        path.write_text('synthetic original')
        api = MacBookmarks()
        lease = api.resolve(api.create(path, read_only=True))
        try:
            self.assertEqual(lease.path.resolve(), path)
            self.assertEqual(lease.path.read_text(), 'synthetic original')
        finally:
            lease.close()


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.previous = QSettings(str(self.root / 'previous.ini'), QSettings.IniFormat)
        self.current = QSettings(str(self.root / 'current.ini'), QSettings.IniFormat)

    def test_merge_preserves_existing_records_and_old_positions_with_recoverable_backup(self):
        self.previous.setValue('library/books_v2', json.dumps([
            dict(id='same', title='old'), dict(id='old-book', title='Fiction')]))
        self.previous.setValue('reader/positions_v1/old-book', 'paragraph-7')
        self.previous.setValue('ask/api_key', 'fictional-do-not-import')
        self.previous.sync()
        self.current.setValue('library/books_v2', json.dumps([dict(id='same', title='current')]))
        self.current.setValue('reader/positions_v1/same', 'paragraph-3')
        before = {k: self.current.value(k) for k in self.current.allKeys()}
        original_bytes = Path(self.previous.fileName()).read_bytes()
        report = import_settings(self.previous.fileName(), self.current, self.root / 'backups')
        rows = json.loads(self.current.value('library/books_v2'))
        self.assertEqual(rows, [dict(id='same', title='current'), dict(id='old-book', title='Fiction')])
        self.assertEqual(self.current.value('reader/positions_v1/old-book'), 'paragraph-7')
        self.assertFalse(self.current.contains('ask/api_key'))
        self.assertEqual(original_bytes, Path(self.previous.fileName()).read_bytes())
        restore(self.current, report['backup'])
        self.assertEqual({k: self.current.value(k) for k in self.current.allKeys()}, before)

    def test_invalid_shelf_does_not_mutate_current_settings(self):
        self.current.setValue('reader/example', 'keep')
        self.previous.setValue('library/books_v2', 'not JSON')
        self.previous.sync()
        with self.assertRaises(ValueError):
            import_settings(self.previous.fileName(), self.current, self.root / 'backups')
        self.assertEqual(self.current.allKeys(), ['reader/example'])

    def test_same_book_notes_and_bookmarks_merge_without_replacing_current_edits(self):
        for prefix in ('reader/paragraph_notes_v1/', 'reader/bookmarks_v1/'):
            key = prefix + 'stable-book'
            self.previous.setValue(key, json.dumps([dict(id='same', text='old'), dict(id='legacy', text='keep')]))
            self.current.setValue(key, json.dumps([dict(id='same', text='current'), dict(id='new', text='keep')]))
        self.previous.setValue('interface/locale', 'en')
        self.previous.sync()
        report = import_settings(self.previous.fileName(), self.current, self.root / 'backups')
        reopened = QSettings(self.current.fileName(), QSettings.IniFormat)
        for prefix in ('reader/paragraph_notes_v1/', 'reader/bookmarks_v1/'):
            self.assertEqual(json.loads(reopened.value(prefix + 'stable-book')), [
                dict(id='same', text='current'), dict(id='new', text='keep'), dict(id='legacy', text='keep')])
        self.assertEqual(reopened.value('interface/locale'), 'en')
        restore(self.current, report['backup'])
        self.assertFalse(self.current.contains('interface/locale'))

    @unittest.skipUnless(sys.platform == 'darwin', 'Native preferences are macOS plist')
    def test_native_preview_plist_is_read_without_touching_production_preferences(self):
        path = self.root / 'preview.plist'
        previous = QSettings(str(path), QSettings.NativeFormat)
        previous.setValue('library/books_v2', '[{"id":"native-old-book","title":"Fiction"}]')
        previous.setValue('reader/positions/native-old-book', 12)
        previous.sync()
        report = import_settings(path, self.current, self.root / 'backups')
        self.assertTrue(report['imported'])
        self.assertEqual(self.current.value('reader/positions/native-old-book'), 12)

    def test_pdf_import_uses_bundled_library_without_external_executable(self):
        from pypdf import PdfWriter
        from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
        writer = PdfWriter()
        page = writer.add_blank_page(width=612, height=792)
        font = DictionaryObject({NameObject('/Type'): NameObject('/Font'),
            NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
        page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): font})})
        stream = DecodedStreamObject()
        stream.set_data(b'BT /F1 12 Tf 72 720 Td (A fictional harbor at dawn.) Tj ET')
        page[NameObject('/Contents')] = writer._add_object(stream)
        path = self.root / 'fiction.pdf'
        writer.write(path)
        with patch('task_config.subprocess.run', side_effect=AssertionError('external executable')):
            self.assertIn('A fictional harbor at dawn.', extract_source_text(path))


if __name__ == '__main__':
    unittest.main()
