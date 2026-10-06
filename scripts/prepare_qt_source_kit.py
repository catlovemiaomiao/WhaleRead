"""Prepare pinned Qt/PySide source archives and reproducible replacement tools.

This local artifact supports a licensing review. It does not certify App Store
terms or redistribute application credentials, libraries or build environments.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import ssl
import urllib.request
import zipfile

import certifi
from build_qt_appstore import SOURCE_SHA256, SOURCE_BASE, VERSION, ROOT

PYSIDE_NAME = 'pyside-setup-everywhere-src-6.11.0.tar.xz'
PYSIDE_URL = ('https://download.qt.io/official_releases/QtForPython/pyside6/'
              'PySide6-6.11.0-src/' + PYSIDE_NAME)
PYSIDE_SHA256 = '48d5c44d7c3ed861055d5491486e6a220ef5006573cae01a5fae3fb69d786336'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = args.source_dir.resolve()
    output = args.output.resolve()
    if output.exists():
        raise ValueError('Refusing to replace an existing source kit')
    archive = source / PYSIDE_NAME
    if not archive.exists():
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(PYSIDE_URL, context=context, timeout=120) as response:
            temporary = archive.with_suffix('.download')
            with temporary.open('wb') as stream:
                shutil.copyfileobj(response, stream)
            temporary.replace(archive)
    entries = [(source / f'{module}-everywhere-src-{VERSION}.tar.xz', expected,
                SOURCE_BASE + f'{module}-everywhere-src-{VERSION}.tar.xz')
               for module, expected in SOURCE_SHA256.items()]
    entries.append((archive, PYSIDE_SHA256, PYSIDE_URL))
    records = []
    for path, expected, url in entries:
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Source archive hash mismatch: ' + path.name)
        records.append(dict(file='sources/' + path.name, sha256=expected, source_url=url))
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.preparing')
    try:
        # xz sources are already compressed. Store them without wasting CPU.
        with zipfile.ZipFile(temporary, 'x', compression=zipfile.ZIP_STORED) as kit:
            for path, _, _ in entries:
                kit.write(path, 'sources/' + path.name)
            for relative in ('scripts/build_qt_appstore.py', 'scripts/adopt_qt_appstore.py',
                             'scripts/collect_qt_notices.py', 'scripts/sign_store_candidate.py',
                             'scripts/store_distribution.py', 'scripts/package_store_candidate.py',
                             'scripts/build_store_launcher.py', 'packaging/StoreLauncher.swift',
                             'scripts/prepare_qt_source_kit.py', 'packaging/WhaleRead.spec',
                             'scripts/audit_native_bundle.py',
                             'packaging/entitlements-main.plist', 'packaging/entitlements-worker.plist',
                             'packaging/app_entry.py',
                             'packaging/requirements-macos.txt', 'docs/APP_STORE_BUILD.md',
                             'docs/OPEN_SOURCE_BUILD.md', 'docs/COMPONENT_SOURCE.md',
                             'LICENSE', 'runtime/lib/distribution_policy.py',
                             'packaging/licenses/Qt/QtFinishPrlFile-utf8.patch',
                             'packaging/licenses/THIRD_PARTY_NOTICES.md'):
                kit.write(ROOT / relative, 'rebuild/' + relative)
            kit.writestr('SOURCE_MANIFEST.json', json.dumps(dict(
                scope='Qt/PySide sources and rebuild material; licensing review remains required',
                qt_version=VERSION, archives=records), indent=2) + '\n')
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps(dict(file=str(output), bytes=output.stat().st_size,
                         sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                         archives=len(entries)), sort_keys=True))


if __name__ == '__main__':
    main()
