"""Opt-in synthetic acceptance using the signed GUI and its inherited helper.

Routes and credentials arrive on stdin, never from developer files or argv.
Only fictional text is sent. All QA Keychain items are deleted in finally.
This is an acceptance entry point, not a background network startup probe.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

import annotations as notes
import post_edit
from credential_store import KeychainCredentialStore
from file_access import application_home, is_sandboxed
from native_launcher import TranslatorController
from provider_profiles import ProviderProfiles
from storage_context import StorageContext
from reader import paragraphs


def review_fixture(root: Path):
    import yaml
    source = ('She thought of the letter burning in her pocket — five lines, no signature, '
              'and the one name she had spent three years trying to forget.')
    translated = ('她想起口袋里那封正在燃烧的信——短短五行字，没有署名，却写着那个她费了三年时间'
                  '试图忘却的名字。')
    (root / '原文').mkdir(parents=True)
    source_path = root / '原文/story.txt'
    source_path.write_text(source, encoding='utf-8')
    config = dict(schema_version=1, languages=dict(source='英语', target='简体中文'),
                  source=dict(files=['原文/story.txt'], encoding='UTF-8'),
                  chunking=dict(segment_mode='blank_line'), glossary=dict(mode='none'),
                  output=dict(directory='译文', filename_rule='story.zh-CN.txt'))
    (root / '翻译任务.yaml').write_text(yaml.safe_dump(config, allow_unicode=True), encoding='utf-8')
    engine = post_edit.engine
    segments, mode, layout = engine.split_source(source, 'blank_line')
    output = root / '译文'
    (output / '.hy-direct-chunks').mkdir(parents=True)
    record = dict(start=1, end=1, path='.hy-direct-chunks/1.json', protocol=engine.OUTPUT_PROTOCOL)
    engine.atomic_json(output / record['path'], dict(
        schema_version=engine.STATE_SCHEMA_VERSION, protocol=engine.OUTPUT_PROTOCOL,
        start=1, end=1, segments=[dict(id=1, text=translated)]))
    engine.atomic_json(output / '.hy-direct-state.json', dict(
        schema_version=engine.STATE_SCHEMA_VERSION, protocol=engine.OUTPUT_PROTOCOL,
        source_hash=engine.source_digest([source_path]),
        segment_hash=engine.segmentation_digest(segments, mode), chunks=[record]))
    machine = output / 'story.zh-CN.txt'
    engine.atomic_text(machine, engine.render_layout(layout, {1: translated}, 1, 1))
    book = post_edit.snapshot(root)
    start = translated.index('正在燃烧')
    flag = notes.add(root, machine, book, engine, paragraphs(translated), 0, start, start + 4,
                     '正在燃烧', '这里是否被误解成真的着火？', '疑似误译')
    return flag, machine


def wait_until_finished(predicate, timeout=160):
    deadline = time.monotonic() + timeout
    while predicate() and time.monotonic() < deadline:
        QApplication.instance().processEvents()
        time.sleep(0.02)
    if predicate():
        raise RuntimeError('Acceptance worker exceeded its deadline')


def main() -> int:
    if not getattr(sys, 'frozen', False) or not is_sandboxed():
        raise RuntimeError('This probe requires the signed sandbox candidate')
    raw = sys.stdin.buffer.read(65537)
    if len(raw) > 65536:
        raise ValueError('Acceptance input too large')
    routes = json.loads(raw)
    if isinstance(routes, dict) and routes.get('mode') == 'research':
        from research_acceptance import run
        return run(routes)
    if isinstance(routes, dict) and routes.get('mode') == 'research-setup':
        from research_acceptance import configure
        return configure(routes)
    if not isinstance(routes, list) or not 1 <= len(routes) <= 3:
        raise ValueError('Supply one to three explicitly authorized test routes')
    app = QApplication(['bundle-api-probe'])
    credentials = KeychainCredentialStore(service='local.sindy.jingdu.qa.' + uuid.uuid4().hex)
    qa_parent = application_home() / 'tmp'
    qa_parent.mkdir(parents=True, exist_ok=True)
    reports, ids, owners = [], [], []
    cleanup_ok = False
    try:
        with tempfile.TemporaryDirectory(prefix='whaleread-bundle-api-', dir=qa_parent) as raw_root:
            base = Path(raw_root).resolve()
            for index, route in enumerate(routes):
                case = base / str(index)
                case.mkdir()
                settings = QSettings(str(case / 'settings.ini'), QSettings.IniFormat)
                storage = StorageContext.isolated(case / 'storage', purpose='qa', settings_scope=settings.fileName())
                owner = TranslatorController(workspace=case / 'Tasks', settings=settings,
                    storage_context=storage, credential_store=credentials, restore=False)
                owners.append(owner)
                pid = owner.providers.save('', route['label'], route['endpoint'], route['model'], route['key'], True)
                if not pid:
                    raise RuntimeError('Acceptance profile could not be saved')
                ids.append(pid)
                for feature in ('translation', 'review', 'ask'):
                    if not owner.providers.select(feature, pid):
                        raise RuntimeError('Acceptance feature binding failed')
                    owner.providers.setConsent(feature, pid, True)
                owner.translationProfile = pid
                source = case / 'synthetic.txt'
                source.write_text('Mara carried the letter in her pocket as the ferry crossed the harbor.\n\n'
                    'The letter was burning a hole in her pocket, but she waited until dawn to read it.\n\n'
                    'At sunrise, she opened the envelope beside the old lighthouse and smiled.', encoding='utf-8')
                owner._select_source(source)
                owner.startTranslation()
                wait_until_finished(lambda: owner._process is not None)
                progress = json.loads((Path(owner.jobPath) / '翻译进度.json').read_text())
                output = Path(owner.outputPath)
                if not owner._finished or progress['completed_segments'] != 3:
                    raise RuntimeError('Acceptance translation incomplete')
                digest = hashlib.sha256(output.read_bytes()).hexdigest()
                owner.startTranslation()
                wait_until_finished(lambda: owner._process is not None)
                if hashlib.sha256(output.read_bytes()).hexdigest() != digest:
                    raise RuntimeError('Completed translation changed on restart')
                reopened = ProviderProfiles(QSettings(settings.fileName(), QSettings.IniFormat), credential_store=credentials)
                if reopened.key(pid) != route['key'] or reopened.selected('ask') != pid:
                    raise RuntimeError('Profile or Keychain failed to reopen')
                if not owner.openReadingFile(str(output)):
                    raise RuntimeError('Translated edition could not be opened')
                column = owner._active_column()
                owner.ask_ai.openSelection(column.columnId, 1, column.reader.rows[1][:6])
                if not owner.ask_ai.origin or not owner.ask_ai.context:
                    raise RuntimeError('Original paragraph map unavailable')
                owner.ask_ai.ask('Explain the idiom burning a hole in her pocket. Was the letter literally on fire?')
                wait_until_finished(lambda: owner.ask_ai.process is not None)
                if not owner.ask_ai.answer or owner.ask_ai.error or not owner.ask_ai.saveAnswerToNote():
                    raise RuntimeError('Acceptance Ask AI or note save failed')
                review = case / 'ReviewFixture'
                flag, machine = review_fixture(review)
                owner._job_path = str(review)
                owner._progress, owner._finished = 1, True
                before = hashlib.sha256(machine.read_bytes()).hexdigest()
                owner.post_editor.reviewAnnotation(flag['id'])
                wait_until_finished(lambda: owner.post_editor.busy)
                item = notes.load(review)['items'][0]
                if item.get('status') != 'reviewed' or not item.get('result'):
                    raise RuntimeError('Acceptance annotation review failed')
                if hashlib.sha256(machine.read_bytes()).hexdigest() != before:
                    raise RuntimeError('Automated review modified the original translation')
                for path in case.rglob('*'):
                    if path.is_file() and path.suffix in {'.ini', '.json', '.yaml', '.txt'}:
                        if route['key'].encode() in path.read_bytes():
                            raise RuntimeError('Credential entered QA artifacts')
                reports.append(dict(provider=route['label'], model=route['model'],
                    profile_saved_reopened=True, keychain_verified=True, translated_segments=3,
                    inherited_worker=True, restart_output_unchanged=True, ask_original_mapping=True,
                    ask_note_saved=True, annotation_status=item['status'],
                    annotation_verdict=item['result'].get('verdict'), original_unchanged=True,
                    secrets_absent_from_artifacts=True))
                owner.shutdown()
    finally:
        for owner in owners:
            owner.shutdown()
        for pid in ids:
            credentials.delete(pid)
        cleanup_ok = all(not credentials.get(pid) for pid in ids)
    print('BUNDLE_API_PROBE ' + json.dumps(dict(frozen=True, sandbox_entitlement_active=True,
        qa_keys_deleted=cleanup_ok, results=reports), ensure_ascii=False), flush=True)
    return 0 if cleanup_ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
