from __future__ import annotations
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from test_post_edit import pe, make_book, TARGETS
import annotations as notes
from reader import paragraphs


class AnnotationReviewerTests(unittest.TestCase):
    def fixture(self, root, index=0):
        make_book(root)
        path = root/'译文/story.zh-CN.txt'
        book = pe.snapshot(root)
        flag = notes.add(root, path, book, pe.engine, paragraphs(path.read_text()),
                         index, 0, 2, TARGETS[index][:2], '核对', '其他')
        return book, flag

    def test_model_change_invalidates_cache_and_preserves_prior_conclusion(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); book, flag = self.fixture(root)
            replies = ['keep', 'issue']; calls = []
            def request(prompt):
                calls.append(prompt); verdict = replies.pop(0)
                return json.dumps({'annotation_id':flag['id'], 'verdict':verdict,
                    'source_evidence':'Mara', 'reason':'人物名称在当前句中指向同一个人。', 'suggestion':'核对音译',
                    'suggested_text':TARGETS[0].replace('马拉','玛拉') if verdict == 'issue' else None}), .1
            qwen = {'id':'qwen', 'model':'qwen-27b', 'label':'Qwen 27B'}
            hy = {'id':'hy', 'model':'hy-mt2-30b-q6', 'label':'Hy 30B'}
            notes.review(root, book, pe.engine, request, lambda _:None, reviewer=qwen)
            notes.review(root, book, pe.engine, request, lambda _:None, reviewer=qwen)
            self.assertEqual(len(calls), 1)
            row = notes.review(root, book, pe.engine, request, lambda _:None, reviewer=hy)['items'][0]
            self.assertEqual(len(calls), 2)
            self.assertEqual(row['result']['verdict'], 'issue')
            self.assertEqual(row['reviewer'], hy)
            self.assertEqual(row['review_history'][0]['result']['verdict'], 'keep')
            self.assertEqual(row['review_history'][0]['reviewer'], qwen)
            from post_edit_controller import PostEditController
            self.assertIn('上次 Qwen 27B：建议保留', PostEditController._decorate_annotations([row])[0]['previous_review_text'])
            self.assertFalse((root/'译后校对/story.zh-CN.当前阅读版.txt').exists())

    def burning_fixture(self, root):
        import yaml
        root = root.resolve()
        source_text = ('She thought of the letter burning in her pocket — five lines, no signature, '
                       'and the one name she had spent three years trying to forget.')
        translation = ('她想起口袋里那封正在燃烧的信——短短五行字，没有署名，却写着那个她费了三年时间'
                       '试图忘却的名字。')
        (root / '原文').mkdir(parents=True)
        source = root / '原文/story.txt'
        source.write_text(source_text, encoding='utf-8')
        config = {'schema_version': 1, 'languages': {'source': '英语', 'target': '简体中文'},
                  'source': {'files': ['原文/story.txt'], 'encoding': 'UTF-8'},
                  'chunking': {'segment_mode': 'blank_line'}, 'glossary': {'mode': 'none'},
                  'output': {'directory': '译文', 'filename_rule': 'story.zh-CN.txt'}}
        (root / '翻译任务.yaml').write_text(yaml.safe_dump(config, allow_unicode=True))
        segments, mode, layout = pe.engine.split_source(source_text, 'blank_line')
        output = root / '译文'
        (output / '.hy-direct-chunks').mkdir(parents=True)
        record = {'start': 1, 'end': 1, 'path': '.hy-direct-chunks/1.json',
                  'protocol': pe.engine.OUTPUT_PROTOCOL}
        pe.engine.atomic_json(output / record['path'], {
            'schema_version': pe.engine.STATE_SCHEMA_VERSION, 'protocol': pe.engine.OUTPUT_PROTOCOL,
            'start': 1, 'end': 1, 'segments': [{'id': 1, 'text': translation}],
        })
        pe.engine.atomic_json(output / '.hy-direct-state.json', {
            'schema_version': pe.engine.STATE_SCHEMA_VERSION, 'protocol': pe.engine.OUTPUT_PROTOCOL,
            'source_hash': pe.engine.source_digest([source]),
            'segment_hash': pe.engine.segmentation_digest(segments, mode), 'chunks': [record],
        })
        machine = output / 'story.zh-CN.txt'
        pe.engine.atomic_text(machine, pe.engine.render_layout(layout, {1: translation}, 1, 1))
        book = pe.snapshot(root)
        start = translation.index('正在燃烧')
        flag = notes.add(root, machine, book, pe.engine, paragraphs(machine.read_text()),
                         0, start, start + 4, '正在燃烧', '这里是否被误解成真的着火？', '疑似误译')
        return book, flag, source_text, translation, machine

    def test_long_exact_evidence_is_not_discarded_by_an_arbitrary_ui_limit(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); book, flag, source, translation, _ = self.burning_fixture(root)
            self.assertGreater(len(source), 100)
            revised = translation.replace('正在燃烧的信', '灼人的信')
            def request(_):
                return json.dumps({'annotation_id': flag['id'], 'verdict': 'issue',
                    'reason': '上下文没有信件实际起火的动作，burning 描写信带来的灼迫感。',
                    'source_evidence': source, 'suggestion': '保留隐喻，避免实指着火。',
                    'suggested_text': revised}, ensure_ascii=False), .1
            row = notes.review(root, book, pe.engine, request, lambda _: None)['items'][0]
            self.assertEqual(row['status'], 'reviewed')
            self.assertEqual(row['result']['suggested_text'], revised)
            self.assertEqual(row['result']['source_evidence'], source)
            self.assertNotIn('unverified_candidate', row)

    def test_evidence_id_is_resolved_to_exact_english_by_the_program(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); book, flag, _, translation, _ = self.burning_fixture(root)
            prompt = notes.review_prompt(flag, book)
            packet = json.loads(prompt.split('\n资料：\n', 1)[1])
            option = next(item for item in packet['source_evidence_options'] if 'letter burning' in item['text'])
            revised = translation.replace('正在燃烧的信', '灼人的信')
            def request(_):
                return json.dumps({'annotation_id': flag['id'], 'verdict': 'issue',
                    'reason': 'burning 在这里修饰信件造成的灼迫感。', 'evidence_id': option['id'],
                    'source_evidence': None, 'suggestion': '避免实指着火。',
                    'suggested_text': revised}, ensure_ascii=False), .1
            row = notes.review(root, book, pe.engine, request, lambda _: None)['items'][0]
            self.assertEqual(row['status'], 'reviewed')
            self.assertEqual(row['result']['evidence_id'], option['id'])
            self.assertEqual(row['result']['source_evidence'], option['text'])

    def test_invalid_evidence_keeps_a_display_only_draft_until_human_saves_it(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); book, flag, _, translation, machine = self.burning_fixture(root)
            revised = translation.replace('正在燃烧的信', '灼人的信')
            original = machine.read_bytes()
            def request(_):
                return json.dumps({'annotation_id': flag['id'], 'verdict': 'issue',
                    'reason': '这是一处语境隐喻。', 'source_evidence': '口袋里那封正在燃烧的信',
                    'suggestion': '避免读成真的起火。', 'suggested_text': revised}, ensure_ascii=False), .1
            row = notes.review(root, book, pe.engine, request, lambda _: None)['items'][0]
            self.assertEqual(row['status'], 'failed')
            self.assertIsNone(row['result'])
            self.assertEqual(row['unverified_candidate']['suggested_text'], revised)
            self.assertNotIn('proposed_text', row)
            from post_edit_controller import PostEditController
            decorated = PostEditController._decorate_annotations([row])[0]
            self.assertTrue(decorated['has_unverified_candidate'])
            self.assertEqual(decorated['default_draft'], revised)
            self.assertEqual(machine.read_bytes(), original)
            notes.save_edit(root, flag['id'], decorated['default_draft'], book, pe.engine)
            self.assertEqual(machine.read_bytes(), original, 'saving a human draft must not publish it')
            self.assertEqual(notes.load(root)['items'][0]['proposed_text'], revised)

    def test_keep_still_requires_specific_reason_and_exact_source_evidence(self):
        cases = [('Mara', '无需改动'), ('马拉', '人物名称在当前句中指向同一个人。')]
        for evidence, reason in cases:
            with self.subTest(evidence=evidence), tempfile.TemporaryDirectory() as raw:
                root = Path(raw); book, flag = self.fixture(root)
                def request(_):
                    return json.dumps({'annotation_id': flag['id'], 'verdict': 'keep', 'reason': reason,
                        'source_evidence': evidence, 'suggestion': '', 'suggested_text': None}), .1
                row = notes.review(root, book, pe.engine, request, lambda _: None)['items'][0]
                self.assertEqual(row['status'], 'failed')
                self.assertIsNone(row['result'])
                self.assertEqual(len(row['responses']), 2)

    def test_public_worker_uses_only_selected_local_hy_route_at_temperature_zero(self):
        import native_launcher as native
        from PySide6.QtCore import QSettings
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        calls = []
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                calls.append((request, self.headers.get('Authorization')))
                data = json.loads(request['messages'][0]['content'].split('\n资料：\n')[1])
                reply = {'annotation_id':data['id'], 'verdict':'issue', 'reason':'同一人物的核心译名需要一致。',
                         'source_evidence':'Mara', 'suggestion':'统一音译',
                         'suggested_text':data['translation'].replace('马拉','玛拉')}
                self.send_response(200); self.end_headers()
                self.wfile.write(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(reply)}}]}).encode())
            def log_message(self, *_): pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as raw:
                root=Path(raw).resolve(); book, flag=self.fixture(root)
                owner=native.TranslatorController(workspace=root/'jobs',
                    settings=QSettings(str(root/'s.ini'),QSettings.Format.IniFormat),restore=False)
                owner._job_path=str(root); owner._progress=1
                editor=owner.post_editor
                self.assertEqual(editor.annotationReviewer, 'local_7b')
                url=f'http://127.0.0.1:{server.server_port}/v1'
                pid=owner.providers.save('', 'Local review', url, 'local-hy-test', '', False)
                owner.providers.select('review',pid)
                original=(root/'译文/story.zh-CN.txt').read_bytes()
                def wait():
                    deadline=time.monotonic()+10
                    while editor.busy and time.monotonic()<deadline: QTest.qWait(25)
                    self.assertFalse(editor.busy,editor.status)
                with patch.object(owner,'translation_route',side_effect=AssertionError('Review route must be independent')):
                    editor.reviewAnnotation(flag['id']); wait()
                    self.assertEqual(len(calls),1)
                    self.assertEqual(calls[0][0]['model'],'local-hy-test')
                    self.assertEqual(calls[0][0]['temperature'],0)
                    self.assertIsNone(calls[0][1], 'local review must not read or send DGX credentials')
                    self.assertEqual(editor.annotations[0]['reviewer']['model'],'local-hy-test')
                    second=owner.providers.save('', 'Second review', url, 'second-model', 'fixture-secret', True)
                    editor.annotationReviewer=second
                    editor.reviewAnnotation(flag['id']); wait()
                    self.assertEqual(len(calls),2)
                    self.assertEqual(calls[1][0]['model'],'second-model')
                    self.assertEqual(calls[1][1], 'Bearer fixture-secret')
                    self.assertEqual(editor.annotationReviewer, second)
                    self.assertEqual(len(editor.annotations[0]['review_history']),1)
                self.assertEqual((root/'译文/story.zh-CN.txt').read_bytes(),original)
                self.assertEqual(owner.settings.value('review/profile'), second)
                owner.shutdown()
        finally:
            server.shutdown(); server.server_close()
