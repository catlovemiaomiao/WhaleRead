import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime/lib'))
sys.path.insert(0, str(ROOT / 'runtime/tools'))
from epub_translation import attach, export, translated_label
from task_config import extract_epub
from translate_range import split_source
from epub_reader import open_epub


class EpubTranslationTests(unittest.TestCase):
    def test_hidden_math_comments_are_not_translated_but_tail_text_is_preserved(self):
        from epub_translation import slots
        from epub_reader import xml
        doc = xml(b'<html><body><p>Value<!-- <math><mi>N</mi></math> --> remains.</p></body></html>')
        self.assertEqual(''.join(t for _, _, t in slots(doc)), 'Value remains.')
    def test_combined_directory_labels_require_complete_local_evidence(self):
        segments = ['ЧАСТЬ ПЕРВАЯ', 'Порт-Саид — Аден', 'Имя', 'Имя']
        translated = {1: '第一部', 2: '塞得港—亚丁', 3: '甲', 4: '乙'}
        self.assertEqual(translated_label('ЧАСТЬ ПЕРВАЯ Порт-Саид — Аден', segments, translated, {1, 2}), '第一部　塞得港—亚丁')
        self.assertIsNone(translated_label('ЧАСТЬ ПЕРВАЯ Порт-Саид — Аден', segments, {1: '第一部'}, {1, 2}))
        self.assertIsNone(translated_label('Имя', segments, translated))
        self.assertEqual(translated_label('Имя', segments, translated, {3}), '甲')
        self.assertIsNone(translated_label('Имя', segments, {3: '甲'}, {4}))

    def test_roundtrip_preserves_resources_and_markup_with_partial_and_full_translation(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            original = root / 'book.epub'
            with zipfile.ZipFile(original, 'w') as z:
                z.writestr('mimetype', 'application/epub+zip')
                z.writestr('META-INF/container.xml', '<container><rootfile full-path="book.opf"/></container>')
                z.writestr('book.opf', '<package><metadata><title>Book</title><language>en</language></metadata><manifest><item id="c" href="c.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c"/></spine></package>')
                z.writestr('c.xhtml', '<html xmlns="http://www.w3.org/1999/xhtml"><head><title>Chapter</title><link rel="stylesheet" href="style.css"/></head><body><h1 id="ch">Chapter</h1><p>A <em>quiet</em> sea.</p><img src="cover.png"/><p id="last">Next paragraph.</p></body></html>')
                z.writestr('style.css', 'p { line-height: 1.8; }')
                z.writestr('cover.png', b'untouched image fixture')
            before = original.read_bytes()
            attach(root, original)
            segments, _, _ = split_source(extract_epub(original), 'auto')
            output = root / '译文/book.zh-CN.epub'
            translations = {i: '译文' + str(i) for i in range(1, len(segments) + 1)}
            export(root, output, segments, {1: translations[1]})
            self.assertIn('Next paragraph.', extract_epub(output))
            self.assertIn('翻译中', open_epub(output, root / 'cache')['title'])
            export(root, output, segments, translations)
            self.assertEqual(original.read_bytes(), before)
            with zipfile.ZipFile(output) as z:
                self.assertEqual(z.infolist()[0].filename, 'mimetype')
                self.assertEqual(z.infolist()[0].compress_type, zipfile.ZIP_STORED)
                self.assertEqual(z.read('cover.png'), b'untouched image fixture')
                self.assertEqual(z.read('style.css'), b'p { line-height: 1.8; }')
                text = z.read('c.xhtml').decode()
                self.assertIn('id="last"', text)
                self.assertIn('<em>', text)
                self.assertIn('data-whale-source-text="Next paragraph."', text)
                self.assertNotIn('Next paragraph.', extract_epub(output))
                self.assertNotIn('data-whale-pending="true"', text.split('<body>')[1])
            good = output.read_bytes()
            with self.assertRaises(ValueError):
                export(root, output, ['not the same source'], {1: '错误'})
            self.assertEqual(output.read_bytes(), good)


if __name__ == '__main__':
    unittest.main()
