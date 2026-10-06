"""Preflight and package WhaleRead for manual App Store Connect validation.

Does not sign/mutate the input app, install, upload, log in or accept terms.
An explicit QA-only unsigned path exercises productbuild without a paid account;
that package is never reported as an upload candidate.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parent))
from hash_app_bundle import bundle_digest
from store_distribution import (ROOT, QA_ID, base_entitlements, decode_profile,
    leaf_certificate, profile_authorizes_certificate, profile_claims, resolve_identity,
    run, signed_entitlements, validate_store_identity, verify_distribution_signature)


def bundle_policy(info, manifest, expected_id, team_id, *, qa=False):
    actual = info.get('CFBundleIdentifier', '')
    if actual != expected_id:
        raise ValueError('Bundle ID differs from the explicitly selected identity')
    mode = 'qa' if qa else 'store'
    if qa:
        if not QA_ID.fullmatch(actual):
            raise ValueError('Unsigned QA packaging requires an isolated QA identity')
    else:
        validate_store_identity(actual, team_id)
    if (info.get('WhaleReadReceiptValidation') != mode or manifest.get('mode') != mode
            or manifest.get('bundle_identifier') != actual):
        raise ValueError('Purchase validation mode and bundle identity disagree')


def preflight(app, bundle_id, team_id, installer_identity, *, qa=False, channel='testflight'):
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    manifest = json.loads((app / 'Contents/Resources/STORE_LAUNCHER.json').read_text())
    bundle_policy(info, manifest, bundle_id, team_id, qa=qa)
    before = bundle_digest(app)
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)])
    for script in ('check_release_bundle.py', 'audit_native_bundle.py'):
        run([sys.executable, '-B', str(ROOT / 'scripts' / script), str(app)])
    expected_main = base_entitlements()
    profile = None
    identity = None
    if not qa:
        verify_distribution_signature(app, team_id)
        identity = resolve_identity(installer_identity, team_id, installer=True)
        profile_path = app / 'Contents/embedded.provisionprofile'
        if profile_path.is_symlink():
            raise ValueError('The embedded profile must be an ordinary file inside the application')
        if profile_path.exists():
            profile = decode_profile(profile_path)
            expected_main.update(profile_claims(profile, bundle_id, team_id,
                                               require_beta=channel == 'testflight'))
            with tempfile.TemporaryDirectory(prefix='whaleread-public-cert-') as raw:
                cert = leaf_certificate(app, Path(raw) / 'certificate')
                profile_authorizes_certificate(profile, hashlib.sha1(cert).hexdigest())
        elif channel == 'testflight':
            raise ValueError('TestFlight requires Contents/embedded.provisionprofile')
    if signed_entitlements(app) != expected_main:
        raise ValueError('Main entitlements differ from reviewed capabilities and profile identity')
    if signed_entitlements(app / 'Contents/MacOS/WhaleReadWorker') != base_entitlements(helper=True):
        raise ValueError('Worker must retain only Sandbox + inherit entitlements')
    if bundle_digest(app) != before:
        raise ValueError('Input application changed during preflight')
    return dict(bundle_id=bundle_id, bundle_sha256=before[0], version=info['CFBundleShortVersionString'],
                build=info['CFBundleVersion'], minimum_macos=info['LSMinimumSystemVersion'],
                mode='qa-unsigned' if qa else channel, team_id=team_id if not qa else None,
                installer_identity=identity[1] if identity else None,
                profile_present=profile is not None, local_preflight_passed=True,
                apple_validation_passed=False, purchase_tested=False, uploaded=False)


def validate_destination(app, output, *, qa=False):
    if output.exists() or output.is_symlink():
        raise ValueError('Refusing to replace an existing package or symlink')
    if output.resolve().is_relative_to(app.resolve()):
        raise ValueError('Output must be outside the input application')
    if output.suffix != '.pkg' or (qa and not output.name.endswith('.qa-unsigned.pkg')):
        raise ValueError('Use a .pkg output; unsigned QA names must end in .qa-unsigned.pkg')


def validate_report_destination(app, report, output=None):
    if report.exists() or report.is_symlink() or report.resolve().is_relative_to(app.resolve()):
        raise ValueError('Report must be a new file outside the input application')
    if output is not None and report.resolve() == output.resolve():
        raise ValueError('Package and report must use different paths')


def verify_distribution(path, report):
    distribution = ET.parse(path).getroot()
    options = distribution.find('options')
    product = distribution.find('product')
    versions = distribution.findall('volume-check/allowed-os-versions/os-version')
    if (options is None or options.get('hostArchitectures') != 'arm64'
            or options.get('require-scripts') != 'false'
            or len(versions) != 1 or versions[0].attrib != {'min': report['minimum_macos']}):
        raise ValueError('Package architecture or minimum macOS requirements changed')
    if product is None or product.get('id') != report['bundle_id'] or product.get('version') != report['version']:
        raise ValueError('Package product identity differs from the input application')
    if distribution.find('script') is not None or any(
            choice.get('customLocation') != '/Applications'
            for choice in distribution.findall('choice[@customLocation]')):
        raise ValueError('Unexpected installer script or custom destination')


def verify_payload(expanded, report):
    verify_distribution(expanded / 'Distribution', report)
    apps = [p for p in expanded.rglob('鲸读.app') if p.is_dir() and not p.is_symlink()]
    if len(apps) != 1 or bundle_digest(apps[0])[0] != report['bundle_sha256']:
        raise ValueError('Package payload does not reproduce the exact input application')
    run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(apps[0])])
    components = list(expanded.rglob('PackageInfo'))
    if len(components) != 1:
        raise ValueError('Expected one application component')
    package = ET.parse(components[0]).getroot()
    if package.get('install-location') != '/Applications' or package.find('scripts') is not None:
        raise ValueError('Package must install only into /Applications without installer scripts')
    if any(p.name == 'Scripts' for p in expanded.rglob('*')):
        raise ValueError('Unexpected installer scripts')


def write_exclusive(source, output):
    # Publish only a complete verified file; hard-link publication refuses races
    # with an existing target and works even if /tmp is on another filesystem.
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix='.whaleread-pkg-', dir=output.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            with source.open('rb') as incoming:
                shutil.copyfileobj(incoming, stream)
            stream.flush()
            os.fsync(stream.fileno())
            os.link(temporary, output)
        finally:
            temporary.unlink(missing_ok=True)


def package(app, output, report, *, qa=False):
    validate_destination(app, output, qa=qa)
    with tempfile.TemporaryDirectory(prefix='whaleread-productbuild-', dir='/private/tmp') as raw:
        work = Path(raw)
        requirements = work / 'requirements.plist'
        requirements.write_bytes(plistlib.dumps({'os': [report['minimum_macos']], 'arch': ['arm64']}))
        staged = work / 'candidate.pkg'
        command = ['/usr/bin/productbuild', '--component', str(app), '/Applications',
                   '--product', str(requirements)]
        if not qa:
            command += ['--sign', report['installer_identity']]
        run([*command, str(staged)])
        if not qa:
            signature = run(['/usr/sbin/pkgutil', '--check-signature', str(staged)])
            summary = (signature.stdout + signature.stderr).decode('utf-8', errors='replace')
            if report['installer_identity'] not in summary:
                raise ValueError('Package signature does not match the selected Installer identity')
        expanded = work / 'expanded'
        run(['/usr/sbin/pkgutil', '--expand-full', str(staged), str(expanded)])
        verify_payload(expanded, report)
        if bundle_digest(app)[0] != report['bundle_sha256']:
            raise ValueError('Input application changed while packaging')
        write_exclusive(staged, output)
    report.update(package=str(output), package_sha256=hashlib.sha256(output.read_bytes()).hexdigest(),
                  package_bytes=output.stat().st_size, payload_roundtrip_verified=True,
                  installer_signature_verified=not qa, input_app_unchanged=True,
                  package_requirements_verified=True, local_package_checks_passed=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('app', type=Path)
    parser.add_argument('--bundle-id', required=True)
    parser.add_argument('--team-id', default='')
    parser.add_argument('--installer-identity', default='')
    parser.add_argument('--channel', choices=('testflight', 'app-store'), default='testflight')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--preflight-only', action='store_true')
    parser.add_argument('--qa-unsigned', action='store_true')
    args = parser.parse_args()
    if not args.preflight_only and args.output is None:
        parser.error('--output is required when creating a package')
    app = args.app.resolve()
    if app.name != '鲸读.app' or not (app / 'Contents/Info.plist').is_file():
        parser.error('Expected a built 鲸读.app')
    try:
        if args.report:
            validate_report_destination(app, args.report, args.output)
        if args.output:
            validate_destination(app, args.output.absolute(), qa=args.qa_unsigned)
        report = preflight(app, args.bundle_id, args.team_id, args.installer_identity,
                           qa=args.qa_unsigned, channel=args.channel)
        if not args.preflight_only:
            report = package(app, args.output.absolute(), report, qa=args.qa_unsigned)
        encoded = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            with args.report.open('x') as stream:
                stream.write(encoded)
        print(encoded, end='')
        return 0
    except (OSError, ValueError, TypeError, KeyError) as error:
        print('Package preflight failed: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
