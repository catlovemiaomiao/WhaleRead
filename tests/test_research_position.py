import hashlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime/lib'))
from research import import_pdf, load
from research_position import render_position, position_path


class OriginalPositionTests(unittest.TestCase):
    def test_scan_with_hidden_text_masks_translation_and_preserves_pending_and_visual(self):
        import pymupdf as fitz
        from PIL import Image
        import pdf_backend
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with fitz.open() as picture:
                page = picture.new_page(width=400, height=300)
                page.insert_text((40, 65), 'Scanned paragraph.', fontsize=11)
                page.insert_text((40, 90), 'Pending words.', fontsize=11)
                page.draw_rect(fitz.Rect(35, 105, 120, 170), fill=(0, 0, 1))
                raster = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False).tobytes('png')
            with fitz.open() as layered:
                page = layered.new_page(width=400, height=300)
                page.insert_image(page.rect, stream=raster)
                page.insert_text((40, 65), 'Scanned paragraph.', fontsize=11, render_mode=3)
                page.insert_text((40, 90), 'Pending words.', fontsize=11, render_mode=3)
                layered.save(root / 'scan.pdf')
            job = import_pdf(root / 'scan.pdf', root / 'jobs')
            before = hashlib.sha256((job / 'original.pdf').read_bytes()).hexdigest()
            with pdf_backend.open(job / 'original.pdf') as doc:
                original = doc[0].get_pixmap(matrix=pdf_backend.Matrix(2, 2), alpha=False).image.copy()
            # Deliberately coarse OCR geometry crosses a pending paragraph and
            # a diagram. Neither may be erased while replacing the translated text.
            data = dict(approved=True, blocks=[
                dict(label='text', visual=False, origin='native', bbox=[.085, .145, .35, .30],
                     source='Scanned paragraph.', translation='扫描正文', font_size=11),
                dict(label='text', visual=False, origin='native', bbox=[.08, .245, .40, .095],
                     source='Pending words.', translation='', font_size=11),
                dict(label='image', visual=True, origin='ocr', bbox=[.075, .335, .25, .25],
                     source='diagram', translation='')])
            saved_data = repr(data)
            result = load(render_position(job, 1, data))
            image = Image.open(position_path(job, 1, data).with_suffix('.png')).convert('RGB')
            crop = (70, 90, 360, 140)
            self.assertNotEqual(set(original.crop(crop).tobytes()), {255})
            self.assertEqual(set(image.crop(crop).tobytes()), {255})
            for crop in ((65, 151, 340, 195), (70, 210, 240, 340)):
                self.assertEqual(image.crop(crop).tobytes(), original.crop(crop).tobytes())
            self.assertTrue(result['blocks'][0]['translated'])
            self.assertFalse(result['blocks'][1]['translated'])
            self.assertEqual(repr(data), saved_data)
            self.assertEqual(hashlib.sha256((job / 'original.pdf').read_bytes()).hexdigest(), before)

    def test_visible_native_text_on_image_keeps_image_background(self):
        import pymupdf as fitz
        from PIL import Image
        import pdf_backend
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            stream = io.BytesIO()
            Image.new('RGB', (400, 300), (205, 223, 241)).save(stream, format='PNG')
            with fitz.open() as doc:
                page = doc.new_page(width=400, height=300)
                page.insert_image(page.rect, stream=stream.getvalue())
                page.insert_text((40, 65), 'Native foreground.', fontsize=11)
                doc.save(root / 'visible.pdf')
            job = import_pdf(root / 'visible.pdf', root / 'jobs')
            with pdf_backend.open(job / 'original.pdf') as doc:
                original = doc[0].get_pixmap(matrix=pdf_backend.Matrix(2, 2), alpha=False).image.copy()
            data = dict(approved=True, blocks=[dict(label='text', visual=False, origin='native',
                bbox=[.085, .145, .35, .09], source='Native foreground.', translation='可见文字')])
            render_position(job, 1, data)
            image = Image.open(position_path(job, 1, data).with_suffix('.png')).convert('RGB')
            background = original.getpixel((20, 20))
            crop = image.crop((70, 90, 350, 145))
            self.assertEqual(crop.tobytes(), bytes(background) * crop.width * crop.height)

    def test_partial_translation_preserves_frames_untranslated_text_and_source(self):
        import pymupdf as fitz
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / 'test.pdf'
            with fitz.open() as doc:
                page = doc.new_page(width=400, height=400)
                page.draw_rect(fitz.Rect(35, 40, 200, 140), color=(0, 0, 1))
                page.insert_text((45, 65), 'A title', fontsize=11)
                page.insert_text((45, 85), 'Body to translate.', fontsize=11)
                page.insert_text((230, 85), 'Leave this source.', fontsize=11)
                doc.save(source)
                original = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            job = import_pdf(source, root / 'jobs')
            import pdf_backend
            with pdf_backend.open(source) as baseline:
                original = baseline[0].get_pixmap(matrix=pdf_backend.Matrix(2, 2), alpha=False)
            before = hashlib.sha256((job / 'original.pdf').read_bytes()).hexdigest()
            data = dict(approved=True, blocks=[
                dict(label='paragraph_title', visual=False, origin='native',
                     bbox=[.1, .125, .25, .04], font_size=11, source='A title', translation='标题'),
                dict(label='text', visual=False, origin='native',
                     bbox=[.1, .14, .4, .1], font_size=11, source='Body to translate.', translation='正文'),
                dict(label='text', visual=False, origin='native',
                     bbox=[.56, .16, .4, .08], font_size=11, source='Leave this source.', translation='')])
            result = load(render_position(job, 1, data))
            pix = fitz.Pixmap(str(position_path(job, 1, data).with_suffix('.png')))
            # Frame and untouched right-hand text retain original pixels.
            for x in range(70, 400):
                self.assertEqual(pix.pixel(x, 80), original.pixel(x, 80))
            for y in range(135, 175):
                for x in range(455, 740):
                    self.assertEqual(pix.pixel(x, y), original.pixel(x, y))
            title, body, pending = result['blocks']
            self.assertLess(title['bbox'][1] + title['bbox'][3], body['bbox'][1])
            self.assertFalse(pending['translated'])
            self.assertEqual(hashlib.sha256((job / 'original.pdf').read_bytes()).hexdigest(), before)
            old_path = position_path(job, 1, data)
            data['blocks'][1]['translation'] = '修订正文'
            self.assertNotEqual(position_path(job, 1, data), old_path)

    def test_unapproved_translation_does_not_replace_source(self):
        import pymupdf as fitz
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with fitz.open() as doc:
                p = doc.new_page(width=200, height=200)
                p.insert_text((25, 50), 'Check first.', fontsize=11)
                doc.save(root / 'test.pdf')
                original = p.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
            job = import_pdf(root / 'test.pdf', root / 'jobs')
            import pdf_backend
            with pdf_backend.open(root / 'test.pdf') as baseline:
                original = baseline[0].get_pixmap(matrix=pdf_backend.Matrix(2, 2), alpha=False)
            data = dict(approved=False, blocks=[dict(label='text', visual=False, origin='native',
                bbox=[.1, .15, .7, .2], source='Check first.', translation='尚待确认')])
            result = load(render_position(job, 1, data))
            self.assertFalse(result['blocks'][0]['translated'])
            pix = fitz.Pixmap(str(position_path(job, 1, data).with_suffix('.png')))
            self.assertEqual(original.samples, pix.samples)


if __name__ == '__main__':
    unittest.main()
