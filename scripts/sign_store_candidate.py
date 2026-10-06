"""Sign nested code explicitly and keep the helper's inheritance entitlements."""
import argparse
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from store_distribution import (base_entitlements, decode_profile, profile_claims,
    profile_authorizes_certificate, resolve_identity, validate_store_identity,
    verify_distribution_signature)
MACHO_MAGIC = {b'\xfe\xed\xfa\xce', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf',
               b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}


def run(args):
    result = subprocess.run(args, capture_output=True)
    if result.returncode:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace').strip())
    return result


def entitlements(path):
    result = run(['/usr/bin/codesign', '-d', '--entitlements', ':-', str(path)])
    return plistlib.loads(result.stdout)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('app')
    parser.add_argument('--identity', default='-')
    parser.add_argument('--profile', type=Path,
                        help='Mac App Store distribution provisioning profile, never a development profile')
    parser.add_argument('--team-id', default='')
    args = parser.parse_args()
    app = Path(args.app).resolve()
    main_exe, helper = app / 'Contents/MacOS/鲸读', app / 'Contents/MacOS/WhaleReadWorker'
    purchase = app / 'Contents/Frameworks/WhaleReadPurchase.dylib'
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    if (not main_exe.is_file() or not helper.is_file()
            or (info.get('WhaleReadReceiptValidation') != 'standalone' and not purchase.is_file())):
        raise SystemExit('Main program, required launcher or helper is missing')
    expected_main, expected_helper = base_entitlements(), base_entitlements(helper=True)
    profile_bytes = None
    if args.profile or args.team_id:
        if args.identity == '-':
            raise SystemExit('A distribution profile or team requires an Apple distribution signing identity')
        info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
        validate_store_identity(info.get('CFBundleIdentifier'), args.team_id)
        signing_sha1, _ = resolve_identity(args.identity, args.team_id)
        # Use the unambiguous code-signing certificate selector after resolution.
        args.identity = signing_sha1
        if args.profile:
            profile_bytes = args.profile.read_bytes()
            profile = decode_profile(args.profile)
            if args.profile.read_bytes() != profile_bytes:
                raise ValueError('Provisioning profile changed during validation')
            expected_main.update(profile_claims(profile, info['CFBundleIdentifier'], args.team_id))
            profile_authorizes_certificate(profile, signing_sha1)
    if profile_bytes is not None:
        profile_target = app / 'Contents/embedded.provisionprofile'
        if profile_target.is_symlink():
            raise ValueError('Refusing to replace a symlinked provisioning profile')
        profile_target.write_bytes(profile_bytes)
    run(['/usr/bin/xattr', '-cr', str(app)])
    options = ['--options', 'runtime'] if args.identity != '-' else []
    def sign(path, *, entitlement=None, identifier=None):
        command = ['/usr/bin/codesign', '--force', '--sign', args.identity, *options]
        if identifier:
            command += ['--identifier', identifier]
        if entitlement:
            command += ['--entitlements', str(entitlement)]
        run([*command, str(path)])
    binaries = set()
    for path in app.rglob('*'):
        if path.is_file() and not path.is_symlink() and path not in {main_exe, helper}:
            with path.open('rb') as stream:
                if stream.read(4) in MACHO_MAGIC:
                    binaries.add(path)
    for path in sorted(binaries, key=lambda p: (-len(p.parts), str(p))):
        sign(path)
    for framework in sorted(app.rglob('*.framework'), key=lambda p: (-len(p.parts), str(p))):
        if not framework.is_symlink():
            sign(framework)
    sign(helper, entitlement=ROOT / 'packaging/entitlements-worker.plist')
    # Framework signing / Finder can add metadata while nested code is signed.
    # Remove it from this candidate immediately before the outer seal.
    run(['/usr/bin/xattr', '-cr', str(app)])
    with tempfile.TemporaryDirectory(prefix='whaleread-distribution-entitlements-') as raw:
        main_entitlement_file = Path(raw) / 'main.plist'
        main_entitlement_file.write_bytes(plistlib.dumps(expected_main))
        sign(app, entitlement=main_entitlement_file)
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)])
    main_entitlements, helper_entitlements = entitlements(app), entitlements(helper)
    if main_entitlements != expected_main or helper_entitlements != expected_helper:
        raise SystemExit('Signed entitlements differ from the reviewed files')
    if args.team_id:
        verify_distribution_signature(app, args.team_id)
    print(json.dumps(dict(signature='adhoc' if args.identity == '-' else 'certificate',
                         main_entitlements=main_entitlements, helper_entitlements=helper_entitlements,
                         profile_embedded=profile_bytes is not None,
                         app_store_submission_verified=False), sort_keys=True))


if __name__ == '__main__':
    main()
