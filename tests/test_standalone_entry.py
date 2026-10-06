"""Exercise the real frozen entry without touching a user's reader or Keychain."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class StandaloneEntryTests(unittest.TestCase):
    def run_entry(self, *, mode='standalone', mutate=None, change_license=False):
        with tempfile.TemporaryDirectory(prefix='whaleread-free-entry-') as raw:
            root = Path(raw)
            contents = root / 'Fixture.app/Contents'
            resources = contents / 'Resources'
            license_dir = resources / 'licenses/WhaleRead'
            license_dir.mkdir(parents=True)
            (contents / 'MacOS').mkdir()
            license_data = b'fictional license fixture, never a distributed license'
            (license_dir / 'LICENSE').write_bytes(license_data)
            info = dict(CFBundleIdentifier='com.example.free.fixture',
                CFBundleShortVersionString='1.19.0', CFBundleVersion='67',
                WhaleReadReceiptValidation=mode)
            manifest = dict(schema_version=1, mode='standalone', channel='github',
                bundle_identifier=info['CFBundleIdentifier'], version='1.19.0', build='67',
                license='GPL-3.0-only', purchase_required=False,
                license_sha256=hashlib.sha256(license_data).hexdigest())
            if mutate:
                mutate(manifest)
            (contents / 'Info.plist').write_bytes(plistlib.dumps(info))
            (resources / 'DISTRIBUTION.json').write_text(json.dumps(manifest))
            if change_license:
                (license_dir / 'LICENSE').write_bytes(b'changed after packaging')
            script = '''import ctypes, os, pathlib, runpy, sys, types
sys.frozen = True
sys._MEIPASS = %r
sys.executable = %r
sys.argv = ['fixture']
os.environ['WHALEREAD_RECEIPT_MODE'] = 'standalone'
def forbidden_library(*args, **kwargs):
    raise OSError('Purchase library unavailable in this fixture')
ctypes.CDLL = forbidden_library
file_access = types.ModuleType('file_access')
file_access.application_home = lambda: pathlib.Path(%r)
sys.modules['file_access'] = file_access
native = types.ModuleType('native_launcher')
def main():
    print('READER_STARTED')
    return 0
native.main = main
sys.modules['native_launcher'] = native
runpy.run_path(%r, run_name='__main__')
''' % (str(ROOT), str(contents / 'MacOS/fixture'), str(root / 'isolated-data'),
       str(ROOT / 'packaging/app_entry.py'))
            return subprocess.run([sys.executable, '-I', '-B', '-c', script],
                capture_output=True, text=True, cwd=raw)

    def test_github_reader_starts_without_loading_any_purchase_library(self):
        result = self.run_entry()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn('READER_STARTED', result.stdout)

    def test_changed_identity_channel_payment_flag_and_license_fail_before_reader(self):
        changes = {
            'identity': lambda m: m.update(bundle_identifier='com.example.other'),
            'channel': lambda m: m.update(channel='app-store'),
            'payment': lambda m: m.update(purchase_required=True),
            'license': lambda m: m.update(license='proprietary'),
            'build': lambda m: m.update(build='66'),
        }
        for name, mutate in changes.items():
            with self.subTest(name=name):
                result = self.run_entry(mutate=mutate)
                self.assertNotEqual(0, result.returncode)
                self.assertNotIn('READER_STARTED', result.stdout)
                self.assertIn('Standalone distribution metadata is unavailable', result.stderr)
        result = self.run_entry(change_license=True)
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn('READER_STARTED', result.stdout)

    def test_runtime_variable_cannot_change_a_store_build_into_a_free_build(self):
        result = self.run_entry(mode='store')
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn('READER_STARTED', result.stdout)
        self.assertIn('Native purchase verification is unavailable', result.stderr)


if __name__ == '__main__':
    unittest.main()
