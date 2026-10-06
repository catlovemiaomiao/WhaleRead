"""Task 06: the release gates must fail, not warn.

Each test builds a temporary fixture, breaks exactly one thing, and asserts the
gate exits non-zero with a message naming the problem.  Nothing here writes to
the real worktree: the catalogue fixtures live in temporary directories and the
bundle fixtures are synthetic trees.

A gate test that only asserted "the happy path passes" would not prove anything,
so every negative case below is the point of the test.
"""
from __future__ import annotations

import json
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
GATE = ROOT / "scripts" / "check_release_bundle.py"
WORKERS = (
    "ask_ai_request.py", "bundle_ui_probe.py", "bundle_api_probe.py", "post_edit.py", "probe_model.py",
    "research_ocr.py", "research_worker.py", "translate_range.py", "worker_entry.py",
)


def run_gate(*args):
    return subprocess.run([PYTHON, "-I", "-B", str(GATE), *args],
                          capture_output=True, text=True, cwd=str(ROOT))


class SourceGateTests(unittest.TestCase):
    """Catalogue completeness, placeholder parity and .qm freshness."""

    def fixture(self, mutate=None):
        """Copy the real catalogue into a temp tree the gate can be pointed at."""
        raw = tempfile.mkdtemp(prefix="release-gate-")
        self.addCleanup(shutil.rmtree, raw, True)
        work = Path(raw) / "repo"
        (work / "i18n").mkdir(parents=True)
        shutil.copy(ROOT / "i18n" / "whaleread_en.ts", work / "i18n" / "whaleread_en.ts")
        shutil.copy(ROOT / "i18n" / "whaleread_en.qm", work / "i18n" / "whaleread_en.qm")
        if mutate:
            mutate(work)
        return work

    def run_scoped(self, work, *args):
        """Run the gate with ROOT pointed at the fixture via a thin wrapper."""
        script = work / "run_gate.py"
        text = GATE.read_text(encoding="utf-8")
        text = text.replace('ROOT = Path(__file__).resolve().parents[1]',
                            f'ROOT = Path({str(work)!r})')
        script.write_text(text, encoding="utf-8")
        return subprocess.run([PYTHON, "-B", str(script), *args],
                              capture_output=True, text=True, cwd=str(work))

    def test_untouched_fixture_passes(self):
        work = self.fixture()
        completed = self.run_scoped(work, "--source-only")
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_unfinished_core_message_fails(self):
        def mutate(work):
            tree = ET.parse(work / "i18n" / "whaleread_en.ts")
            message = tree.getroot().find("context").find("message")
            message.find("translation").set("type", "unfinished")
            tree.write(work / "i18n" / "whaleread_en.ts", encoding="utf-8",
                       xml_declaration=True)
        completed = self.run_scoped(self.fixture(mutate), "--source-only")
        self.assertEqual(1, completed.returncode)
        self.assertIn("unfinished", completed.stderr)

    def test_obsolete_message_fails(self):
        def mutate(work):
            tree = ET.parse(work / "i18n" / "whaleread_en.ts")
            message = tree.getroot().find("context").find("message")
            message.find("translation").set("type", "obsolete")
            tree.write(work / "i18n" / "whaleread_en.ts", encoding="utf-8",
                       xml_declaration=True)
        completed = self.run_scoped(self.fixture(mutate), "--source-only")
        self.assertEqual(1, completed.returncode)
        self.assertIn("obsolete", completed.stderr)

    def test_placeholder_mismatch_fails(self):
        def mutate(work):
            path = work / "i18n" / "whaleread_en.ts"
            tree = ET.parse(path)
            for message in tree.getroot().iter("message"):
                source = message.findtext("source") or ""
                if "%1" in source and message.get("numerus") != "yes":
                    message.find("translation").text = "no placeholder left"
                    break
            tree.write(path, encoding="utf-8", xml_declaration=True)
        completed = self.run_scoped(self.fixture(mutate), "--source-only")
        self.assertEqual(1, completed.returncode)
        self.assertIn("placeholder", completed.stderr)

    def test_plural_form_count_mismatch_fails(self):
        def mutate(work):
            path = work / "i18n" / "whaleread_en.ts"
            tree = ET.parse(path)
            for message in tree.getroot().iter("message"):
                if message.get("numerus") == "yes":
                    translation = message.find("translation")
                    forms = translation.findall("numerusform")
                    translation.remove(forms[-1])
                    break
            tree.write(path, encoding="utf-8", xml_declaration=True)
        completed = self.run_scoped(self.fixture(mutate), "--source-only")
        self.assertEqual(1, completed.returncode)
        self.assertIn("plural", completed.stderr)

    def test_stale_compiled_catalogue_fails(self):
        def mutate(work):
            # Make the .ts newer than the .qm without rebuilding the .qm.
            future = time.time() + 120
            os.utime(work / "i18n" / "whaleread_en.ts", (future, future))
        completed = self.run_scoped(self.fixture(mutate), "--source-only")
        self.assertEqual(1, completed.returncode)
        self.assertIn("older than its source", completed.stderr)

    def test_newer_but_content_stale_catalogue_fails(self):
        def mutate(work):
            path = work / "i18n" / "whaleread_en.ts"
            text = path.read_text(encoding="utf-8")
            self.assertIn("<translation>Settings</translation>", text)
            path.write_text(
                text.replace(
                    "<translation>Settings</translation>",
                    "<translation>DELIBERATELY STALE</translation>",
                    1,
                ),
                encoding="utf-8",
            )
            future = time.time() + 120
            os.utime(work / "i18n" / "whaleread_en.qm", (future, future))
        completed = self.run_scoped(self.fixture(mutate), "--source-only")
        self.assertEqual(1, completed.returncode)
        self.assertIn("content mismatch", completed.stderr)

    def test_missing_compiled_catalogue_fails(self):
        def mutate(work):
            (work / "i18n" / "whaleread_en.qm").unlink()
        completed = self.run_scoped(self.fixture(mutate), "--source-only")
        self.assertEqual(1, completed.returncode)
        self.assertIn("missing compiled catalogue", completed.stderr)

    def test_unloadable_compiled_catalogue_fails(self):
        def mutate(work):
            (work / "i18n" / "whaleread_en.qm").write_bytes(b"not a catalogue")
            future = time.time() + 120
            os.utime(work / "i18n" / "whaleread_en.qm", (future, future))
        completed = self.run_scoped(self.fixture(mutate), "--source-only")
        self.assertEqual(1, completed.returncode)
        self.assertIn("does not load", completed.stderr)

    def test_missing_catalogue_source_fails(self):
        def mutate(work):
            (work / "i18n" / "whaleread_en.ts").unlink()
        completed = self.run_scoped(self.fixture(mutate), "--source-only")
        self.assertEqual(1, completed.returncode)
        self.assertIn("missing catalogue source", completed.stderr)


