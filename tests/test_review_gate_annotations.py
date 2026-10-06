from __future__ import annotations
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from test_post_edit import pe, make_book, response, gate_response, TARGETS
import review_gate as gate
import annotations as notes
from reader import paragraphs, paragraph_spans


def wait_reader(app, owner, timeout=4):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if not any(column.loading or getattr(column, '_refresh_token', '')
                   for column in owner.readingColumns):
            app.processEvents()
            return
        time.sleep(.01)
    raise AssertionError('reader background load did not finish')


def set_translation_extent(root, end):
    """Rewrite the synthetic machine checkpoint to one contiguous partial extent."""
    root = root.resolve()
    source = root / '原文/story.txt'
    segments, mode, layout = pe.engine.split_source(source.read_text(), 'blank_line')
    output = root / '译文'
    chunk = output / '.hy-direct-chunks/1.json'
    record = {'start': 1, 'end': end, 'path': '.hy-direct-chunks/1.json',
              'protocol': pe.engine.OUTPUT_PROTOCOL}
    pe.engine.atomic_json(chunk, {
        'schema_version': pe.engine.STATE_SCHEMA_VERSION,
        'protocol': pe.engine.OUTPUT_PROTOCOL,
        'start': 1, 'end': end,
        'segments': [{'id': number, 'text': TARGETS[number - 1]}
                     for number in range(1, end + 1)],
    })
    pe.engine.atomic_json(output / '.hy-direct-state.json', {
        'schema_version': pe.engine.STATE_SCHEMA_VERSION,
        'protocol': pe.engine.OUTPUT_PROTOCOL,
        'source_hash': pe.engine.source_digest([source]),
        'segment_hash': pe.engine.segmentation_digest(segments, mode),
        'chunks': [record],
    })
    partial, final = output / 'story.zh-CN.txt.partial', output / 'story.zh-CN.txt'
    visible = final if end == len(segments) else partial
    other = partial if visible == final else final
    if other.exists():
        other.unlink()
    pe.engine.atomic_text(
        visible,
        pe.engine.render_layout(layout, dict(enumerate(TARGETS[:end], 1)), 1, end),
    )
    return visible


