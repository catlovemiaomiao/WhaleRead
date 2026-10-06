import sys
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime/lib'))
from research_terms import prepare, validate, candidates
from research import save


def rule(term, mode, target='', page=0):
    return dict(term=term, mode=mode, target=target, page=page)


class TermTests(unittest.TestCase):
    def test_abbreviation_containing_number_stays_visible(self):
        text, mapping, checks, _ = prepare('GPT-5 and AI', [], 1)
        self.assertEqual(text, 'GPT-5 and AI')
        self.assertEqual(mapping, {})
        self.assertEqual(checks, [('GPT-5', 'GPT-5'), ('AI', 'AI')])

    def test_formula_contains_confirmed_variable(self):
        text, mapping, checks, _ = prepare('$x=2$ and x', [rule('x', 'keep', page=1)], 1)
        self.assertEqual(list(mapping.values()), ['$x=2$', 'x'])
        self.assertNotIn('zzdecision', validate(text, mapping, checks))

    def test_failed_block_does_not_stop_following_block(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime/tools'))
        import research_worker
        with tempfile.TemporaryDirectory() as raw:
            job = Path(raw)
            (job / 'pages/0001').mkdir(parents=True)
            save(job / 'document.json', {'terms': '', 'term_rules': []})
            data = dict(approved=True, blocks=[dict(source='AI', translation='', visual=False),
                                             dict(source='Hello', translation='', visual=False)])
            with patch.object(research_worker, 'upgrade_page', return_value=data), patch.object(research_worker, 'load_key', return_value=''), patch.object(research_worker, 'emit'), patch.object(research_worker, 'translate_once', side_effect=[('人工智能', .1), ('人工智能', .1), ('你好', .1)]):
                research_worker.translate(job, 1, dict(id='test', auth_provider='test', api_base='http://localhost', model='test'))
            self.assertEqual(data['blocks'][0]['status'], 'translation_failed')
            self.assertEqual(len(data['blocks'][0]['attempts']), 2)
            self.assertEqual(data['blocks'][1]['translation'], '你好')

    def test_visible_abbreviations_and_formula(self):
        text, mapping, checks, instruction = prepare('AI studies AGI with $x=2$.', [], 1)
        self.assertIn('AI studies AGI', text)
        self.assertEqual(list(mapping.values()), ['$x=2$'])
        self.assertEqual(validate('AI研究AGI，' + next(iter(mapping)), mapping, checks), 'AI研究AGI，$x=2$')
        with self.assertRaises(ValueError):
            validate('AGI研究' + next(iter(mapping)), mapping, checks)

    def test_normal_translation_overrides_auto_and_legacy(self):
        text, mapping, checks, _ = prepare('AI and $x=2$', [rule('AI', 'translate'), rule('$x=2$', 'translate')], 1, ['AI'])
        self.assertEqual(text, 'AI and $x=2$')
        self.assertEqual(mapping, {})
        self.assertEqual(checks, [])

    def test_page_override_and_future_pages(self):
        rules = [rule('AI', 'keep'), rule('AI', 'fixed', '人工智能', 2)]
        self.assertEqual(prepare('AI', rules, 2)[2], [('AI', '人工智能')])
        self.assertEqual(prepare('AI', rules, 9)[2], [('AI', 'AI')])

    def test_whole_word_and_longest_phrase(self):
        text, _, checks, _ = prepare('cat scatter cat scan', [rule('cat', 'fixed', '猫'), rule('cat scan', 'fixed', '扫描')], 1)
        self.assertEqual(text, 'cat scatter cat scan')
        self.assertEqual(checks, [('cat', '猫'), ('cat scan', '扫描')])

    def test_candidates_dismissed_only_in_scope(self):
        with tempfile.TemporaryDirectory() as raw:
            job = Path(raw)
            for number in (1, 2):
                path = job / f'pages/{number:04d}/page.json'
                path.parent.mkdir(parents=True)
                save(path, {'blocks': [dict(source='AI and $x=2$', visual=False)]})
            items = candidates(job, 1, [rule('AI', 'translate'), rule('$x=2$', 'keep', page=1)])
            self.assertEqual([(x['term'], x['page']) for x in items], [('$x=2$', 2)])

    def test_repeated_terms_and_legacy(self):
        _, _, checks, _ = prepare('AI and AI', [], 1, ['AI'])
        self.assertEqual(len(checks), 2)
        with self.assertRaises(ValueError):
            validate('AI', {}, checks)
        self.assertEqual(validate('AI与AI', {}, checks), 'AI与AI')
