from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime/tools'))
import post_edit as pe


SOURCES = [
    'Mara reached North Harbor before dawn. She carried the sealed letter in her coat and waited quietly.',
    'Iven opened the door of the old lighthouse and offered her a cup of tea. The harbor was still asleep.',
    'Mara read the letter again beside the window. The familiar handwriting brought back memories of home.',
    'Iven remained by the window, watching the quiet harbor as the first fishing boats returned from the sea.',
]
TARGETS = [
    '马拉在黎明前来到北港。她把封好的信藏在外套里，静静地等候着。',
    '伊文打开旧灯塔的门，给她端来一杯茶。港湾仍在沉睡之中。',
    '玛拉在窗边又读了一遍信。熟悉的字迹唤起了她对家乡的回忆。',
    '伊文仍站在窗边，望着宁静的港湾，第一批渔船正从海上归来。',
]


def make_book(root, targets=None):
    root = root.resolve()
    e = pe.engine
    targets = targets or TARGETS
    (root / '原文').mkdir(exist_ok=True)
    source = root / '原文/story.txt'
    source.write_text('\n\n'.join(SOURCES), encoding='utf-8')
    config = {'schema_version': 1, 'languages': {'source': '英语', 'target': '简体中文'},
              'source': {'files': ['原文/story.txt'], 'encoding': 'UTF-8'},
              'chunking': {'segment_mode': 'blank_line'}, 'glossary': {'mode': 'none'},
              'output': {'directory': '译文', 'filename_rule': 'story.zh-CN.txt'}}
    (root / '翻译任务.yaml').write_text(yaml.safe_dump(config, allow_unicode=True))
    segments, mode, layout = e.split_source(source.read_text(), 'blank_line')
    output = root / '译文'
    (output / '.hy-direct-chunks').mkdir(parents=True, exist_ok=True)
    record = {'start': 1, 'end': 4, 'path': '.hy-direct-chunks/1.json', 'protocol': e.OUTPUT_PROTOCOL}
    e.atomic_json(output / record['path'], {'schema_version': e.STATE_SCHEMA_VERSION, 'protocol': e.OUTPUT_PROTOCOL,
                                           'start': 1, 'end': 4, 'segments': [{'id': n, 'text': t} for n, t in enumerate(targets, 1)]})
    e.atomic_json(output / '.hy-direct-state.json', {'schema_version': e.STATE_SCHEMA_VERSION, 'protocol': e.OUTPUT_PROTOCOL,
                   'source_hash': e.source_digest([source]), 'segment_hash': e.segmentation_digest(segments, mode), 'chunks': [record]})
    e.atomic_text(output / 'story.zh-CN.txt', e.render_layout(layout, dict(enumerate(targets, 1)), 1, 4))


def response(_prompt):
    if _prompt.startswith('协议 qwen-review-v1'):
        packets = json.loads(_prompt.split('\n资料：\n', 1)[1])
        return json.dumps({'items': [gate_response(p) for p in packets]}, ensure_ascii=False), 0
    if _prompt.startswith('逐处做中英名称对齐'):
        return json.dumps([{'id': 1, 'target': '马拉', 'source': 'Mara'}, {'id': 3, 'target': '玛拉', 'source': 'Mara'}]), 0
    return json.dumps({'preferred': '玛拉', 'reason': '同一人物的音译不同',
                       'evidence': [{'id': 1, 'target': '马拉'}, {'id': 3, 'target': '玛拉'}]}, ensure_ascii=False), 0


def gate_response(p):
    return {'case_id': p['case_id'], 'type': 'PERSON', 'policy': 'ENTITY_EXACT', 'confidence': .96,
            'reason': '同一人物，核对名字的具体位置', 'mentions': [
                {'mention_id': m['mention_id'], 'source_span': p['candidate'],
                 'target_span': '马拉' if m['segment_id'] == 1 else '玛拉',
                 'entity_id': 'Mara', 'mention_form': 'NAME'} for m in p['mentions']]}


