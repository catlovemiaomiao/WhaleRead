"""Distribution refusal cases and non-destructive package publication.

All profile/certificate data here is fictional. These tests do not claim Apple
trust, purchase validation or a signed Store upload. A real unsigned QA package
roundtrip is recorded separately with the candidate hash.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from package_store_candidate import (bundle_policy, validate_destination,
                                    validate_report_destination, verify_distribution, write_exclusive)
from store_distribution import choose_identity, profile_authorizes_certificate, profile_claims

TEAM = 'ABCDE12345'
BUNDLE = 'com.example.whaleread.fixture'
NOW = datetime(2026, 10, 2, tzinfo=timezone.utc)
CERT = b'fictional-public-certificate-bytes-not-a-real-certificate'


def profile():
    return dict(ExpirationDate=NOW + timedelta(days=30), TeamIdentifier=[TEAM],
        ApplicationIdentifierPrefix=['LEGACY6789'], Platform=['OSX'], DeveloperCertificates=[CERT],
        Entitlements={'com.apple.application-identifier': 'LEGACY6789.' + BUNDLE,
                      'com.apple.developer.team-identifier': TEAM,
                      'beta-reports-active': True,
                      'keychain-access-groups': ['LEGACY6789.*']})


class DistributionPolicyTests(unittest.TestCase):
    def test_app_id_prefix_may_differ_from_team_without_copying_wildcard_capabilities(self):
        claims = profile_claims(profile(), BUNDLE, TEAM, require_beta=True, now=NOW)
        self.assertEqual('LEGACY6789.' + BUNDLE, claims['com.apple.application-identifier'])
        self.assertEqual(TEAM, claims['com.apple.developer.team-identifier'])
        self.assertTrue(claims['beta-reports-active'])
        self.assertNotIn('keychain-access-groups', claims)

    def test_wrong_expired_development_and_unrelated_profiles_fail(self):
        mutations = {
            'expired': lambda p: p.update(ExpirationDate=NOW),
            'team': lambda p: p.update(TeamIdentifier=['OTHER12345']),
            'platform': lambda p: p.update(Platform=['iOS']),
            'devices': lambda p: p.update(ProvisionedDevices=[]),
            'developer_id': lambda p: p.update(ProvisionsAllDevices=True),
            'debug': lambda p: p['Entitlements'].update({'com.apple.security.get-task-allow': True}),
            'app_id': lambda p: p['Entitlements'].update({'com.apple.application-identifier': 'LEGACY6789.other'}),
            'wildcard': lambda p: p['Entitlements'].update({'com.apple.application-identifier': 'LEGACY6789.*'}),
            'entitlement_team': lambda p: p['Entitlements'].update({'com.apple.developer.team-identifier': 'OTHER12345'}),
            'certificates': lambda p: p.update(DeveloperCertificates=[]),
            'beta': lambda p: p['Entitlements'].pop('beta-reports-active'),
            'malformed_teams': lambda p: p.update(TeamIdentifier=TEAM),
            'malformed_prefixes': lambda p: p.update(ApplicationIdentifierPrefix='LEGACY6789'),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                candidate = profile(); mutate(candidate)
                with self.assertRaises(ValueError):
                    profile_claims(candidate, BUNDLE, TEAM, require_beta=True, now=NOW)

    def test_another_valid_certificate_is_still_not_authorized_by_profile(self):
        profile_authorizes_certificate(profile(), hashlib.sha1(CERT).hexdigest())
        with self.assertRaisesRegex(ValueError, 'this signing certificate'):
            profile_authorizes_certificate(profile(), '0' * 40)

    def test_distribution_identity_type_team_and_ambiguity(self):
        digest = 'A' * 40
        name = f'Apple Distribution: Fictional Test ({TEAM})'
        row = f'  1) {digest} "{name}"\n'
        self.assertEqual((digest, name), choose_identity(row, digest.lower(), TEAM))
        with self.assertRaises(ValueError):
            choose_identity(row, name, 'OTHER12345')
        for bad_name in (f'Apple Development: Fixture ({TEAM})', f'Developer ID Application: Fixture ({TEAM})'):
            with self.subTest(name=bad_name), self.assertRaises(ValueError):
                choose_identity(f'  1) {digest} "{bad_name}"\n', digest, TEAM)
        installer = f'3rd Party Mac Developer Installer: Fixture ({TEAM})'
        installer_row = f'  1) {digest} "{installer}"\n'
        self.assertEqual((digest, installer), choose_identity(installer_row, installer, TEAM, installer=True))
        with self.assertRaises(ValueError):
            choose_identity(installer_row, installer, TEAM)
        with self.assertRaisesRegex(ValueError, 'ambiguous'):
            choose_identity(installer_row + f'  2) {"B" * 40} "{installer}"\n', digest, TEAM, installer=True)

    def test_qa_and_provisional_bundles_cannot_take_store_path(self):
        for identity, mode in [('local.sindy.jingdu.storeqa5', 'qa'),
                               ('local.sindy.jingdu.storecandidate', 'store')]:
            info = dict(CFBundleIdentifier=identity, WhaleReadReceiptValidation=mode)
            manifest = dict(bundle_identifier=identity, mode=mode)
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                bundle_policy(info, manifest, identity, TEAM)
        with self.assertRaises(ValueError):
            bundle_policy(dict(CFBundleIdentifier=BUNDLE, WhaleReadReceiptValidation='store'),
                          dict(bundle_identifier=BUNDLE, mode='store'), BUNDLE, TEAM, qa=True)

    def test_receipt_mode_and_explicit_identity_mismatch_fail(self):
        info = dict(CFBundleIdentifier=BUNDLE, WhaleReadReceiptValidation='store')
        with self.assertRaises(ValueError):
            bundle_policy(info, dict(bundle_identifier=BUNDLE, mode='qa'), BUNDLE, TEAM)
        with self.assertRaises(ValueError):
            bundle_policy(info, dict(bundle_identifier=BUNDLE, mode='store'), BUNDLE + '.other', TEAM)


class PackagingProtectionTests(unittest.TestCase):
    def test_package_runtime_constraints_and_product_identity_cannot_drift(self):
        source = ('<installer-gui-script><options hostArchitectures="arm64" require-scripts="false"/>'
                  '<product id="com.example.whaleread.fixture" version="1.19.0"/>'
                  '<volume-check><allowed-os-versions><os-version min="15.0"/></allowed-os-versions></volume-check>'
                  '<choice customLocation="/Applications"/></installer-gui-script>')
        report = dict(bundle_id=BUNDLE, version='1.19.0', minimum_macos='15.0')
        changes = [('options', 'hostArchitectures', 'arm64,x86_64'),
                   ('volume-check/allowed-os-versions/os-version', 'min', '14.0'),
                   ('product', 'id', 'com.example.another'),
                   ('choice', 'customLocation', '/Users/Shared')]
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / 'Distribution'; path.write_text(source)
            verify_distribution(path, report)
            for element, key, value in changes:
                tree = ET.fromstring(source); tree.find(element).set(key, value)
                path.write_text(ET.tostring(tree, encoding='unicode'))
                with self.subTest(change=key), self.assertRaises(ValueError):
                    verify_distribution(path, report)

    def test_output_cannot_replace_existing_file_symlink_or_app_content(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); app = root / '鲸读.app'; app.mkdir()
            existing = root / 'existing.pkg'; existing.write_bytes(b'keep-existing')
            link = root / 'dangling.pkg'; link.symlink_to(root / 'absent')
            for output in (existing, link, app / 'nested.pkg', root / 'upload.zip'):
                with self.subTest(output=output.name), self.assertRaises(ValueError):
                    validate_destination(app, output)
            with self.assertRaises(ValueError):
                validate_destination(app, root / 'store.pkg', qa=True)
            self.assertEqual(b'keep-existing', existing.read_bytes())

    def test_atomic_publication_refuses_a_target_created_after_preflight(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); source = root / 'staged'; output = root / 'candidate.pkg'
            source.write_bytes(b'verified-new'); output.write_bytes(b'keep-existing')
            with self.assertRaises(FileExistsError):
                write_exclusive(source, output)
            self.assertEqual(b'keep-existing', output.read_bytes())
            self.assertEqual([], list(root.glob('.whaleread-pkg-*')))

    def test_report_cannot_modify_input_app_or_collide_with_package(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); app = root / '鲸读.app'; app.mkdir()
            output = root / 'candidate.pkg'
            for report in (app / 'new-report.json', output):
                with self.subTest(report=report.name), self.assertRaises(ValueError):
                    validate_report_destination(app, report, output)
            self.assertEqual([], list(app.iterdir()))

    def test_invalid_candidate_cli_does_not_create_package_or_change_input(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); app = root / '鲸读.app'
            resources = app / 'Contents/Resources'; resources.mkdir(parents=True)
            info = app / 'Contents/Info.plist'
            info.write_bytes(plistlib.dumps(dict(CFBundleIdentifier='local.sindy.jingdu.storecandidate',
                                                 WhaleReadReceiptValidation='store')))
            manifest = resources / 'STORE_LAUNCHER.json'
            manifest.write_text(json.dumps(dict(bundle_identifier='local.sindy.jingdu.storecandidate', mode='store')))
            before = (info.read_bytes(), manifest.read_bytes())
            output = root / 'upload.pkg'
            result = subprocess.run([sys.executable, '-I', '-B', str(ROOT / 'scripts/package_store_candidate.py'),
                str(app), '--bundle-id', 'local.sindy.jingdu.storecandidate', '--team-id', TEAM,
                '--output', str(output)], capture_output=True, text=True)
            self.assertNotEqual(0, result.returncode)
            self.assertIn('provisional', result.stderr)
            self.assertFalse(output.exists())
            self.assertEqual(before, (info.read_bytes(), manifest.read_bytes()))


if __name__ == '__main__':
    unittest.main()
