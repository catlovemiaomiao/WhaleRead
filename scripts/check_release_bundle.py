"""Fail a release candidate that is incomplete, stale, or not self-contained.

    python scripts/check_release_bundle.py <path/to/鲸读.app>
    python scripts/check_release_bundle.py --source-only

The source gate compares every reviewed TS message with the compiled QM. The
bundle gate then requires the packaged application and Qt catalogues to be the
same bytes selected by the build interpreter, and requires every shipped
WhaleRead UI/runtime resource to match the source snapshot. Existence or
timestamps alone are never accepted as freshness evidence.
"""
from __future__ import annotations

import argparse
import json
import importlib.util
import ipaddress
import os
import plistlib
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

EXPECTED_VERSION = "1.19.1"
EXPECTED_BUILD = "68"
MINIMUM_MACOS = "15.0"
DECLARED_LOCALIZATIONS = ("zh_CN", "en")
DEVELOPMENT_REGION = "en"

CATALOGUE = ROOT / "i18n" / "whaleread_en.qm"
CATALOGUE_SOURCE = ROOT / "i18n" / "whaleread_en.ts"

SHIPPED_WORKERS = (
    "ask_ai_request.py",
    "bundle_ui_probe.py",
    "bundle_api_probe.py",
    "post_edit.py",
    "probe_model.py",
    "research_ocr.py",
    "research_worker.py",
    "translate_range.py",
    "worker_entry.py",
)

PLACEHOLDER = re.compile(r"%(\d+|n|L\d+)")
HOST_PATH = re.compile(r"(?:/Users/[^/\s'\"]+|/private/tmp|/tmp)/")
TEXT_SUFFIXES = {
    ".js", ".json", ".md", ".plist", ".py", ".qml", ".txt", ".ts", ".yaml", ".yml"
}
FORBIDDEN_PUBLIC_BYTES = (
    str(Path.home()).encode('utf-8'),
)
API_KEY_LITERAL = re.compile(rb"sk-[A-Za-z0-9_-]{20,}")
IP_ENDPOINT_LITERAL = re.compile(rb"https?://([0-9]{1,3}(?:\.[0-9]{1,3}){3})(?=[:/\s'\"])")


def placeholders(text: str) -> list[str]:
    return sorted(PLACEHOLDER.findall(text.replace("%%", "")))


def catalogue_rows(tree: ET.ElementTree) -> tuple[list[tuple], list[str]]:
    """Return validated rows used for an entry-by-entry QM comparison."""
    rows: list[tuple] = []
    problems: list[str] = []
    for context in tree.getroot().findall("context"):
        name = context.findtext("name") or ""
        for message in context.findall("message"):
            source = message.findtext("source") or ""
            comment = message.findtext("comment") or ""
            translation = message.find("translation")
            if translation is None:
                problems.append(f"[{name}] missing translation: {source}")
                continue
            kind = translation.get("type")
            if kind in {"unfinished", "obsolete", "vanished"}:
                problems.append(f"[{name}] {kind}: {source}")
                continue
            if message.get("numerus") == "yes":
                forms = [node.text or "" for node in translation.findall("numerusform")]
                if len(forms) != 2:
                    problems.append(f"[{name}] expected 2 plural forms, got {len(forms)}: {source}")
                    continue
                if any(not form.strip() for form in forms):
                    problems.append(f"[{name}] empty plural translation: {source}")
                    continue
                if any(placeholders(form) != placeholders(source) for form in forms):
                    problems.append(f"[{name}] plural placeholder mismatch: {source}")
                    continue
                rows.append((name, source, comment, tuple(forms), True))
            else:
                value = translation.text or ""
                if not value.strip():
                    problems.append(f"[{name}] empty translation: {source}")
                    continue
                if placeholders(value) != placeholders(source):
                    problems.append(f"[{name}] placeholder mismatch: {source}")
                    continue
                rows.append((name, source, comment, (value,), False))
    return rows, problems


