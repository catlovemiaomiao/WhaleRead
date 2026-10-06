"""Load the signed candidate's real QML tree and switch its visible locale.

The candidate is never modified.  Its allowlisted ``bundle_ui_probe.py`` worker
creates the production controller and ``Main.qml`` against temporary settings,
workspace, and cache roots.  The wrapper launches it from an unrelated directory
with source-import paths removed and fails unless a clean install starts in
English and a real QML control changes English -> Chinese -> English without
QML binding/reference errors.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import plistlib


def static_checks(app: Path) -> dict:
    resources = app / "Contents/Resources"
    return {
        "app": str(app),
        "executable": (app / "Contents/MacOS/鲸读").is_file(),
        "probe": (resources / "runtime/tools/bundle_ui_probe.py").is_file(),
        "catalogue": (resources / "i18n/whaleread_en.qm").is_file(),
        "qt_zh": (resources / "PySide6/Qt/translations/qtbase_zh_CN.qm").is_file(),
        "qt_en": (resources / "PySide6/Qt/translations/qtbase_en.qm").is_file(),
        "main_qml": (resources / "ui/Main.qml").is_file(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Signed bundle-only QML verification")
    parser.add_argument("app")
    parser.add_argument("--json", default="")
    args = parser.parse_args()
    app = Path(args.app).resolve()
    if not app.is_dir():
        print(f"bundle not found: {app}", file=sys.stderr)
        return 2
    static = static_checks(app)
    executable = app / "Contents/MacOS/鲸读"
    info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
    standalone = info.get('WhaleReadReceiptValidation') == 'standalone'
    policy = subprocess.run([str(executable), '--distribution-self-test' if standalone else '--receipt-policy-self-test'],
                            capture_output=True, text=True, timeout=30)
    policy_report = json.loads(policy.stdout) if policy.returncode == 0 else {}
    # A Store build requires an actual purchase before it can start its reader.
    # Do not bypass the native entry or run an inherited child from the shell.
    if info.get('WhaleReadReceiptValidation') == 'store':
        summary = dict(static=static, purchase_policy=policy_report,
            production_store_launch_tested=False, packaged_ui_tested=False,
            status='Store-delivered purchase and UI acceptance pending')
        if args.json:
            Path(args.json).write_text(json.dumps(summary, indent=2) + '\n')
        print(json.dumps(summary, indent=2))
        return 0 if (all(v for k, v in static.items() if k != 'app') and policy.returncode == 0
                     and policy_report.get('policy_tests_passed') and policy_report.get('bundle_identity_matches')) else 1
    probe = app / "Contents/Resources/runtime/tools/bundle_ui_probe.py"
    workdir = Path(tempfile.mkdtemp(prefix="bundle-ui-cwd-")).resolve()
    env = {
        key: value for key, value in os.environ.items()
        if key not in {
            "PYTHONHOME", "PYTHONPATH", "QT_PLUGIN_PATH",
            "QT_QPA_PLATFORM_PLUGIN_PATH", "QML2_IMPORT_PATH",
        }
    }
    env.update({
        "PYTHONPATH": "",
        "QT_QPA_PLATFORM": "offscreen",
        "QT_QUICK_CONTROLS_STYLE": "Basic",
        "QSG_RENDER_LOOP": "basic",
    })
    try:
        completed = subprocess.run(
            [str(executable), "-B", str(probe)],
            capture_output=True,
            text=True,
            cwd=str(workdir),
            env=env,
            timeout=240,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    report: dict = {}
    for line in completed.stdout.splitlines():
        if line.startswith("BUNDLE_UI_PROBE "):
            report = json.loads(line.removeprefix("BUNDLE_UI_PROBE "))
            break
    required = (
        "frozen", "root_loaded", "settings_button_found", "catalogue_exists",
        "switched", "switched_back", "workspace_isolated", "storage_isolated",
        "sandbox_entitlement_active", "welcome_visible", "onboarding_saved",
    )
    problems: list[str] = []
    if (policy.returncode != 0 or not policy_report.get('bundle_identity_matches')
            or not policy_report.get('metadata_verified' if standalone else 'policy_tests_passed')
            or (standalone and policy_report.get('purchase_required') is not False)):
        problems.append('application distribution policy checks failed')
    for key, present in static.items():
        if key != "app" and not present:
            problems.append(f"bundle is missing {key}")
    if completed.returncode != 0:
        problems.append(f"packaged UI probe exited {completed.returncode}")
    for key in required:
        if not report.get(key):
            problems.append(f"{key} was not satisfied")
    if report.get("en_text") != "Settings":
        problems.append("clean-install English settings text was not observed")
    if report.get("zh_text") != "设置":
        problems.append("Chinese settings control text was not observed")
    if report.get("en_text_again") != "Settings":
        problems.append("English settings control was not restored")
    if report.get("source_tree_on_path"):
        problems.append("a source checkout appeared on sys.path")
    if report.get('settings_domain') != report.get('bundle_identifier'):
        problems.append('settings are not isolated by application identity')
    if '/Library/Containers/' + str(report.get('bundle_identifier')) + '/Data' not in str(report.get('application_home')):
        problems.append('the probe did not run in its real sandbox container')
    if report.get("qml_errors"):
        problems.append("QML binding/reference errors were emitted")
    summary = {
        "static": static,
        "run_exit": completed.returncode,
        "distribution_policy" if standalone else "purchase_policy": policy_report,
        "production_store_launch_tested": False,
        "report": report,
        "stdout_tail": completed.stdout[-1200:],
        "stderr_tail": completed.stderr[-1200:],
        "problems": problems,
    }
    if args.json:
        Path(args.json).write_text(
            json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if problems:
        print("bundle-only UI verification failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    print("PASS: signed bundle loaded Main.qml, started in English, and switched its visible locale")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