class BundleGateTests(unittest.TestCase):
    """Bundle resource, inventory and version checks on a synthetic bundle."""

    def bundle(self, *, version="1.19.0", build="67", localizations=("zh_CN", "en"),
               development_region="en"):
        raw = tempfile.mkdtemp(prefix="release-bundle-")
        self.addCleanup(shutil.rmtree, raw, True)
        app = Path(raw) / "鲸读.app"
        resources = app / "Contents" / "Resources"
        (resources / "PySide6/Qt/translations").mkdir(parents=True)
        (resources / "runtime/tools").mkdir(parents=True)
        shutil.copytree(ROOT / "ui", resources / "ui")
        shutil.copytree(ROOT / "runtime/lib", resources / "runtime/lib",
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(ROOT / "assets", resources / "assets")
        shutil.copy(ROOT / "packaging/PrivacyInfo.xcprivacy", resources / "PrivacyInfo.xcprivacy")
        import certifi
        (resources / 'certifi').mkdir()
        shutil.copy(certifi.where(), resources / 'certifi/cacert.pem')
        (resources / "docs").mkdir()
        shutil.copy(ROOT / "docs/PRIVACY.md", resources / "docs/PRIVACY.md")
        shutil.copytree(ROOT / "packaging/licenses", resources / "licenses")
        (resources / "licenses/WhaleRead").mkdir()
        shutil.copy(ROOT / "LICENSE", resources / "licenses/WhaleRead/LICENSE")
        (resources / "i18n").mkdir(parents=True)
        for name in ("SOURCES.txt", "whaleread_en.ts", "whaleread_en.qm"):
            shutil.copy(ROOT / "i18n" / name, resources / "i18n" / name)
        for name in WORKERS:
            shutil.copy(ROOT / "runtime/tools" / name, resources / "runtime/tools" / name)
        from PySide6.QtCore import QLibraryInfo
        qt_root = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
        for name in ("qtbase_zh_CN.qm", "qtbase_en.qm"):
            shutil.copy(qt_root / name, resources / "PySide6/Qt/translations" / name)
        executable = app / "Contents/MacOS/鲸读"
        executable.parent.mkdir(parents=True)
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        shutil.copy(executable, executable.with_name("WhaleReadWorker"))
        purchase = app / 'Contents/Frameworks/WhaleReadPurchase.dylib'
        purchase.parent.mkdir(parents=True)
        purchase.write_bytes(b'fixture-native-code')
        info = {"CFBundleName": "鲸读", "CFBundleShortVersionString": version,
                "CFBundleIdentifier": "local.sindy.jingdu.storeqa.example",
                "WhaleReadReceiptValidation": "qa",
                "CFBundleExecutable": "鲸读", "LSBackgroundOnly": False,
                "LSMinimumSystemVersion": "15.0",
                "CFBundleVersion": build,
                "CFBundleDevelopmentRegion": development_region,
                "LSApplicationCategoryType": "public.app-category.reference"}
        if localizations is not None:
            info["CFBundleLocalizations"] = list(localizations)
        (app / "Contents" / "Info.plist").write_bytes(plistlib.dumps(info))
        import json
        (resources / 'STORE_LAUNCHER.json').write_text(json.dumps(dict(
            mode='qa', bundle_identifier=info['CFBundleIdentifier'], real_purchase_verified=False)))
        return app

    def test_complete_bundle_passes(self):
        completed = run_gate(str(self.bundle()))
        self.assertEqual(0, completed.returncode, completed.stderr)

    def test_qa_purchase_bypass_cannot_use_a_production_identity(self):
        app = self.bundle()
        path = app / 'Contents/Info.plist'
        info = plistlib.loads(path.read_bytes())
        info['CFBundleIdentifier'] = 'com.example.whaleread'
        path.write_bytes(plistlib.dumps(info))
        result = run_gate(str(app))
        self.assertEqual(result.returncode, 1)
        self.assertIn('QA purchase bypass', result.stderr)

    def test_missing_paid_app_entry_metadata_fails(self):
        app = self.bundle()
        (app / 'Contents/Resources/STORE_LAUNCHER.json').unlink()
        result = run_gate(str(app))
        self.assertEqual(result.returncode, 1)
        self.assertIn('invalid application distribution', result.stderr)

    def test_missing_application_license_fails(self):
        app = self.bundle()
        (app / 'Contents/Resources/licenses/WhaleRead/LICENSE').unlink()
        result = run_gate(str(app))
        self.assertEqual(result.returncode, 1)
        self.assertIn('licenses/WhaleRead/LICENSE', result.stderr)

    def test_missing_purchase_verification_library_fails(self):
        app = self.bundle()
        (app / 'Contents/Frameworks/WhaleReadPurchase.dylib').unlink()
        result = run_gate(str(app))
        self.assertEqual(result.returncode, 1)
        self.assertIn('purchase verification library', result.stderr)

    def test_missing_application_catalogue_fails(self):
        app = self.bundle()
        (app / "Contents/Resources/i18n/whaleread_en.qm").unlink()
        completed = run_gate(str(app))
        self.assertEqual(1, completed.returncode)
        self.assertIn("whaleread_en.qm", completed.stderr)

    def test_missing_qt_catalogue_fails(self):
        app = self.bundle()
        (app / "Contents/Resources/PySide6/Qt/translations/qtbase_zh_CN.qm").unlink()
        completed = run_gate(str(app))
        self.assertEqual(1, completed.returncode)
        self.assertIn("qtbase_zh_CN.qm", completed.stderr)

    def test_empty_resource_fails(self):
        app = self.bundle()
        (app / "Contents/Resources/i18n/whaleread_en.qm").write_bytes(b"")
        completed = run_gate(str(app))
        self.assertEqual(1, completed.returncode)
        self.assertIn("whaleread_en.qm", completed.stderr)

    def test_valid_but_wrong_application_catalogue_fails(self):
        app = self.bundle()
        from PySide6.QtCore import QLibraryInfo
        qt_root = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
        shutil.copy(qt_root / "qtbase_en.qm",
                    app / "Contents/Resources/i18n/whaleread_en.qm")
        completed = run_gate(str(app))
        self.assertEqual(1, completed.returncode)
        self.assertIn("differs from source", completed.stderr)

    def test_corrupt_qt_catalogue_fails(self):
        app = self.bundle()
        path = app / "Contents/Resources/PySide6/Qt/translations/qtbase_en.qm"
        path.write_bytes(b"non-empty garbage")
        completed = run_gate(str(app))
        self.assertEqual(1, completed.returncode)
        self.assertIn("differs from selected interpreter", completed.stderr)

    def test_inventory_gap_fails(self):
        app = self.bundle()
        (app / "Contents/Resources/ui/ReviewPane.qml").unlink()
        completed = run_gate(str(app))
        self.assertEqual(1, completed.returncode)
        self.assertIn("ReviewPane.qml", completed.stderr)

    def test_worker_cannot_be_the_app_entry(self):
        app = self.bundle()
        path = app / 'Contents/Info.plist'
        info = plistlib.loads(path.read_bytes())
        info.update(CFBundleExecutable='WhaleReadWorker', LSBackgroundOnly=True)
        path.write_bytes(plistlib.dumps(info))
        result = run_gate(str(app))
        self.assertEqual(result.returncode, 1)
        self.assertIn('visible GUI', result.stderr)

    def test_minimum_macos_cannot_understate_the_runtime_requirement(self):
        app = self.bundle()
        path = app / 'Contents/Info.plist'
        info = plistlib.loads(path.read_bytes())
        info['LSMinimumSystemVersion'] = '13.0'
        path.write_bytes(plistlib.dumps(info))
        result = run_gate(str(app))
        self.assertEqual(result.returncode, 1)
        self.assertIn('minimum macOS', result.stderr)

    def test_missing_tls_roots_fails(self):
        app = self.bundle()
        (app / 'Contents/Resources/certifi/cacert.pem').unlink()
        result = run_gate(str(app))
        self.assertEqual(result.returncode, 1)
        self.assertIn('certifi/cacert.pem', result.stderr)

    def test_wrong_version_fails(self):
        completed = run_gate(str(self.bundle(version="1.17.1", build="63")))
        self.assertEqual(1, completed.returncode)
        self.assertIn("CFBundleShortVersionString", completed.stderr)
        self.assertIn("CFBundleVersion", completed.stderr)

    def test_localizations_without_a_catalogue_fails(self):
        app = self.bundle(localizations=("zh_CN", "en"))
        (app / "Contents/Resources/i18n/whaleread_en.qm").unlink()
        completed = run_gate(str(app))
        self.assertEqual(1, completed.returncode)
        self.assertIn("whaleread_en.qm", completed.stderr)

    def test_missing_localization_metadata_fails(self):
        completed = run_gate(str(self.bundle(localizations=None, development_region="")))
        self.assertEqual(1, completed.returncode)
        self.assertIn("CFBundleLocalizations", completed.stderr)
        self.assertIn("CFBundleDevelopmentRegion", completed.stderr)

    def test_modified_qml_fails_even_when_present(self):
        app = self.bundle()
        (app / "Contents/Resources/ui/Main.qml").write_text(
            "this is not the reviewed Main.qml", encoding="utf-8")
        completed = run_gate(str(app))
        self.assertEqual(1, completed.returncode)
        self.assertIn("ui/Main.qml", completed.stderr)

    def test_build_path_leak_fails(self):
        app = self.bundle()
        leaked = app / "Contents/Resources/runtime/lib/locale_service.py"
        leaked.write_text(f'PATH = "{ROOT}"\n', encoding="utf-8")
        completed = run_gate(str(app))
        self.assertEqual(1, completed.returncode)
        self.assertIn("host path leaked", completed.stderr)

    def test_missing_bundle_fails(self):
        completed = run_gate("/nonexistent/鲸读.app")
        self.assertEqual(1, completed.returncode)
        self.assertIn("bundle not found", completed.stderr)


class BuildScriptGateTests(unittest.TestCase):
    """The build entry point refuses to run without a selected interpreter."""

    def run_build(self, env):
        return subprocess.run(["/bin/zsh", str(ROOT / "scripts/build_native_app.sh"), "--build-only"],
                              capture_output=True, text=True, cwd=str(ROOT), env=env)

    def test_build_requires_an_explicit_interpreter(self):
        env = {k: v for k, v in os.environ.items() if k != "HY_BUILD_PYTHON"}
        completed = self.run_build(env)
        self.assertEqual(2, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("HY_BUILD_PYTHON is required", completed.stderr)

    def test_build_rejects_a_missing_interpreter(self):
        env = dict(os.environ, HY_BUILD_PYTHON="/nonexistent/python")
        completed = self.run_build(env)
        self.assertEqual(2, completed.returncode)
        self.assertIn("not executable", completed.stderr)

    def test_build_rejects_an_interpreter_without_pyside6(self):
        env = dict(os.environ, HY_BUILD_PYTHON="/usr/bin/python3")
        completed = self.run_build(env)
        self.assertEqual(3, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("PySide6 is not importable", completed.stderr)

    def test_build_ignores_pythonpath_module_injection(self):
        raw = tempfile.mkdtemp(prefix="fake-build-modules-")
        self.addCleanup(shutil.rmtree, raw, True)
        fake = Path(raw)
        for package in ("PySide6", "PyInstaller"):
            (fake / package).mkdir()
            (fake / package / "__init__.py").write_text("# fake\n", encoding="utf-8")
        env = dict(os.environ, HY_BUILD_PYTHON="/usr/bin/python3", PYTHONPATH=str(fake))
        completed = self.run_build(env)
        self.assertEqual(3, completed.returncode, completed.stdout + completed.stderr)
        self.assertIn("PySide6 is not importable", completed.stderr)

    def test_tool_resolution_never_falls_back_to_path(self):
        raw = tempfile.mkdtemp(prefix="fake-build-tool-")
        self.addCleanup(shutil.rmtree, raw, True)
        fake_tool = Path(raw) / "definitely-not-a-real-qt-tool"
        fake_tool.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_tool.chmod(0o755)
        env = dict(os.environ, PATH=f"{raw}:{os.environ.get('PATH', '')}")
        completed = subprocess.run(
            [PYTHON, "-I", "-B", str(ROOT / "scripts/resolve_build_tools.py"),
             "tool", fake_tool.name],
            capture_output=True, text=True, cwd=str(ROOT), env=env)
        self.assertEqual(3, completed.returncode)
        self.assertIn("not found beside", completed.stderr)

    def test_build_rejects_an_interpreter_without_pyinstaller(self):
        """A valid runtime interpreter that cannot build must also fail closed."""
        probe = subprocess.run(
            [PYTHON, "-c", "import importlib.util,sys;"
             "sys.exit(0 if importlib.util.find_spec('PyInstaller') else 9)"],
            capture_output=True)
        if probe.returncode == 0:
            self.skipTest("this interpreter does have PyInstaller")
        env = dict(os.environ, HY_BUILD_PYTHON=PYTHON)
        completed = self.run_build(env)
        self.assertEqual(3, completed.returncode)
        self.assertIn("PyInstaller is not importable", completed.stderr)

    def test_build_refuses_to_install(self):
        env = dict(os.environ, HY_BUILD_PYTHON=PYTHON)
        completed = subprocess.run(
            ["/bin/zsh", str(ROOT / "scripts/build_native_app.sh"), "--install-built"],
            capture_output=True, text=True, cwd=str(ROOT), env=env)
        self.assertEqual(2, completed.returncode)
        self.assertIn("candidate only", completed.stderr)

    def test_build_script_has_no_hardcoded_worktree_venv(self):
        text = (ROOT / "scripts/build_native_app.sh").read_text(encoding="utf-8")
        self.assertNotIn('$root_dir/.venv/bin/python', text)
        self.assertNotIn("chflags -R nohidden \"$root_dir/.venv", text)


class PackagingSpecTests(unittest.TestCase):
    """The spec must promise exactly what the gate requires."""

    def spec(self):
        return (ROOT / "packaging/WhaleRead.spec").read_text(encoding="utf-8")

    def test_spec_ships_the_application_catalogue(self):
        text = self.spec()
        self.assertIn("(str(root/'i18n'), 'i18n')", text)

    def test_spec_ships_the_qt_catalogues(self):
        text = self.spec()
        self.assertIn("qtbase_zh_CN.qm", text)
        self.assertIn("qtbase_en.qm", text)
        self.assertIn("qt_translation_data", text)

    def test_spec_fails_when_a_required_catalogue_is_missing(self):
        text = self.spec()
        self.assertIn("Missing Qt translation catalogues", text)
        self.assertIn("raise SystemExit", text)

    def test_spec_declares_the_candidate_version(self):
        text = self.spec()
        self.assertIn("'CFBundleShortVersionString':'1.19.0'", text)
        self.assertIn("'CFBundleVersion':'67'", text)

    def test_spec_declares_localizations_only_with_resources(self):
        text = self.spec()
        self.assertIn("'CFBundleLocalizations':['zh_CN', 'en']", text)
        self.assertIn("'CFBundleDevelopmentRegion':'en'", text)

    def test_spec_excludes_legacy_typescript_workers(self):
        text = self.spec()
        self.assertIn("worker_data", text)
        self.assertNotIn("(str(root/'runtime/tools'), 'runtime/tools')", text)


class BundleIdentityTests(unittest.TestCase):
    def test_digest_survives_move_and_covers_symlink_target(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        try:
            from hash_app_bundle import bundle_digest
        finally:
            sys.path.pop(0)
        raw = Path(tempfile.mkdtemp(prefix="bundle-digest-"))
        self.addCleanup(shutil.rmtree, raw, True)
        first = raw / "first/鲸读.app"
        (first / "Contents/Resources").mkdir(parents=True)
        (first / "Contents/Resources/value.txt").write_text("same", encoding="utf-8")
        (first / "Contents/link").symlink_to("Resources/value.txt")
        second = raw / "second/鲸读.app"
        shutil.copytree(first, second, symlinks=True)
        first_digest = bundle_digest(first)[0]
        self.assertEqual(first_digest, bundle_digest(second)[0])
        (second / "Contents/link").unlink()
        (second / "Contents/link").symlink_to("Resources/other.txt")
        self.assertNotEqual(first_digest, bundle_digest(second)[0])

    def test_bundle_qa_never_rewrites_a_signed_worker(self):
        text = (ROOT / "scripts/qa_bundle_only.py").read_text(encoding="utf-8")
        self.assertNotIn("write_text(PROBE", text)
        self.assertNotIn("target.write", text)
        self.assertIn("bundle_ui_probe.py", text)


if __name__ == "__main__":
    unittest.main()