def compiled_catalogue_problems(path: Path, rows: list[tuple], label: str) -> list[str]:
    """Load a QM and compare every reviewed entry with the TS values."""
    if not path.is_file():
        return [f"missing {label}: {path}"]
    if path.stat().st_size == 0:
        return [f"empty {label}: {path}"]
    from PySide6.QtCore import QTranslator

    translator = QTranslator()
    if not translator.load(str(path)) or translator.isEmpty():
        return [f"{label} does not load: {path}"]
    problems: list[str] = []
    for context, source, comment, values, numerus in rows:
        if numerus:
            for number in (1, 2):
                # QTranslator.translate() selects the plural form but leaves %n
                # unsubstituted; QCoreApplication performs that final substitution.
                expected = values[0 if number == 1 else 1]
                actual = translator.translate(context, source, comment or None, number)
                if actual != expected:
                    problems.append(
                        f"{label} content mismatch [{context}] n={number}: {source}")
                    break
        else:
            actual = translator.translate(context, source, comment or None)
            if actual != values[0]:
                problems.append(f"{label} content mismatch [{context}]: {source}")
        if len(problems) >= 20:
            problems.append(f"{label} has additional content mismatches")
            break
    return problems


def loadable_qm(path: Path, label: str) -> list[str]:
    from PySide6.QtCore import QTranslator

    translator = QTranslator()
    if not path.is_file():
        return [f"missing {label}: {path}"]
    if path.stat().st_size == 0 or not translator.load(str(path)) or translator.isEmpty():
        return [f"{label} does not load or is empty: {path}"]
    return []


def check_source() -> tuple[list[str], list[tuple]]:
    problems: list[str] = []
    if not CATALOGUE_SOURCE.is_file():
        return [f"missing catalogue source: {CATALOGUE_SOURCE.relative_to(ROOT)}"], []
    try:
        tree = ET.parse(CATALOGUE_SOURCE)
    except ET.ParseError as exc:
        return [f"catalogue source is not valid XML: {exc}"], []
    rows, catalogue_problems = catalogue_rows(tree)
    problems.extend(catalogue_problems)
    if not CATALOGUE.is_file():
        problems.append(
            f"missing compiled catalogue: {CATALOGUE.relative_to(ROOT)} "
            "(run scripts/i18n_compile.py)")
        return problems, rows
    if CATALOGUE.stat().st_mtime < CATALOGUE_SOURCE.stat().st_mtime:
        problems.append(
            f"compiled catalogue is older than its source: {CATALOGUE.name} < "
            f"{CATALOGUE_SOURCE.name} (run scripts/i18n_compile.py)")
    if rows:
        problems.extend(compiled_catalogue_problems(CATALOGUE, rows, "compiled catalogue"))
    public_sources = {"native_launcher.py": ROOT / "native_launcher.py"}
    public_sources.update(source_inventory())
    for relative, path in public_sources.items():
        if not path.is_file():
            continue
        data = path.read_bytes()
        if relative.startswith(('native_launcher.py', 'runtime/', 'ui/', 'docs/')) and HOST_PATH.search(data.decode('utf-8', errors='replace')):
            problems.append(f"private release literal in {relative}: developer-local path")
        for forbidden in FORBIDDEN_PUBLIC_BYTES:
            if forbidden in data:
                problems.append(
                    f"private release literal in {relative}: {forbidden.decode('utf-8')}")
        if API_KEY_LITERAL.search(data):
            problems.append(f"API key-shaped literal in {relative}")
        for match in IP_ENDPOINT_LITERAL.finditer(data):
            try:
                local = ipaddress.ip_address(match.group(1).decode('ascii')).is_loopback
            except ValueError:
                local = False
            if not local:
                problems.append(f"private release literal in {relative}: non-loopback endpoint")
                break
    return problems, rows


def source_inventory() -> dict[str, Path]:
    """Files deliberately shipped as readable WhaleRead resources."""
    result: dict[str, Path] = {}
    groups = (
        (ROOT / "ui", "ui", lambda path: path.is_file()),
        (ROOT / "runtime/lib", "runtime/lib",
         lambda path: path.is_file() and path.suffix == ".py"),
        (ROOT / "assets", "assets", lambda path: path.is_file()),
    )
    for source_root, destination, include in groups:
        for path in sorted(source_root.rglob("*")):
            if include(path):
                relative = path.relative_to(source_root).as_posix()
                result[f"{destination}/{relative}"] = path
    for name in SHIPPED_WORKERS:
        result[f"runtime/tools/{name}"] = ROOT / "runtime/tools" / name
    for name in ("SOURCES.txt", "whaleread_en.ts", "whaleread_en.qm"):
        result[f"i18n/{name}"] = ROOT / "i18n" / name
    if (ROOT / 'LICENSE').is_file():
        result['licenses/WhaleRead/LICENSE'] = ROOT / 'LICENSE'
    result['docs/PRIVACY.md'] = ROOT / 'docs/PRIVACY.md'
    result['PrivacyInfo.xcprivacy'] = ROOT / 'packaging/PrivacyInfo.xcprivacy'
    for path in sorted((ROOT / 'packaging/licenses').rglob('*')):
        if path.is_file():
            result['licenses/' + path.relative_to(ROOT / 'packaging/licenses').as_posix()] = path
    return result


