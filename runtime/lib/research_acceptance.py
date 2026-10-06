"""Explicit, synthetic sandbox acceptance; invoked only by bundle_api_probe."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import time


def configure(config):
    """Explicitly configure only a separate QA app, never a Store identity."""
    import re
    from PySide6.QtWidgets import QApplication
    from application_identity import application_settings, bundle_identifier
    from provider_profiles import ProviderProfiles
    if not re.fullmatch(r'local\.sindy\.jingdu\.storeqa\d+', bundle_identifier()):
        raise ValueError('This setup requires a separate QA application identity')
    app = QApplication(['private-ocr-setup'])
    settings = application_settings()
    providers = ProviderProfiles(settings)
    if providers.selected('ocr'):
        raise ValueError('Existing OCR profile preserved; setup is only for a new candidate')
    route = config['ocr']
    pid = providers.save('', 'DGX · PP-OCRv6', route['endpoint'], route['model'], route['key'], True)
    if not pid or providers.key(pid) != route['key']:
        raise ValueError('Private service profile could not be verified')
    assert providers.select('ocr', pid)
    providers.setConsent('ocr', pid, True)
    providers.select('translation', 'local_1_8b')
    settings.setValue('interface/locale', 'zh-CN')
    settings.sync()
    print('BUNDLE_RESEARCH_SETUP ' + json.dumps(dict(bundle_id=bundle_identifier(),
        profile_saved=True, keychain_readback=True, ocr_selected=True,
        sharing_authorized_by_task=True, translation='local_1_8b')), flush=True)
    return 0


def run(config):
    from PIL import Image, ImageDraw, ImageFont
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from credential_store import MemoryCredentialStore
    from file_access import application_home
    from native_launcher import TranslatorController
    from research import load, page_dir, export_html
    from storage_context import StorageContext

    app = QApplication(['research-acceptance'])
    saved_profile_verified = False
    if config.get('verify_saved_ocr'):
        from application_identity import application_settings
        from provider_profiles import ProviderProfiles
        saved = ProviderProfiles(application_settings())
        selected = saved.selected('ocr')
        assert saved.consented('ocr', selected)
        assert saved.key(selected) == config['ocr']['key']
        saved_profile_verified = True
    def until(predicate, timeout=240):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            app.processEvents()
            if predicate():
                return
            time.sleep(.02)
        raise RuntimeError('Research acceptance deadline exceeded')

    qa_parent = application_home() / 'tmp'
    qa_parent.mkdir(parents=True, exist_ok=True)
    credentials = MemoryCredentialStore()  # Test only: no persistent QA token.
    owners = []
    with tempfile.TemporaryDirectory(prefix='whaleread-research-', dir=qa_parent) as raw:
        root = Path(raw)
        settings = QSettings(str(root / 'settings.ini'), QSettings.IniFormat)
        def make_owner(restore):
            owner = TranslatorController(workspace=root / 'Tasks', settings=settings,
                storage_context=StorageContext.isolated(root / 'storage', purpose='qa', settings_scope=settings.fileName()),
                credential_store=credentials, restore=restore)
            owners.append(owner)
            return owner
        try:
            owner = make_owner(False)
            route = config['ocr']
            pid = owner.providers.save('', 'Synthetic OCR acceptance', route['endpoint'], route['model'], route['key'], True)
            assert pid and owner.providers.select('ocr', pid)
            owner.providers.setConsent('ocr', pid, True)
            owner.translationProfile = 'local_1_8b'
            pages = []
            for number in (1, 2, 3):
                image = Image.new('RGB', (1200, 1600), 'white')
                draw = ImageDraw.Draw(image)
                font = ImageFont.load_default(size=32)
                lines = [f'Research sample page {number}', '',
                    'The team studied how plants grow in warm rooms.',
                    'They measured the height of every plant each week.', '',
                    'The results suggest that light changes leaf growth.',
                    'The original notes remain available for review.', '',
                    'This fictional document tests translation recovery.',
                    'No personal or confidential information is included.']
                for i, line in enumerate(lines):
                    draw.text((70, 80 + i*70), line, fill='black', font=font)
                pages.append(image)
            source = root / 'fictional-research.pdf'
            pages[0].save(source, 'PDF', save_all=True, append_images=pages[1:])
            original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            owner.research.openPath(str(source))
            assert owner.research.job
            job = owner.research.job
            owner.research.setBatchMode(True)
            owner.research.run('batch')
            def saved_blocks():
                result = {}
                for path in (job / 'pages').glob('*/page.json'):
                    for b in load(path)['blocks']:
                        if b.get('translation'):
                            result[(path.parent.name, b['id'])] = b
                return result
            until(lambda: bool(saved_blocks()) or owner.research.process is None)
            assert saved_blocks(), owner.research.message_text() + owner.research.message_detail()
            owner.research.stop()
            until(lambda: owner.research.process is None)
            before = saved_blocks()
            assert load(job / 'document.json')['batch']['state'] == 'paused'
            owner.shutdown()
            owner = make_owner(True)
            until(lambda: not owner.research._restore_scheduled and owner.research.process is None)
            assert owner.research.job == job
            owner.research.run('batch')
            until(lambda: owner.research.process is None, timeout=600)
            manifest = load(job / 'document.json')
            assert manifest['batch']['state'] == 'done', owner.research.message_text() + owner.research.message_detail()
            after = saved_blocks()
            assert all(after[key] == value for key, value in before.items())
            data = [load(page_dir(job, n) / 'page.json') for n in (1,2,3)]
            assert all(not page['approved'] for page in data)
            assert all(page['translation_mode'] == 'batch' for page in data)
            assert all(b['translation'] for page in data for b in page['blocks'] if not b['visual'])
            assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash
            assert '尚未人工核对' in export_html(job).read_text()
            # Normal single-page approval and OCR consent remain effective.
            owner.research.run('translate')
            assert owner.research.process is None and owner.research.state['statusCode'] == 'researchNeedApproval'
            owner.providers.setConsent('ocr', pid, False)
            owner.research.run('analyze')
            assert owner.research.process is None and owner.research.state['statusCode'] == 'actionIncomplete'
            secret = route['key'].encode()
            for path in root.rglob('*'):
                if path.is_file() and path.suffix in {'.ini','.json','.txt','.html'}:
                    assert secret not in path.read_bytes(), 'Secret persisted to task artifacts'
            report = dict(frozen=True, sandbox_pipeline=True, native_window_verified=False,
                saved_profile_keychain_verified=saved_profile_verified,
                pages=3, translated_blocks=len(after), saved_blocks_before_pause=len(before),
                pause_reopen_resume=True, completed_blocks_unchanged=True,
                approval_not_fabricated=True, manual_mode_guard=True, ocr_consent_guard=True,
                original_unchanged=True, secret_absent_from_files=True,
                translation_model='jingdu-hy-mt2:1.8b-q8', ocr_model=route['model'])
            print('BUNDLE_RESEARCH_PROBE ' + json.dumps(report), flush=True)
            return 0
        finally:
            for owner in owners:
                owner.shutdown()
