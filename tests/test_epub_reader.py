import sys
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'runtime/lib'))
from epub_reader import (cache_stats, clear_epub_cache, discard_cache_root,
                         member, open_epub, release_cache_root,
                         retain_cache_root, sanitize)


def write_epub(path, title='Fixture', body='Body'):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(
            'META-INF/container.xml',
            '<container><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>',
        )
        archive.writestr(
            'content.opf',
            '<package><metadata><title>' + title + '</title></metadata><manifest>'
            '<item id="a" href="a.xhtml" media-type="application/xhtml+xml"/>'
            '</manifest><spine><itemref idref="a"/></spine></package>',
        )
        archive.writestr(
            'a.xhtml', '<html><head><title>A</title></head><body><p>' + body + '</p></body></html>')


class EpubTests(unittest.TestCase):
    def test_paths(self):
        for href in ['../secret', '/tmp/secret', 'https://example.com/x', 'a/../../secret', '%2e%2e/secret', 'a\\b']:
            with self.assertRaises(ValueError):
                member('', href)
        self.assertEqual(member('OEBPS', 'Text/a%20b.xhtml'), 'OEBPS/Text/a b.xhtml')

    def test_sanitizer_preserves_style_and_blocks_scripts(self):
        output = sanitize(b'<html xmlns="http://www.w3.org/1999/xhtml"><head><link rel="stylesheet" href="style.css"/><script>alert(1)</script></head><body onload="alert(1)"><p class="indent"><em>Text</em><img src="a.png"/></p><iframe src="file:///etc/passwd"/></body></html>').decode()
        self.assertIn('style.css', output)
        self.assertIn('class="indent"', output)
        self.assertIn('<em>Text</em>', output)
        self.assertNotIn('onload=', output)
        self.assertNotIn('<script', output)
        self.assertNotIn('<iframe', output)
        self.assertIn('Content-Security-Policy', output)

    def test_spine_order_and_source_unchanged(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root/'book.epub'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('META-INF/container.xml', '<container><rootfiles><rootfile full-path="content.opf"/></rootfiles></container>')
                z.writestr('content.opf', '<package><metadata><title>Fixture</title></metadata><manifest><item id="a" href="a.xhtml" media-type="application/xhtml+xml"/><item id="b" href="b.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="b"/><itemref idref="a"/></spine></package>')
                for name in ['a', 'b']:
                    z.writestr(name+'.xhtml', '<html><head><title>'+name+'</title></head><body><p>Body</p></body></html>')
            original = path.read_bytes()
            book = open_epub(path, root/'cache')
            self.assertEqual([x['path'] for x in book['chapters']], ['b.xhtml','a.xhtml'])
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(open_epub(path, root/'cache'), book)

    def test_unsafe_archive_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = root/'bad.epub'
            with zipfile.ZipFile(path, 'w') as z:
                z.writestr('../outside', 'no')
            with self.assertRaises(ValueError):
                open_epub(path, root/'cache')
            self.assertFalse((root/'outside').exists())

    def test_book_scoped_cache_replaces_inactive_generation_but_keeps_live_one(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path, cache = root / 'book.epub', root / 'cache'
            write_epub(path, body='version one')
            first = open_epub(path, cache, cache_identity='shelf-book-1')
            retain_cache_root(first['root'])
            try:
                path.unlink()
                write_epub(path, body='version two')
                second = open_epub(path, cache, cache_identity='shelf-book-1')
                self.assertNotEqual(first['root'], second['root'])
                self.assertTrue(Path(first['root']).is_dir())
                self.assertTrue(Path(second['root']).is_dir())

                release_cache_root(first['root'])
                discard_cache_root(first['root'])
                path.unlink()
                write_epub(path, body='version three')
                third = open_epub(path, cache, cache_identity='shelf-book-1')
                self.assertFalse(Path(first['root']).exists())
                self.assertFalse(Path(second['root']).exists())
                self.assertTrue(Path(third['root']).is_dir())
                self.assertEqual(cache_stats(cache)['generations'], 1)
            finally:
                release_cache_root(first['root'])

    def test_manual_clear_preserves_live_page_then_removes_it_after_release(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cache = root / 'cache'
            first_path, second_path = root / 'first.epub', root / 'second.epub'
            write_epub(first_path, title='First')
            write_epub(second_path, title='Second')
            first = open_epub(first_path, cache, cache_identity='first-book')
            second = open_epub(second_path, cache, cache_identity='second-book')
            retain_cache_root(first['root'])
            try:
                result = clear_epub_cache(cache)
                self.assertTrue(Path(first['root']).is_dir())
                self.assertFalse(Path(second['root']).exists())
                self.assertEqual(result['generations'], 1)
                self.assertGreater(result['remaining'], 0)
            finally:
                release_cache_root(first['root'])
            result = clear_epub_cache(cache)
            self.assertFalse(Path(first['root']).exists())
            self.assertEqual(result['generations'], 0)
            self.assertEqual(result['remaining'], 0)

    def test_semantic_edition_alias_reuses_one_generation_across_two_paths(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first, alias, cache = root/'first.epub', root/'retained.epub', root/'cache'
            write_epub(first)
            alias.write_bytes(first.read_bytes())
            a = open_epub(first, cache, cache_identity='task-one',
                          edition_identity='original', producer_scope='test')
            b = open_epub(alias, cache, cache_identity='task-one',
                          edition_identity='original', producer_scope='test')
            self.assertEqual(a['root'], b['root'])
            self.assertEqual(cache_stats(cache)['generations'], 1)

    def test_binary_assets_are_physically_shared_between_editions(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first, second, cache = root/'first.epub', root/'second.epub', root/'cache'
            asset = b'large immutable image bytes' * 20000
            for path, body in ((first, 'original'), (second, 'translation')):
                write_epub(path, body=body)
                with zipfile.ZipFile(path, 'a') as archive:
                    archive.writestr('image.png', asset)
            a = open_epub(first, cache, cache_identity='task-one',
                          edition_identity='original', producer_scope='test')
            b = open_epub(second, cache, cache_identity='task-one',
                          edition_identity='translation', producer_scope='test')
            left, right = Path(a['root'])/'image.png', Path(b['root'])/'image.png'
            self.assertEqual((left.stat().st_dev, left.stat().st_ino),
                             (right.stat().st_dev, right.stat().st_ino))
            stats = cache_stats(cache)
            self.assertLess(stats['physical_bytes'], stats['bytes'])

    def test_cross_process_clear_respects_prepared_generation_lease(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source, cache = root/'book.epub', root/'cache'
            write_epub(source)
            book, lease = open_epub(source, cache, cache_identity='task-one',
                                    edition_identity='original', acquire_lease=True,
                                    producer_scope='test')
            command = (
                "from epub_reader import clear_epub_cache; "
                "import sys; clear_epub_cache(sys.argv[1])"
            )
            env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1]/'runtime/lib')}
            subprocess.run([sys.executable, '-c', command, str(cache)], check=True, env=env)
            self.assertTrue(Path(book['root']).is_dir())
            lease.release()
            subprocess.run([sys.executable, '-c', command, str(cache)], check=True, env=env)
            self.assertFalse(Path(book['root']).exists())