def safe_bundle_file(resources: Path, relative: str) -> tuple[Path, str | None]:
    path = resources / relative
    if not path.is_file():
        return path, f"missing from bundle inventory: Contents/Resources/{relative}"
    if path.is_symlink():
        return path, f"bundle resource must not be a symlink: Contents/Resources/{relative}"
    try:
        path.resolve().relative_to(resources.resolve())
    except ValueError:
        return path, f"bundle resource escapes Resources: Contents/Resources/{relative}"
    return path, None


def check_bundle(app: Path, rows: list[tuple]) -> list[str]:
    problems: list[str] = []
    resources = app / "Contents/Resources"
    if app.name != "鲸读.app":
        problems.append(f"unexpected bundle name: {app.name}")
    executable = app / "Contents/MacOS/鲸读"
    if not executable.is_file() or executable.is_symlink() or not os.access(executable, os.X_OK):
        problems.append("missing or non-executable main program: Contents/MacOS/鲸读")
    helper = app / 'Contents/MacOS/WhaleReadWorker'
    if not helper.is_file() or helper.is_symlink() or not os.access(helper, os.X_OK):
        problems.append('missing bundled worker helper')
    purchase = app / 'Contents/Frameworks/WhaleReadPurchase.dylib'
    standalone_manifest = resources / 'DISTRIBUTION.json'
    if standalone_manifest.is_file():
        if purchase.exists():
            problems.append('unexpected purchase launcher in standalone edition')
    elif not purchase.is_file() or purchase.is_symlink():
        problems.append('missing native purchase verification library')
    import certifi
    ca_file, error = safe_bundle_file(resources, 'certifi/cacert.pem')
    if error:
        problems.append(error)
    elif ca_file.read_bytes() != Path(certifi.where()).read_bytes():
        problems.append('packaged TLS trust bundle differs from the selected build runtime')

    expected = source_inventory()
    for relative, source in expected.items():
        packaged, error = safe_bundle_file(resources, relative)
        if error:
            problems.append(error)
            continue
        if not source.is_file():
            problems.append(f"source inventory entry is missing: {source.relative_to(ROOT)}")
        elif packaged.read_bytes() != source.read_bytes():
            problems.append(f"bundle resource differs from source: Contents/Resources/{relative}")

    tools = resources / "runtime/tools"
    if tools.is_dir():
        actual_workers = {path.name for path in tools.iterdir() if path.is_file()}
        unexpected = sorted(actual_workers - set(SHIPPED_WORKERS))
        if unexpected:
            problems.append("unexpected runtime/tools files: " + ", ".join(unexpected))

    app_qm = resources / "i18n/whaleread_en.qm"
    if rows and app_qm.is_file():
        problems.extend(
            compiled_catalogue_problems(app_qm, rows, "bundled application catalogue"))

    from PySide6.QtCore import QLibraryInfo
    qt_source = Path(QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath))
    for name in ("qtbase_zh_CN.qm", "qtbase_en.qm"):
        relative = f"PySide6/Qt/translations/{name}"
        packaged, error = safe_bundle_file(resources, relative)
        if error:
            problems.append(error)
            continue
        # qtbase_en.qm is an official 33-byte empty catalogue because Qt's source
        # strings are already English.  Chinese must contain translations; both
        # files must still be byte-identical to the selected interpreter below.
        if name == "qtbase_zh_CN.qm":
            problems.extend(loadable_qm(packaged, f"bundled Qt catalogue {name}"))
        selected = qt_source / name
        if not selected.is_file():
            problems.append(f"selected interpreter is missing Qt catalogue: {selected}")
        elif packaged.read_bytes() != selected.read_bytes():
            problems.append(f"bundled Qt catalogue differs from selected interpreter: {name}")

    plist_path = app / "Contents/Info.plist"
    if not plist_path.is_file():
        problems.append("missing from bundle inventory: Contents/Info.plist")
        info = {}
    else:
        try:
            info = plistlib.loads(plist_path.read_bytes())
        except Exception as exc:  # noqa: BLE001
            problems.append(f"Info.plist is unreadable: {exc}")
            info = {}
    version = str(info.get("CFBundleShortVersionString") or "")
    build = str(info.get("CFBundleVersion") or "")
    if info.get('LSApplicationCategoryType') != 'public.app-category.reference':
        problems.append('missing Reference application category')
    if info.get('CFBundleExecutable') != '鲸读' or info.get('LSBackgroundOnly'):
        problems.append('bundle must launch the visible GUI, not its worker helper')
    if info.get('LSMinimumSystemVersion') != MINIMUM_MACOS:
        problems.append('minimum macOS must match the verified runtime: ' + MINIMUM_MACOS)
    try:
        # The build invokes this gate with Python -I, so sibling scripts are
        # deliberately absent from sys.path. Load only this reviewed helper.
        spec = importlib.util.spec_from_file_location('store_launcher_policy', ROOT / 'scripts/build_store_launcher.py')
        policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(policy)
        mode = info.get('WhaleReadReceiptValidation', '')
        policy.validate_mode(mode, info.get('CFBundleIdentifier', ''))
        if mode == 'standalone':
            helper_spec = importlib.util.spec_from_file_location('standalone_distribution_policy', ROOT / 'runtime/lib/distribution_policy.py')
            helper_policy = importlib.util.module_from_spec(helper_spec)
            helper_spec.loader.exec_module(helper_policy)
            helper_policy.validate_standalone(info, json.loads(standalone_manifest.read_text()))
            if (resources / 'STORE_LAUNCHER.json').exists():
                raise ValueError('A standalone app must not include paid launcher metadata')
        else:
            launcher = json.loads((resources / 'STORE_LAUNCHER.json').read_text())
            if (launcher.get('mode') != mode or launcher.get('bundle_identifier') != info.get('CFBundleIdentifier')
                    or launcher.get('real_purchase_verified') is not False):
                raise ValueError('Paid-app launcher metadata does not match its identity and scope')
    except (ValueError, OSError, TypeError) as exc:
        problems.append('invalid application distribution: ' + str(exc))
    if version != EXPECTED_VERSION:
        problems.append(
            f"CFBundleShortVersionString is {version!r}, expected {EXPECTED_VERSION!r}")
    if build != EXPECTED_BUILD:
        problems.append(f"CFBundleVersion is {build!r}, expected {EXPECTED_BUILD!r}")
    declared = [str(item) for item in (info.get("CFBundleLocalizations") or [])]
    if sorted(declared) != sorted(DECLARED_LOCALIZATIONS):
        problems.append(
            f"CFBundleLocalizations is {declared!r}, expected "
            f"{list(DECLARED_LOCALIZATIONS)!r}")
    region = str(info.get("CFBundleDevelopmentRegion") or "")
    if region != DEVELOPMENT_REGION:
        problems.append(
            f"CFBundleDevelopmentRegion is {region!r}, expected {DEVELOPMENT_REGION!r}")

    for relative in expected:
        path = resources / relative
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        marker = HOST_PATH.search(text)
        if marker:
            problems.append(
                f"host path leaked into Contents/Resources/{relative}: {marker.group(0)}")

    # Scan every regular bundle file, including compiled archives and native
    # libraries, for the owner's known private endpoint and local account path.
    # This catches build-runtime leakage that a text-resource inventory cannot.
    for path in sorted(app.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            problems.append(f"could not inspect bundle file {path.relative_to(app)}: {exc}")
            continue
        for forbidden in FORBIDDEN_PUBLIC_BYTES:
            if forbidden in data:
                problems.append(
                    "private release literal in bundle file "
                    f"{path.relative_to(app)}: {forbidden.decode('utf-8')}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="Release candidate gate")
    parser.add_argument("app", nargs="?", help="path to 鲸读.app")
    parser.add_argument("--source-only", action="store_true")
    args = parser.parse_args()
    problems, rows = check_source()
    if not args.source_only:
        if not args.app:
            parser.error("an app path is required unless --source-only is given")
        app = Path(args.app).resolve()
        if not app.is_dir():
            problems.append(f"bundle not found: {app}")
        else:
            problems.extend(check_bundle(app, rows))
    if problems:
        print("release gate failed:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    scope = "source" if args.source_only else "source and bundle"
    print(f"PASS: {scope} release gates (version {EXPECTED_VERSION} build {EXPECTED_BUILD})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