class GateTests(unittest.TestCase):
    def test_year_and_different_entities_cannot_pass_even_with_positive_policy(self):
        candidate = {'source': 'YEAR', 'samples': [
            {'id': 1, 'source_text': 'YEAR 102', 'translation': '第102年'},
            {'id': 2, 'source_text': 'YEAR 1145', 'translation': '第1145年'}]}
        p = gate.packet(candidate)
        row = {'case_id': p['case_id'], 'type': 'STRUCTURAL_LABEL', 'policy': 'TEMPLATE_EXACT', 'confidence': .99,
               'mentions': [{'mention_id': m['mention_id'], 'source_span': 'YEAR', 'target_span': m['translation'],
                             'entity_id': 'year', 'mention_form': 'OTHER'} for m in p['mentions']]}
        self.assertEqual(gate.validate(row, p)['policy'], 'TEMPLATE_WITH_SLOTS')
        row['type'] = 'PERSON'; row['policy'] = 'ENTITY_EXACT'
        row['mentions'][0]['entity_id'] = 'Dan Wells'
        row['mentions'][1]['entity_id'] = 'Dire Dan'
        self.assertEqual(gate.validate(row, p)['policy'], 'REJECT')
        row['mentions'][0]['target_span'] = '编造的译文'
        with self.assertRaisesRegex(ValueError, '逐字证据'):
            gate.validate(row, p)

    def test_valid_siblings_checkpoint_and_only_bad_ids_retry(self):
        cs = [{'source': 'Mara', 'samples': [{'id': 1, 'source_text': 'Mara came.', 'translation': '马拉来了。'},
                                            {'id': 3, 'source_text': 'Mara waited.', 'translation': '玛拉等候。'}]},
              {'source': 'Wouldn’t', 'samples': [{'id': 5, 'source_text': 'Wouldn’t you?', 'translation': '你不会吗？'}]}]
        calls, state = [], {}
        def request(prompt):
            packets = json.loads(prompt.split('\n资料：\n')[1]); calls.append(packets)
            return json.dumps({'items': [gate_response(p) for p in packets if p['candidate'] == 'Mara']}), 0
        result = gate.gate_candidates(cs, state, request, lambda: None, lambda _: None)
        self.assertIsNotNone(result['Mara']); self.assertIsNone(result['Wouldn’t'])
        self.assertEqual([len(p) for p in calls], [2, 1])
        self.assertEqual(len(state['items']), 1)

    def test_heading_variables_stay_in_full_distinct_titles(self):
        texts = ['YEAR 102: THE MOST SIGNIFICANT TEXT', 'YEAR 1145: THE GREAT LOSS', 'THE FIRST WEEK IN HELL'] * 2
        book = {'segments': texts, 'translated': dict(enumerate(texts, 1)), 'end': 6, 'total': 6}
        rows, _ = pe.candidates(book)
        names = {r['source'] for r in rows}
        self.assertIn(texts[0], names); self.assertIn(texts[1], names); self.assertIn(texts[2], names)
        self.assertNotIn('YEAR', names); self.assertNotIn('WEEK IN HELL', names)

    def test_full_name_conflict_vetoes_model_claiming_same_person(self):
        given = gate.packet({'source': 'Dan', 'samples': [
            {'id': 1, 'source_text': 'Dan Wells wrote it.', 'translation': '丹·威尔斯写的。'},
            {'id': 2, 'source_text': 'Dire Dan was gone.', 'translation': '迪尔·丹消失了。'}]})
        row = {'case_id': given['case_id'], 'type': 'PERSON', 'policy': 'ENTITY_EXACT', 'confidence': .99,
               'mentions': [{'mention_id': m['mention_id'], 'source_span': 'Dan', 'target_span': '丹',
                             'entity_id': 'Dan Wells', 'mention_form': 'NAME'} for m in given['mentions']]}
        result = gate.validate(row, given)
        self.assertEqual(result['policy'], 'REVIEW')
        self.assertIn('Dan Wells', result['guard']); self.assertIn('Dire Dan', result['guard'])

    def test_full_and_short_names_not_issue_regardless_preferred(self):
        candidate = {'source': 'Borges', 'samples': [
            {'id': 1, 'source_text': 'Jorge Luis Borges', 'translation': '豪尔赫·路易斯·博尔赫斯'},
            {'id': 2, 'source_text': 'Borges', 'translation': '博尔赫斯'}], 'mentions': 2}
        raw = {'preferred': '豪尔赫·路易斯·博尔赫斯', 'evidence': [
            {'id': 1, 'target': '豪尔赫·路易斯·博尔赫斯'}, {'id': 2, 'target': '博尔赫斯'}]}
        self.assertIsNone(pe.validate_issue(json.dumps(raw), candidate))

    def test_exact_edit_changes_only_confirmed_occurrence(self):
        row = {'before': '玛拉提到了马拉。马拉也在场。', 'rules': [
            {'start': 5, 'end': 7, 'previous': ['马拉'], 'target': '玛菈'}]}
        self.assertTrue(pe.name_only_edit(row, '玛拉提到了玛菈。马拉也在场。'))
        self.assertFalse(pe.name_only_edit(row, '玛拉提到了玛菈。玛菈也在场。'))


