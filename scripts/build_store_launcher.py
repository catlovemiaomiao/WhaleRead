"""Build a native paid-app verification library for the signed app entry.

No runtime environment variable bypasses StoreKit in a production launcher.
The real main executable retains its signed Info.plist and Sandbox identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import plistlib
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def validate_mode(mode: str, bundle_id: str):
    if mode not in {'store', 'qa', 'standalone'}:
        raise ValueError('Distribution mode must be standalone, store or qa')
    qa_id = bool(re.fullmatch(r'local\.sindy\.jingdu\.storeqa(?:[0-9]+|\.[A-Za-z0-9.-]+)', bundle_id))
    if mode == 'qa' and not qa_id:
        raise ValueError('QA purchase bypass requires a separate local.sindy.jingdu.storeqa identity')
    if mode == 'store' and qa_id:
        raise ValueError('Use an approved production identity for the paid Store launcher')
    if not re.fullmatch(r'[A-Za-z0-9]+(?:\.[A-Za-z0-9-]+)+', bundle_id):
        raise ValueError('Invalid application identity')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    parser.add_argument('--mode', choices=('standalone', 'store', 'qa'), default='standalone')
    args = parser.parse_args()
    app = args.app.resolve()
    if app.name != '鲸读.app' or not app.is_dir():
        parser.error('Expected a newly built 鲸读.app candidate')
    info_path = app / 'Contents/Info.plist'
    info = plistlib.loads(info_path.read_bytes())
    bundle_id = info.get('CFBundleIdentifier', '')
    validate_mode(args.mode, bundle_id)
    executable = app / 'Contents/MacOS/鲸读'
    library = app / 'Contents/Frameworks/WhaleReadPurchase.dylib'
    if library.exists() or not executable.is_file() or executable.is_symlink():
        raise ValueError('Refusing an existing purchase library or missing main program')
    if args.mode == 'standalone':
        license_path = ROOT / 'LICENSE'
        if not license_path.is_file():
            raise ValueError('The standalone application requires its GPL-3.0 license')
        manifest = dict(schema_version=1, mode='standalone', channel='github',
            bundle_identifier=bundle_id, version=info['CFBundleShortVersionString'],
            build=str(info['CFBundleVersion']), license='GPL-3.0-only',
            license_sha256=hashlib.sha256(license_path.read_bytes()).hexdigest(),
            purchase_required=False)
        info['WhaleReadReceiptValidation'] = 'standalone'
        info_path.write_bytes(plistlib.dumps(info))
        (app / 'Contents/Resources/DISTRIBUTION.json').write_text(
            json.dumps(manifest, indent=2) + '\n')
        print(json.dumps(manifest, sort_keys=True))
        return
    with tempfile.TemporaryDirectory(prefix='whaleread-store-launcher-') as raw:
        temporary = Path(raw)
        identity = temporary / 'ReleaseIdentity.swift'
        identity.write_text('enum ReleaseIdentity {\n'
            '    static let bundleID = ' + json.dumps(bundle_id) + '\n'
            '    static let mode = ' + json.dumps(args.mode) + '\n}\n')
        binary = temporary / 'WhaleReadPurchase.dylib'
        command = ['xcrun', 'swiftc', '-O', '-emit-library', '-target', 'arm64-apple-macosx15.0',
                   '-framework', 'StoreKit', '-framework', 'AppKit',
                   '-Xlinker', '-install_name', '-Xlinker', '@rpath/WhaleReadPurchase.dylib']
        if args.mode == 'qa':
            command += ['-D', 'WHALEREAD_QA']
        command += [str(ROOT / 'packaging/StoreLauncher.swift'), str(identity), '-o', str(binary)]
        subprocess.run(command, check=True)
        library.write_bytes(binary.read_bytes())
        library.chmod(0o755)
    info['WhaleReadReceiptValidation'] = args.mode
    info_path.write_bytes(plistlib.dumps(info))
    manifest = dict(mode=args.mode, bundle_identifier=bundle_id,
        mechanism='StoreKit AppTransaction verified by application entry before Qt; compile-time QA only',
        compiled_library_sha256_before_seal=hashlib.sha256(library.read_bytes()).hexdigest(),
        real_purchase_verified=False)
    (app / 'Contents/Resources/STORE_LAUNCHER.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, sort_keys=True))


if __name__ == '__main__':
    main()
