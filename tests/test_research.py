import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime/lib'))
from research import protect, restore, import_pdf, save, load, build_page, edit_source, page_dir, render_page, join_prose, preserve_as_image, reading_text, compare_ocr


class ResearchTests(unittest.TestCase):
    def test_ocr_witness_flags_probable_primary_extension(self):
        witness = ('Although a rigorous definition is involved, the general idea is simple. '
                   'Every sequence produced by the proc-')
        primary = (witness[:-5] + 'process f(x,y) is a function of the input variable x.')
        result = compare_ocr(primary, witness)
        self.assertEqual(result['status'], 'review')
        self.assertTrue(result['possible_extension'])

    def test_ocr_witness_accepts_minor_character_noise(self):
        result = compare_ocr('The probability is nearly zero.', 'The probability is nearly zero')
        self.assertEqual(result['status'], 'agree')

    def test_ocr_witness_flags_caption_number_disagreement(self):
        result = compare_ocr('Fig. 5 - A graph corresponding to the source.',
                             'Fig. S - A graph corresponding to the source.')
        self.assertEqual(result['status'], 'check')
        self.assertTrue(result['numeric_conflict'])

    def test_second_ocr_is_evidence_and_never_overwrites_primary(self):
        import pymupdf as fitz
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doc = fitz.open()
            doc.new_page()
            doc.save(root / 'scan.pdf')
            doc.close()
            job = import_pdf(root / 'scan.pdf', root / 'library')
            secondary = dict(engine='tesseract', status='review', text='Visible source ends proc-',
                             similarity=.61, length_ratio=.62, possible_extension=True,
                             reason='主识别疑似比页面可见文字多出内容')
            with patch('research.tesseract_witness', return_value=secondary):
                data = build_page(job, 1, dict(width=595, height=842, parsing_res_list=[
                    dict(block_label='text', block_bbox=[40, 40, 500, 120],
                         block_content='Visible source ends proc- invented continuation')]))
            self.assertEqual(data['blocks'][0]['source'], 'Visible source ends proc- invented continuation')
            self.assertEqual(data['blocks'][0]['witness']['text'], 'Visible source ends proc-')
            self.assertEqual(data['witness_conflicts'], 1)
            self.assertFalse(data['approved'])

    def test_wrap_join_keeps_uncertain_hyphen(self):
        self.assertEqual(join_prose('var-\nious messages\nof N', 'various messages of N'), 'various messages of N')
        self.assertEqual(join_prose('long-\nterm memory', 'long-term memory'), 'long-term memory')

    def test_embedded_symbol_image_invalidates_incomplete_translation(self):
        import pymupdf as fitz
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doc = fitz.open()
            page = doc.new_page(width=600, height=800)
            source = 'If the enemy intercepts letters, the probability changes. Additional source text.'
            page.insert_text((40, 50), source)
            symbol_doc = fitz.open()
            symbol = symbol_doc.new_page(width=20, height=20)
            symbol.insert_text((3, 14), 'N')
            page.insert_image(fitz.Rect(150, 40, 160, 50), stream=symbol.get_pixmap().tobytes('png'))
            doc.save(root / 'hybrid.pdf')
            doc.close()
            job = import_pdf(root / 'hybrid.pdf', root / 'library')
            ocr = source.replace('intercepts letters', 'intercepts N letters')
            result = dict(width=600, height=800, parsing_res_list=[dict(block_label='text', block_bbox=[35, 30, 580, 65], block_content=ocr)])
            prior = dict(approved=True, blocks=[dict(id=0, source=source, translation='旧漏字译文')])
            data = build_page(job, 1, result, previous=prior)
            self.assertEqual(data['blocks'][0]['origin'], 'mixed')
            self.assertIn('N letters', data['blocks'][0]['source'])
            self.assertFalse(data['approved'])
            self.assertEqual(data['blocks'][0]['translation'], '')
            self.assertEqual(data['blocks'][0]['revisions'][0]['translation'], '旧漏字译文')
            self.assertEqual(len(data['blocks'][0]['bbox']), 4)

    def test_protection_is_exact_and_rejects_missing_or_duplicate(self):
        source = 'AGI improves when x grows. Homo sapiens has α receptors.'
        masked, mapping = protect(source, ['Homo sapiens'])
        self.assertEqual(restore(masked, mapping), source)
        self.assertNotIn('Homo sapiens', masked)
        with self.assertRaises(ValueError):
            restore(masked.replace(next(iter(mapping)), ''), mapping)
        with self.assertRaises(ValueError):
            restore(masked + next(iter(mapping)), mapping)

    def test_complete_inline_formulas_are_protected(self):
        source = r'The distance is $ \frac{H(K)}{D} $ when N grows.'
        masked, mapping = protect(source)
        self.assertIn(r'$ \frac{H(K)}{D} $', mapping.values())
        self.assertEqual(restore(masked, mapping), source)

    def test_uppercase_heading_is_translatable_but_body_acronyms_stay_fixed(self):
        heading, heading_map = protect('MATHEMATICAL STRUCTURE OF SECRECY SYSTEMS', protect_uppercase=False)
        self.assertEqual(heading, 'MATHEMATICAL STRUCTURE OF SECRECY SYSTEMS')
        self.assertEqual(heading_map, {})
        body, body_map = protect('An AGI system')
        self.assertIn('AGI', body_map.values())

    def test_font_style_is_evidence_not_a_whole_block_verdict(self):
        prose = 'This italic theorem statement still contains ordinary language that readers need translated.'
        italic = [dict(text=prose, font='Times-Italic', flags=2)]
        self.assertFalse(preserve_as_image('text', prose, italic))
        mixed = r'The function $ H(N) $ is useful because it measures uncertainty in ordinary language.'
        spans = [dict(text='The function is useful because it measures uncertainty in ordinary language.',
                      font='Times-Roman', flags=4),
                 dict(text='H(N)', font='CMMI10', flags=2)]
        self.assertFalse(preserve_as_image('text', mixed, spans))
        self.assertTrue(preserve_as_image('display_formula', r'$ H(N)=0 $', spans))
        self.assertTrue(preserve_as_image('text', r'$ H(N)=0 $', [dict(text='H(N)=0', font='CMMI10', flags=2)]))

    def test_old_all_caps_heading_translation_is_retired_on_layout_upgrade(self):
        import pymupdf as fitz
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doc = fitz.open()
            page = doc.new_page(width=600, height=800)
            page.insert_text((40, 50), 'Enough native body text to make this an editable PDF page. ' * 2)
            page.insert_text((40, 100), 'PART I')
            doc.save(root / 'heading.pdf')
            doc.close()
            job = import_pdf(root / 'heading.pdf', root / 'library')
            prior = dict(approved=True, blocks=[dict(id=0, source='PART I', translation='PART 一', visual=False)])
            data = build_page(job, 1, dict(width=600, height=800, parsing_res_list=[
                dict(block_label='paragraph_title', block_bbox=[35, 80, 120, 110], block_content='PART I')]), previous=prior)
            self.assertEqual(data['blocks'][0]['translation'], '')
            self.assertEqual(data['blocks'][0]['revisions'][-1]['translation'], 'PART 一')
            self.assertTrue(data['translation_rules_changed'])

    def test_native_text_overrides_hallucinated_ocr_and_formulas_stay_pixels(self):
        import pymupdf as fitz
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / 'paper.pdf'
            doc = fitz.open()
            page = doc.new_page(width=600, height=800)
            page.insert_text((40, 50), 'Native scientific prose must remain the authoritative source text for this page.')
            page.insert_text((40, 140), 'x = 2')
            doc.save(source)
            doc.close()
            job = import_pdf(source, root / 'library')
            self.assertEqual(job, import_pdf(source, root / 'library'))
            render_page(job, 1)
            data = build_page(job, 1, dict(width=600, height=800, parsing_res_list=[
                dict(block_label='text', block_bbox=[35, 30, 590, 65], block_content='INVENTED OCR'),
                dict(block_label='display_formula', block_bbox=[35, 120, 100, 150], block_content='WRONG FORMULA')]))
            self.assertEqual(data['blocks'][0]['origin'], 'native')
            self.assertNotIn('INVENTED', data['blocks'][0]['source'])
            self.assertTrue(data['blocks'][1]['visual'])
            self.assertTrue(data['approved'])
            path = page_dir(job, 1) / 'page.json'
            data['blocks'][0]['translation'] = 'OLD'
            save(path, data)
            edit_source(job, 1, 0, 'Corrected source')
            updated = load(path)
            self.assertFalse(updated['approved'])
            self.assertEqual(updated['blocks'][0]['translation'], '')
            self.assertTrue(updated['blocks'][0]['reviewed'])
            self.assertEqual(updated['blocks'][0]['source'], 'Corrected source')
            self.assertEqual(updated['blocks'][0]['revisions'][-1]['translation'], 'OLD')
            self.assertEqual(source.read_bytes(), (job / 'original.pdf').read_bytes())

    def test_scanned_text_requires_confirmation(self):
        import pymupdf as fitz
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            doc = fitz.open()
            doc.new_page()
            doc.save(root / 'scan.pdf')
            doc.close()
            job = import_pdf(root / 'scan.pdf', root / 'library')
            data = build_page(job, 1, dict(width=595, height=842, parsing_res_list=[
                dict(block_label='text', block_bbox=[40, 40, 400, 100], block_content='OCR text')]))
            self.assertFalse(data['approved'])
            self.assertEqual(data['blocks'][0]['status'], 'needs_review')

    def test_contractions_are_not_variables(self):
        source = "The system’s design doesn't change x or H."
        text, mapping = protect(source)
        self.assertIn('system’s', text)
        self.assertIn("doesn't", text)
        self.assertEqual(set(mapping.values()), {'x', 'H'})

    def test_numbers_and_simple_relations_are_preserved(self):
        text, mapping = protect('If N is large (say 50 letters); for N = 15 use R; D is .7, i.e. small.')
        self.assertIn('N = 15', mapping.values())
        self.assertIn('50', mapping.values())
        self.assertIn('.7', mapping.values())
        self.assertNotIn('e', mapping.values())
        self.assertEqual(restore(text, mapping), 'If N is large (say 50 letters); for N = 15 use R; D is .7, i.e. small.')

    def test_reading_text_keeps_math_meaning_without_raw_tex_delimiters(self):
        text = reading_text(r'Distance $ \frac{H(K)}{D} $, $ \log_{10}26! $, and $ N \to \infty $.')
        self.assertEqual(text, 'Distance H(K)⁄D, log₁₀26!, and N → ∞.')


if __name__ == '__main__':
    unittest.main()