class AnnotationTests(unittest.TestCase):
    def test_current_reading_edition_follows_new_translation_tail_and_remains_annotatable(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            machine = set_translation_extent(root, 2)
            book = pe.snapshot(root)
            rows = paragraphs(machine.read_text())
            flag = notes.add(root, machine, book, pe.engine, rows, 0, 0, 2, '马拉', '统一音译', '人名')
            revised = TARGETS[0].replace('马拉', '玛拉')
            notes.save_edit(root, flag['id'], revised, book, pe.engine)
            current = Path(notes.publish_edits(root, [flag['id']], book, pe.engine)['output'])
            self.assertEqual(len(paragraphs(current.read_text())), 2)

            machine = set_translation_extent(root, 4)
            grown = pe.snapshot(root)
            # Even before the UI refreshes the tail, the visible prefix is a valid
            # edition and a newly selected annotation must not be rejected.
            old_rows = paragraphs(current.read_text())
            start = old_rows[1].index('旧灯塔')
            saved = notes.add(root, current, grown, pe.engine, old_rows, 1, start, start + 3,
                              '旧灯塔', '检查描述', '其他')
            self.assertEqual(saved['segment_id'], 2)

            self.assertTrue(notes.sync_current_edition(root, current, grown, pe.engine))
            live_rows = paragraphs(current.read_text())
            self.assertEqual(len(live_rows), 4)
            self.assertEqual(live_rows[0], revised)
            self.assertEqual(live_rows[-1], TARGETS[-1])
            translations, edits, visible_end = notes.edition_state(root, current, grown, pe.engine)
            self.assertEqual(visible_end, 4)
            self.assertEqual(translations[1], revised)
            self.assertEqual(len(edits), 1)
            self.assertEqual(machine.read_text(), '\n\n'.join(TARGETS) + '\n')

    def test_controller_live_refresh_extends_current_edition_without_losing_position(self):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        import native_launcher as native
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            machine = set_translation_extent(root, 2)
            book = pe.snapshot(root)
            rows = paragraphs(machine.read_text())
            flag = notes.add(root, machine, book, pe.engine, rows, 0, 0, 2, '马拉', '', '人名')
            revised = TARGETS[0].replace('马拉', '玛拉')
            notes.save_edit(root, flag['id'], revised, book, pe.engine)
            current = Path(notes.publish_edits(root, [flag['id']], book, pe.engine)['output'])

            settings = QSettings(str(root / 's.ini'), QSettings.Format.IniFormat)
            owner = native.TranslatorController(workspace=root / 'jobs', settings=settings, restore=False)
            owner._job_path = str(root); owner._output_path = str(machine); owner._progress = .5
            self.assertTrue(owner.openReadingFile(str(current)))
            self.assertFalse(owner.readingExternal)
            owner.saveReadingPosition(1)

            owner._output_path = str(set_translation_extent(root, 4))
            owner._progress = 1
            owner._refresh_reader()
            wait_reader(app, owner)
            self.assertEqual(owner.reader.count, 4)
            self.assertEqual(owner.reader.rows[0], revised)
            self.assertEqual(owner.reader.rows[-1], TARGETS[-1])
            self.assertEqual(owner.savedReadingPosition, 1)
            owner.reader_timer.stop(); owner.shutdown()

    def test_saved_manual_edit_updates_current_reading_edition_and_copy_is_optional(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            book = pe.snapshot(root); canonical = root / '译文/story.zh-CN.txt'
            canonical_bytes = canonical.read_bytes()
            flag = notes.add(root, canonical, book, pe.engine, paragraphs(canonical.read_text()),
                             0, 0, 2, '马拉', '与后文统一', '人名')
            revised = TARGETS[0].replace('马拉', '玛拉')
            notes.save_edit(root, flag['id'], revised, book, pe.engine)
            self.assertEqual(canonical.read_bytes(), canonical_bytes)

            applied = notes.publish_edits(root, [flag['id']], book, pe.engine)
            current = Path(applied['output'])
            self.assertEqual(current.name, 'story.zh-CN.当前阅读版.txt')
            self.assertIn(revised, current.read_text())
            self.assertEqual(canonical.read_bytes(), canonical_bytes)
            saved = notes.load(root)['items'][0]
            self.assertEqual(saved['status'], 'resolved')
            self.assertEqual(saved['applied_output'], str(current))

            rows = paragraphs(current.read_text())
            start = rows[0].index('静静地')
            second = notes.add(root, current, book, pe.engine, rows, 0, start, start + 3,
                               '静静地', '改成安静等待', '其他')
            second_text = revised.replace('静静地等候着', '安静等待着')
            notes.save_edit(root, second['id'], second_text, book, pe.engine)
            current_bytes = current.read_bytes()
            copied = notes.publish_edits(root, [second['id']], book, pe.engine, export_copy=True)
            copy_path = Path(copied['output'])
            self.assertIn('.校订版-', copy_path.name)
            self.assertEqual(current.read_bytes(), current_bytes)
            self.assertIn(second_text, copy_path.read_text())
            self.assertEqual(canonical.read_bytes(), canonical_bytes)

    def test_manual_edit_is_not_blocked_by_sequential_name_choice_history(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            report = pe.audit(root, response, lambda _: None, 'Mara')
            directory = root / '译后校对'
            pe.engine.atomic_json(directory / '偏好译名.json', {'Mara': '玛拉'})
            first = pe.preview(root, lambda _: self.fail('exact name edit asked for a rewrite'),
                               lambda _: None, [{'id': report['issues'][0]['id'], 'preferred': '玛拉'}])
            current = Path(pe.publish(root, [row['id'] for row in first['edits']])['output'])
            pe.engine.atomic_json(directory / '偏好译名.json', {'Mara': '马拉'})
            second = pe.preview(root, lambda _: self.fail('exact name edit asked for a rewrite'),
                                lambda _: None, [{'id': report['issues'][0]['id'], 'preferred': '马拉'}])
            current = Path(pe.publish(root, [row['id'] for row in second['edits']])['output'])

            book = pe.snapshot(root)
            rows = paragraphs(current.read_text())
            start = rows[1].index('旧灯塔')
            flag = notes.add(root, current, book, pe.engine, rows, 1, start, start + 3,
                             '旧灯塔', '更准确的描述', '其他')
            revised = TARGETS[1].replace('旧灯塔', '老灯塔')
            notes.save_edit(root, flag['id'], revised, book, pe.engine)
            applied = notes.publish_edits(root, [flag['id']], book, pe.engine)

            self.assertIn(revised, Path(applied['output']).read_text())
            saved = notes.load(root)['items'][0]
            self.assertEqual(saved['status'], 'resolved')
            latest = max(directory.glob('校订记录-*.json'), key=lambda path: path.stat().st_mtime_ns)
            entries = {row['source']: row['target'] for row in json.loads(latest.read_text())['entries']}
            self.assertEqual(entries['Mara'], '马拉')

    def test_qt_annotation_apply_hot_loads_reader_and_persists_export_choice(self):
        from PySide6.QtCore import QSettings
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        import native_launcher as native
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            canonical_bytes = (root / '译文/story.zh-CN.txt').read_bytes()
            settings_path = root / 's.ini'
            settings = QSettings(str(settings_path), QSettings.Format.IniFormat)
            owner = native.TranslatorController(workspace=root / 'jobs', settings=settings, restore=False)
            owner._job_path = str(root); owner._output_path = str(root / '译文/story.zh-CN.txt'); owner._progress = 1
            owner._refresh_reader()
            wait_reader(app, owner)
            editor = owner.post_editor
            owner.saveReadingPosition(2)
            self.assertFalse(editor.exportCopy)
            editor.exportCopy = True
            self.assertTrue(settings.value('review/export_copy', False, type=bool))
            editor.exportCopy = False
            self.assertTrue(editor.addAnnotation(0, 0, 2, '马拉', '统一音译', '人名'))
            identity = editor.annotations[0]['id']
            revised = TARGETS[0].replace('马拉', '玛拉')
            self.assertTrue(editor.saveAnnotationEdit(identity, revised))
            signals = []
            editor.editionApplied.connect(lambda: signals.append(True))
            editor.publishAnnotation(identity)
            deadline = __import__('time').monotonic() + 10
            while editor.busy and __import__('time').monotonic() < deadline:
                QTest.qWait(25)
            QTest.qWait(100)
            self.assertFalse(editor.busy, editor.status)
            self.assertTrue(signals, editor.status)
            self.assertTrue(owner._read_file.endswith('.当前阅读版.txt'))
            self.assertTrue(owner._reader_identity.startswith('book:'))
            self.assertEqual(owner.savedReadingPosition, 2)
            self.assertIn(revised, '\n'.join(owner.reader.rows))
            self.assertEqual((root / '译文/story.zh-CN.txt').read_bytes(), canonical_bytes)
            owner.reader_timer.stop(); owner.shutdown()

    def test_bad_evidence_retries_once_and_preserves_responses(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            book = pe.snapshot(root); path = root / '译文/story.zh-CN.txt'
            flag = notes.add(root, path, book, pe.engine, paragraphs(path.read_text()), 0, 0, 2, '马拉', '', '人名')
            replies = ['Mara...Iven', 'Mara']
            revised = TARGETS[0].replace('马拉', '玛拉')
            def request(prompt):
                return json.dumps({'annotation_id': flag['id'], 'verdict': 'issue', 'reason': '名字不一致',
                                   'source_evidence': replies.pop(0), 'suggestion': '统一译名',
                                   'suggested_text': revised}), .1
            row = notes.review(root, book, pe.engine, request, lambda _: None)['items'][0]
            self.assertEqual(row['status'], 'reviewed')
            self.assertEqual(len(row['responses']), 2)
            self.assertEqual(row['result']['suggested_text'], revised)
            self.assertNotIn('error', row)

    def test_default_draft_supports_v2_and_unambiguous_legacy_suggestions(self):
        original = '通道一侧立着一根约四英尺高的粗大金属栏杆。'
        revised = original.replace('一根', '一道')
        self.assertEqual(notes.default_draft({
            'translation': original,
            'result': {'suggested_text': revised, 'suggestion': '修改量词'},
        }), revised)
        self.assertEqual(notes.default_draft({
            'translation': original,
            'result': {'suggestion': '建议将“一根”改为“一道”或“一条”，以体现护栏的连续性。'},
        }), revised)
        repeated = '一根栏杆连着另一根栏杆。'
        self.assertEqual(notes.default_draft({
            'translation': repeated,
            'result': {'suggestion': '建议将“一根”改为“一道”。'},
        }), repeated)

    def test_issue_requires_a_targeted_complete_revised_paragraph(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            book = pe.snapshot(root); path = root / '译文/story.zh-CN.txt'
            flag = notes.add(root, path, book, pe.engine, paragraphs(path.read_text()), 0, 0, 2, '马拉', '', '人名')
            calls = []
            revised = TARGETS[0].replace('马拉', '玛拉')
            def request(prompt):
                calls.append(prompt)
                payload = {'annotation_id': flag['id'], 'verdict': 'issue', 'reason': '名字不一致',
                           'source_evidence': 'Mara', 'suggestion': '统一译名'}
                if len(calls) == 2:
                    payload['suggested_text'] = revised
                return json.dumps(payload), .1
            row = notes.review(root, book, pe.engine, request, lambda _: None)['items'][0]
            self.assertEqual(row['status'], 'reviewed')
            self.assertEqual(row['result']['suggested_text'], revised)
            self.assertEqual(len(calls), 2)
            self.assertIn('必须返回修改后的完整段落', calls[1])

    def test_annotation_on_export_uses_edited_paragraph_and_correct_source(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            report = pe.audit(root, response, lambda _: None, 'Mara')
            pe.preview(root, lambda _: (json.dumps({'text': TARGETS[0].replace('马拉', '玛拉')}), 0), lambda _: None,
                       [{'id': report['issues'][0]['id'], 'preferred': '玛拉'}])
            exported = Path(pe.publish(root, [1])['output'])
            book = pe.snapshot(root)
            flag = notes.add(root, exported, book, pe.engine, paragraphs(exported.read_text()), 0, 0, 2, '玛拉', '', '人名')
            self.assertEqual(flag['source_text'], book['segments'][0])
            self.assertEqual(flag['translation'], TARGETS[0].replace('马拉', '玛拉'))
            notes.validate_anchor(root, flag, book, pe.engine)

    def test_offsets_survive_wrapping_whitespace_and_unicode(self):
        text = '  😀第一句\n\n  ' + '句' * 1600 + '  \n第二句\n'
        blocks = paragraph_spans(text)
        self.assertEqual([r[0] for r in blocks], paragraphs(text))
        for value, start, end in blocks:
            self.assertEqual(text[start:end], value)

    def test_persist_recheck_resume_stale_and_original_unchanged(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root)
            book = pe.snapshot(root); path = root / '译文/story.zh-CN.txt'
            original = path.read_bytes(); rows = paragraphs(path.read_text())
            flag = notes.add(root, path, book, pe.engine, rows, 0, 0, 2, '马拉', '名字不一致', '人名 / 地名')
            calls = []
            revised = TARGETS[0].replace('马拉', '玛拉')
            def request(prompt):
                calls.append(prompt)
                return json.dumps({'annotation_id': flag['id'], 'verdict': 'issue', 'reason': '名字需核对',
                                   'source_evidence': 'Mara', 'suggestion': '与玛拉统一',
                                   'suggested_text': revised}), .1
            checked = notes.review(root, book, pe.engine, request, lambda _: None)
            self.assertEqual(checked['items'][0]['result']['verdict'], 'issue')
            notes.review(root, book, pe.engine, request, lambda _: None)
            self.assertEqual(len(calls), 1)
            notes.review(root, book, pe.engine, request, lambda _: None, [flag['id']])
            self.assertEqual(len(calls), 2)
            self.assertEqual(path.read_bytes(), original)
            make_book(root, [TARGETS[0].replace('静静地', '耐心地'), *TARGETS[1:]])
            result = notes.review(root, pe.snapshot(root), pe.engine, request, lambda _: None)
            self.assertEqual(result['items'][0]['status'], 'stale')
            self.assertEqual(len(calls), 2)

    def test_unrelated_txt_cannot_be_linked_to_current_book(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as outside:
            root = Path(raw); make_book(root)
            path = Path(outside) / 'other.txt'; path.write_text(TARGETS[0])
            with self.assertRaisesRegex(ValueError, '原文对照'):
                notes.add(root, path, pe.snapshot(root), pe.engine, [TARGETS[0]], 0, 0, 2, '马拉', '', '其他')

    def test_qt_add_handles_utf16_and_never_runs_model(self):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        import native_launcher as native
        app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); make_book(root, ['😀' + TARGETS[0], *TARGETS[1:]])
            owner = native.TranslatorController(workspace=root / 'jobs', settings=QSettings(str(root/'s.ini'), QSettings.Format.IniFormat), restore=False)
            owner._job_path = str(root); owner._output_path = str(root / '译文/story.zh-CN.txt'); owner._progress = 1
            owner._refresh_reader()
            wait_reader(app, owner)
            self.assertTrue(owner.post_editor.addAnnotation(0, 2, 4, '马拉', '检查音译', '人名'))
            saved = notes.load(root)['items'][0]
            self.assertEqual((saved['start'], saved['end']), (1, 3))
            owner.post_editor.load()
            self.assertEqual(len(owner.post_editor.annotations), 1)
            owner.post_editor.resolveAnnotation(saved['id'], True)
            self.assertEqual(notes.load(root)['items'][0]['status'], 'resolved')
            owner.reader_timer.stop(); owner.shutdown()


if __name__ == '__main__':
    unittest.main()