class PostEditTests(unittest.TestCase):
    def test_committed_done_event_closes_a_worker_with_a_stuck_tail(self):
        sys.path.insert(0, str(ROOT))
        import native_launcher as native
        from PySide6.QtCore import QProcess, QSettings
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            owner = native.TranslatorController(
                workspace=root / 'jobs',
                settings=QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat),
                restore=False,
            )
            owner._job_path, owner._progress, owner._finished = str(root), 1, True
            editor = owner.post_editor
            process = QProcess(editor)
            process.setProgram(sys.executable)
            process.setArguments(['-u', '-c',
                                  'import json,time; print(json.dumps({"type":"done"}), flush=True); time.sleep(30)'])
            process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            process.readyReadStandardOutput.connect(editor._read)
            process.finished.connect(editor._finished)
            process.errorOccurred.connect(editor._error)
            editor.process, editor._action, editor._buffer = process, 'annotations', ''
            editor._done = editor._stopping = editor._commit_close_scheduled = False
            process.start()
            deadline = time.monotonic() + 5
            while editor.busy and time.monotonic() < deadline:
                QTest.qWait(25)
            self.assertFalse(editor.busy, editor.status)
            self.assertIn('复查结束', editor.status)
            owner.reader_timer.stop(); owner.shutdown(); owner.deleteLater()

    def test_summary_never_displays_contradictory_model_reason(self):
        from post_edit_controller import PostEditController
        row = {'source': 'Storm’s End', 'preferred': '风息堡', 'decision': 'use',
               'group_reason': '不采用风息堡，选择其他名称'}
        self.assertEqual(PostEditController.summary(row, '风息堡'), '采用你指定的译名「风息堡」，不会被模型建议覆盖。')
        row['decision'] = 'uncertain'
        self.assertIn('默认保持原译', PostEditController.summary(row))

    def test_qt_worker_lifecycle_audit_preview_publish_and_explicit_selection(self):
        sys.path.insert(0, str(ROOT))
        import native_launcher as native
        from PySide6.QtCore import QSettings
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        answers = [response('')[0], response('逐处做中英名称对齐')[0]]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                prompt = request['messages'][0]['content']
                content = response(prompt)[0] if prompt.startswith('协议 qwen-review-v1') else answers.pop(0)
                body = json.dumps({'choices': [{'message': {'content': content}}]}).encode()
                self.send_response(200); self.end_headers(); self.wfile.write(body)
            def log_message(self, *_):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw).resolve(); make_book(root)
                settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
                owner = native.TranslatorController(workspace=root / 'jobs', settings=settings, restore=False)
                owner._job_path, owner._progress, owner._finished = str(root), 1, True
                editor = owner.post_editor
                editor.focusTerm = 'Mara'
                def wait():
                    deadline = time.monotonic() + 10
                    while editor.busy and time.monotonic() < deadline:
                        QTest.qWait(25)
                    self.assertFalse(editor.busy, editor.status)
                pid = owner.providers.save('', 'Review fixture',
                    f'http://127.0.0.1:{server.server_port}/v1', 'local-test', 'isolated-test', True)
                self.assertTrue(pid)
                self.assertTrue(owner.providers.select('review', pid))
                with patch.object(owner, 'translation_route', side_effect=AssertionError('Review must use its own profile')):
                    editor.audit(); self.assertTrue(owner.running); wait()
                    self.assertEqual(len(editor.issues), 1, editor.status)
                    self.assertFalse(editor.issues[0]['selected'])
                    editor.selectIssue(editor.issues[0]['id'], True)
                    editor.setPreferred(editor.issues[0]['id'], '玛拉')
                    QTest.qWait(50); wait()
                    self.assertEqual(len(editor.edits), 1, editor.status)
                    self.assertTrue(editor.edits[0]['accepted'], 'strict name-only edits should be ready for one confirmation')
                    editor.selectEdit(1, False)
                    editor.publish()
                    self.assertFalse(editor.busy, 'unaccepted preview must not publish')
                    editor.selectEdit(1, True)
                    editor.publish(); wait()
                    self.assertTrue(Path(editor.output).is_file(), editor.status)
                    self.assertEqual(len(answers), 0)
                owner.shutdown(); owner.reader_timer.stop(); owner.deleteLater()
        finally:
            server.shutdown(); server.server_close()

    def test_prepare_groups_names_honors_preference_and_only_selects_name_changes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            directory = root / '译后校对'; directory.mkdir()
            pe.engine.atomic_json(directory / '偏好译名.json', {'Mara': '玛拉'})
            calls = []
            def request(prompt):
                calls.append(prompt)
                if prompt.startswith('把同一本书'):
                    self.assertIn('variant_paragraph_counts', prompt)
                    self.assertIn('user_preference', prompt)
                    return json.dumps([{'id': pe.fingerprint('Mara')[:16], 'decision': 'use', 'preferred': '马拉', 'reason': '已有名称'}]), 0
                if prompt.startswith('请对照原文校订'):
                    return json.dumps({'text': TARGETS[0].replace('马拉', '玛拉')}), 0
                return response(prompt)
            draft = pe.prepare_review(root, request, lambda _: None, 'Mara')
            self.assertEqual(len(calls), 4)
            self.assertEqual(draft['ready'], 1)
            report = pe.engine.load_json(directory / '检查报告.json', {})
            self.assertEqual(report['issues'][0]['preferred'], '玛拉')
            self.assertTrue(report['issues'][0]['user_preference'])
            row = draft['edits'][0]
            self.assertTrue(pe.name_only_edit(row, row['after']))
            self.assertFalse(pe.name_only_edit(row, row['after'].replace('静静地', '耐心地')))
            calls.clear()
            pe.prepare_review(root, request, lambda _: None, 'Mara')
            self.assertEqual(calls, [], 'completed grouped review and previews must resume without requests')
            pe.engine.atomic_json(directory / '偏好译名.json', {'Mara': '玛菈'})
            with self.assertRaisesRegex(ValueError, '偏好已改变'):
                pe.publish(root, [1])

    def test_audit_model_change_rechecks_and_archives_old_report(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            calls = []
            def request(prompt):
                calls.append(prompt)
                return response(prompt)
            first = dict(id='profile_a', api_base='https://example.test/v1', model='one', temperature=0)
            old = pe.audit(root, request, lambda _: None, 'Mara', reviewer=first)
            self.assertGreater(len(calls), 0)
            calls.clear()
            pe.audit(root, request, lambda _: None, 'Mara', reviewer=dict(first, label='renamed'))
            self.assertEqual(calls, [], 'a display name change should preserve a completed review')
            new = pe.audit(root, request, lambda _: None, 'Mara', reviewer=dict(first, model='two'))
            self.assertGreater(len(calls), 0, 'a different model must recheck the evidence')
            self.assertNotEqual(old['stamp'], new['stamp'])
            history = list((root / '译后校对').glob('历史检查-*.json'))
            self.assertEqual(len(history), 1)
            self.assertEqual(pe.engine.load_json(history[0], {})['stamp'], old['stamp'])

    def test_uncertain_group_keeps_user_preference_and_clears_old_draft(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            report = pe.audit(root, response, lambda _: None, 'Mara')
            pe.preview(root, lambda _: (json.dumps({'text': TARGETS[0].replace('马拉', '玛拉')}), 0), lambda _: None,
                       [{'id': report['issues'][0]['id'], 'preferred': '玛拉'}])
            pe.engine.atomic_json(root / '译后校对/偏好译名.json', {'Mara': '玛菈'})
            result = pe.prepare_review(root, lambda _: ('not valid JSON', 0), lambda _: None, 'Mara')
            self.assertEqual(result['edits'], [])
            issue = pe.engine.load_json(root / '译后校对/检查报告.json', {})['issues'][0]
            self.assertEqual(issue['decision'], 'uncertain')
            self.assertEqual(issue['preferred'], '玛菈')
            self.assertEqual(pe.engine.load_json(root / '译后校对/校订预览.json', {})['edits'], [])

    def test_audit_preview_publish_keeps_original_and_other_paragraphs_exact(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            make_book(root)
            original = {p: p.read_bytes() for p in root.rglob('*') if p.is_file()}
            report = pe.audit(root, response, lambda _: None, 'Mara')
            self.assertEqual(len(report['issues']), 1)
            choice = [{'id': report['issues'][0]['id'], 'preferred': '玛拉'}]
            requests = []
            def repair(prompt):
                requests.append(prompt)
                return json.dumps({'text': TARGETS[0].replace('马拉', '玛拉')}, ensure_ascii=False), 0
            preview = pe.preview(root, repair, lambda _: None, choice)
            self.assertEqual([r['id'] for r in preview['edits']], [1])
            self.assertEqual(len(requests), 0, 'position-verified name changes must not ask a model to rewrite prose')
            copied = pe.publish(root, [1], export_copy=True)
            self.assertIn('.校订版-', Path(copied['output']).name)
            self.assertTrue(copied['export_copy'])
            exported = pe.publish(root, [1])
            self.assertTrue(Path(exported['output']).name.endswith('.当前阅读版.txt'))
            self.assertFalse(exported['export_copy'])
            content = Path(exported['output']).read_text()
            self.assertEqual(content, (root / '译文/story.zh-CN.txt').read_text().replace('马拉', '玛拉'))
            for path, data in original.items():
                self.assertEqual(path.read_bytes(), data, str(path))
            self.assertEqual(json.loads(next((root / '译后校对').glob('确认译名-*.json')).read_text())['entries'][0]['source'], 'Mara')
            current_bytes = Path(exported['output']).read_bytes()
            self.assertEqual(Path(exported['output']).read_bytes(), current_bytes)
            self.assertEqual(Path(copied['output']).read_bytes(), current_bytes)
            applied = pe.engine.load_json(root / '译后校对/校订预览.json', {})
            self.assertEqual(applied['status'], 'applied')
            self.assertEqual(applied['applied_ids'], [1])

    def test_choice_maps_every_exact_occurrence_not_only_sampled_evidence(self):
        book = {
            'stamp': 'book', 'end': 8, 'total': 8,
            'segments': [f'Mara arrived at place {number}.' for number in range(1, 9)],
            'translated': {number: ('马拉' if number % 2 else '玛拉') + f'到达了第{number}处。'
                           for number in range(1, 9)},
            'confirmed_variants': {},
        }
        issue = {
            'id': 'mara', 'source': 'Mara', 'preferred': '玛拉',
            'variants': ['马拉', '玛拉'], 'match_case': True,
            'policy': 'ENTITY_EXACT',
            'evidence': [{'id': 1, 'target': '马拉'}, {'id': 2, 'target': '玛拉'}],
        }
        report = {'book_stamp': 'book', 'revision': pe.REVISION, 'issues': [issue]}
        diagnostics = {}
        plan = pe.correction_plan(book, report, [{'id': 'mara', 'preferred': '玛拉'}], diagnostics)
        self.assertEqual([row['id'] for row in plan], [1, 3, 5, 7])
        self.assertEqual(diagnostics['source_mentions'], 8)
        self.assertEqual(diagnostics['mapped_mentions'], 8)
        self.assertEqual(diagnostics['unmapped_segments'], [])
        self.assertTrue(all(pe.exact_name_repair(row).startswith('玛拉') for row in plan))

    def test_a_new_preference_can_replace_a_previously_applied_name_choice(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            report = pe.audit(root, response, lambda _: None, 'Mara')
            directory = root / '译后校对'
            def no_rewrite(_):
                self.fail('exact name replacement should be local')
            pe.engine.atomic_json(directory / '偏好译名.json', {'Mara': '玛拉'})
            first = pe.preview(root, no_rewrite, lambda _: None,
                               [{'id': report['issues'][0]['id'], 'preferred': '玛拉'}])
            first_output = Path(pe.publish(root, [row['id'] for row in first['edits']])['output'])
            self.assertEqual(first_output.read_text().count('玛拉'), 2)
            pe.engine.atomic_json(directory / '偏好译名.json', {'Mara': '马拉'})
            second = pe.preview(root, no_rewrite, lambda _: None,
                                [{'id': report['issues'][0]['id'], 'preferred': '马拉'}])
            self.assertEqual([row['id'] for row in second['edits']], [1, 3])
            second_output = Path(pe.publish(root, [row['id'] for row in second['edits']])['output'])
            self.assertEqual(second_output.read_text().count('马拉'), 2)
            self.assertEqual(second_output.read_text().count('玛拉'), 0)
            latest = max(directory.glob('校订记录-*.json'), key=lambda path: path.stat().st_mtime_ns)
            entries = {row['source']: row['target'] for row in json.loads(latest.read_text())['entries']}
            self.assertEqual(entries['Mara'], '马拉')

    def test_false_or_single_variant_evidence_cannot_become_issue(self):
        candidate = {'source': 'Mara', 'mentions': 2, 'samples': [
            {'id': 1, 'source_text': SOURCES[0], 'translation': TARGETS[0]},
            {'id': 3, 'source_text': SOURCES[2], 'translation': TARGETS[2]}]}
        bad = {'preferred': '玛拉', 'evidence': [{'id': 1, 'target': '凭空编造'}, {'id': 3, 'target': '玛拉'}]}
        with self.assertRaisesRegex(ValueError, '不是现有译文'):
            pe.validate_issue(json.dumps(bad), candidate)
        bad['evidence'][0] = {'id': 77, 'target': '马拉'}
        with self.assertRaisesRegex(ValueError, '段号无效'):
            pe.validate_issue(json.dumps(bad), candidate)

    def test_titles_and_contractions_are_not_name_drift(self):
        for term in ['White', 'I’ll', "I'll", "It’s", "Cressen’s", 'About', 'Between']:
            self.assertFalse(pe.name_candidate(term), term)
        candidate = {'source': 'Davos', 'mentions': 2, 'samples': [
            {'id': 1, 'source_text': 'Davos came.', 'translation': '戴佛斯来了。'},
            {'id': 2, 'source_text': 'Davos waited.', 'translation': '戴佛斯爵士在等。'}]}
        raw = {'preferred': '戴佛斯', 'reason': '不同译法', 'evidence': [{'id': 1, 'target': '戴佛斯'}, {'id': 2, 'target': '戴佛斯爵士'}]}
        self.assertIsNone(pe.validate_issue(json.dumps(raw), candidate))

    def test_surname_ambiguity_and_lowercase_generic_nouns_excluded(self):
        self.assertTrue(pe.ambiguous_fragment('Baratheon', ['Stannis Baratheon arrived.', 'Renly Baratheon waited.']))
        self.assertFalse(pe.ambiguous_fragment('Mara', ['Lady Mara arrived.', 'Mara waited with Lord Iven.']))
        book = {'segments': ['The Citadel sent a letter.', 'The Citadel opened its gates.', 'This is a lonely citadel.'],
                'translated': {1: '学城送来一封信。', 2: '学城打开了大门。', 3: '这里是一座孤独的城堡。'}, 'end': 3, 'total': 3}
        rows, _ = pe.candidates(book)
        row = next(r for r in rows if r['source'] == 'Citadel')
        self.assertEqual([s['id'] for s in row['samples']], [1, 2])

    def test_alignment_discards_unmatched_pair_but_preserves_sufficient_evidence(self):
        issue = {'source': 'Mara', 'preferred': '玛拉', 'evidence': [
            {'id': 1, 'source_text': 'Mara arrived.', 'target': '马拉'},
            {'id': 2, 'source_text': 'Mara waited.', 'target': '玛拉'},
            {'id': 3, 'source_text': 'Mara met Iven.', 'target': '伊文'}]}
        raw = json.dumps([{'id': 1, 'target': '马拉', 'source': 'Mara'},
                          {'id': 2, 'target': '玛拉', 'source': 'Mara'},
                          {'id': 3, 'target': '伊文', 'source': 'Iven'}])
        result = pe.aligned_issue(raw, issue)
        self.assertEqual([r['id'] for r in result['evidence']], [1, 2])
        self.assertEqual(result['variants'], ['玛拉', '马拉'])

    def test_rejected_semantic_confirmation_does_not_create_suggestion(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            def reject(prompt):
                return (json.dumps([{'id': 1, 'target': '马拉', 'source': 'Iven'}, {'id': 3, 'target': '玛拉', 'source': 'Mara'}]), 0) if prompt.startswith('逐处做中英名称对齐') else response(prompt)
            report = pe.audit(root, reject, lambda _: None, 'Mara')
            self.assertEqual(report['issues'], [])
            self.assertEqual(report['status'], 'completed')

    def test_review_is_resumable_and_changed_translation_blocks_old_preview(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            report = pe.audit(root, response, lambda _: None, 'Mara')
            def unexpected(_):
                self.fail('already reviewed names should not call Hy')
            pe.audit(root, unexpected, lambda _: None, 'Mara')
            make_book(root, [TARGETS[0].replace('静静地', '耐心地'), *TARGETS[1:]])
            with self.assertRaisesRegex(ValueError, '译文已有新内容'):
                pe.preview(root, unexpected, lambda _: None, [{'id': report['issues'][0]['id'], 'preferred': '玛拉'}])

    def test_manual_file_change_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            path = root / '译文/story.zh-CN.txt'
            path.write_text('我的手工校订')
            with self.assertRaisesRegex(ValueError, '可能有手工修改'):
                pe.snapshot(root)
            self.assertEqual(path.read_text(), '我的手工校订')

    def test_snapshot_accepts_only_equivalent_unicode_path_spelling(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / '温泉が'
            root.mkdir()
            root = root.resolve()
            make_book(root)
            source = root / '原文/story.txt'
            state_path = root / '译文/.hy-direct-state.json'
            state = json.loads(state_path.read_text(encoding='utf-8'))
            nfc = unicodedata.normalize('NFC', str(source))
            nfd = unicodedata.normalize('NFD', str(source))
            recorded = nfd if str(source) != nfd else nfc
            self.assertNotEqual(recorded, str(source))
            digest = hashlib.sha256()
            digest.update(recorded.encode('utf-8'))
            digest.update(b'\0')
            digest.update(source.read_bytes())
            digest.update(b'\0')
            state['source_hash'] = digest.hexdigest()
            state['source_files'] = [recorded]
            state_path.write_text(json.dumps(state), encoding='utf-8')

            self.assertEqual(pe.snapshot(root)['end'], len(SOURCES))
            source.write_text(source.read_text() + '\nchanged', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '原文或段落映射'):
                pe.snapshot(root)

    def test_ordinary_adjective_filtered_but_explicit_phrase_can_be_checked(self):
        book = {'segments': ['White ravens arrive with Mara at White Harbor.'] * 3,
                'translated': {1: '白鸦和玛拉到了白港。', 2: '白鸦和玛拉到了白港。', 3: '白信鸽和玛拉到了白港。'}, 'end': 3, 'total': 3}
        rows, _ = pe.candidates(book)
        self.assertNotIn('white', [r['source'].casefold() for r in rows])
        rows, _ = pe.candidates(book, 'white ravens')
        self.assertEqual(rows[0]['source'], 'white ravens')

    def test_network_failure_does_not_claim_audit_complete(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            def failure(_):
                raise TimeoutError('测试超时')
            with self.assertRaises(TimeoutError):
                pe.audit(root, failure, lambda _: None, 'Mara')
            self.assertFalse((root / '译后校对/校订预览.json').exists())

    def test_unapproved_and_numeric_or_multiline_edits_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            report = pe.audit(root, response, lambda _: None, 'Mara')
            book = pe.snapshot(root)
            row = pe.correction_plan(book, report, [{'id': report['issues'][0]['id'], 'preferred': '玛拉'}])[0]
            for after in ['玛拉丢失所有其他信息', TARGETS[0].replace('马拉', '玛拉') + '\n多出一段', TARGETS[0].replace('马拉', '玛拉') + '123']:
                with self.assertRaises(ValueError):
                    pe.validate_repair(json.dumps({'text': after}), row, book)
            with self.assertRaises(ValueError):
                pe.publish(root, [])


class PreferredNameSchedulingTests(unittest.TestCase):
    """Saving a preference decides the preview; the status wording never does."""

    def editor(self, root):
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / 'runtime/lib'))
        from PySide6.QtCore import QObject, QSettings, Signal
        from PySide6.QtWidgets import QApplication
        from locale_service import LocaleService
        from post_edit_controller import PostEditController
        QApplication.instance() or QApplication([])

        class Owner(QObject):
            changed = Signal()

            def __init__(self):
                super().__init__()
                self.settings = QSettings(str(root / 'settings.ini'), QSettings.Format.IniFormat)
                self._job_path = str(root)
                # The category picker is localized through the locale service.
                self.locale = LocaleService(self.settings, system_locale='zh_CN')

        return PostEditController(Owner())

    def test_successful_save_writes_the_preference_and_schedules_preview(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / '译后校对').mkdir()
            editor = self.editor(root)
            editor._issues = [{'id': 'n1', 'source': 'Mara', 'preferred': ''}]
            with patch.object(type(editor), 'notify'), \
                    patch.object(type(editor), '_schedule_preview') as schedule:
                editor.setPreferred('n1', '玛拉')
            schedule.assert_called_once()
            saved = json.loads((root / '译后校对/偏好译名.json').read_text(encoding='utf-8'))
            self.assertEqual({'Mara': '玛拉'}, saved)
            self.assertTrue(editor.issues[0]['selected'])

    def test_failed_save_keeps_the_raw_error_and_skips_preview(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            directory = root / '译后校对'
            directory.mkdir()
            (directory / '偏好译名.json').mkdir()  # an unwritable preference path
            editor = self.editor(root)
            editor._issues = [{'id': 'n1', 'source': 'Mara', 'preferred': ''}]
            with patch.object(type(editor), 'notify'), \
                    patch.object(type(editor), '_schedule_preview') as schedule:
                editor.setPreferred('n1', '玛拉')
            schedule.assert_not_called()
            self.assertEqual('译名偏好未保存', editor.status)
            self.assertTrue(editor.statusDetail, '原始文件错误应保存在独立详情中')
            self.assertNotEqual(True, editor.issues[0].get('selected'))

    def test_preview_decision_ignores_the_status_wording(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / '译后校对').mkdir()
            editor = self.editor(root)
            editor._issues = [{'id': 'n1', 'source': 'Mara', 'preferred': ''},
                              {'id': 'n2', 'source': 'Iven', 'preferred': ''}]
            with patch.object(type(editor), 'notify'):
                with patch.object(type(editor), '_save_preference', return_value=True), \
                        patch.object(type(editor), '_schedule_preview') as schedule:
                    editor._status = '译名偏好未保存：磁盘已满'
                    editor.setPreferred('n1', '玛拉')
                schedule.assert_called_once()
                with patch.object(type(editor), '_save_preference', return_value=False), \
                        patch.object(type(editor), '_schedule_preview') as schedule:
                    editor._status = '已记住你的译名，正在自动重算全书对应段落。'
                    editor.setPreferred('n2', '伊文')
                schedule.assert_not_called()

    def test_controller_exposes_the_category_picker_with_stable_values(self):
        with tempfile.TemporaryDirectory() as raw:
            editor = self.editor(Path(raw))
            meta = type(editor).staticMetaObject
            index = meta.indexOfProperty('categoryOptions')
            self.assertGreaterEqual(index, 0, 'QML cannot resolve backend.postEditor.categoryOptions')
            self.assertEqual('QVariantList', str(meta.property(index).typeName()))
            options = editor.categoryOptions
            self.assertEqual(['mistranslation', 'names_places', 'omission', 'numbers_time',
                              'tone', 'other'], [item['value'] for item in options])
            self.assertEqual(['疑似误译', '人名 / 地名', '漏译 / 多译', '数字 / 时间',
                              '语气 / 表达', '其他'], [item['text'] for item in options])

    def test_decorated_annotations_carry_category_and_error_identity(self):
        sys.path.insert(0, str(ROOT))
        sys.path.insert(0, str(ROOT / 'runtime/lib'))
        from post_edit_controller import PostEditController
        row = {'id': 'a1', 'category': '人名 / 地名', 'status': 'failed',
               'error': '复查的原文证据并非逐字摘录', 'result': None}
        decorated = PostEditController._decorate_annotations([row])[0]
        self.assertEqual('names_places', decorated['category_id'])
        self.assertEqual('人名 / 地名', decorated['category_label'])
        self.assertEqual('annotation.evidence_not_exact', decorated['error_code'])
        self.assertEqual('evidence', decorated['error_bucket'])
        unknown = PostEditController._decorate_annotations(
            [{'id': 'a2', 'category': '语气问题', 'error': '未来版本的错误', 'result': None}])[0]
        self.assertEqual('', unknown['category_id'])
        self.assertEqual('语气问题', unknown['category_label'])
        self.assertEqual('', unknown['error_bucket'])


if __name__ == '__main__':
    unittest.main()
