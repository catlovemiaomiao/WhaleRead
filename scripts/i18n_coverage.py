"""Deterministic interface-coverage check for the QML/JS surfaces.

    python scripts/i18n_coverage.py

Every Han string literal in the supported app's QML must be one of

* **wrapped** in ``qsTr(...)`` so it is extracted into the reviewed catalogue, or
* explicitly **allowed** below with a reason.

The allowlist is deliberately narrow: only the product name and wordmark are
not interface text.  This check never claims that *every* Han string in the
repository is interface text — publisher titles, book content, translations,
user notes, model evidence, paths, protocol values and stable object names live
in data or identifiers, not in QML literals, and are not scanned here.  The
JavaScript files injected into the reader page must carry no Han literal at
all: they receive their labels from QML at call time.

It also verifies that every ``qsTr`` literal actually appears in
``i18n/whaleread_en.ts``, so a wrapped-but-not-extracted string fails too.
Offline; exit code 1 on any finding.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from i18n_common import (CATALOGUE, ROOT, TranslationError, messages,
                         parse_catalogue, sources)

HAN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")

UI_DIR = ROOT / "ui"

# (file, literal) -> reason.  Keep this list narrow and justified.
ALLOWED: dict[tuple[str, str], str] = {
    ("ui/Main.qml", "鲸读"): "application name used as the window title (product identity)",
    ("ui/Main.qml", "鲸 读"): "brand wordmark in the header (product identity)",
}


def unescape(text: str) -> str:
    return text.replace('\\"', '"').replace("\\'", "'").replace("\\n", "\n")


def wrapped(text: str, position: int) -> bool:
    """True when the literal at ``position`` is the argument of a qsTr call."""
    return bool(re.search(r"qsTr\(\s*$", text[max(0, position - 60):position]))


def string_literals(text: str):
    """Yield ``(position, value)`` for JS/QML string literals.

    QML's JavaScript syntax accepts double quotes, single quotes and template
    literals.  The coverage gate must therefore inspect all three.  Comments
    are skipped so examples in developer notes do not become interface text.
    This small lexer deliberately does not interpret template substitutions:
    any Han text anywhere in the template is still a user-visible candidate.
    """
    index = 0
    size = len(text)
    while index < size:
        if text.startswith("//", index):
            newline = text.find("\n", index + 2)
            index = size if newline < 0 else newline + 1
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = size if end < 0 else end + 2
            continue
        quote = text[index]
        if quote not in ('"', "'", "`"):
            index += 1
            continue
        start = index
        index += 1
        value: list[str] = []
        while index < size:
            char = text[index]
            if char == "\\" and index + 1 < size:
                value.extend((char, text[index + 1]))
                index += 2
                continue
            if char == quote:
                index += 1
                break
            value.append(char)
            index += 1
        yield start, "".join(value)


def scan_qml(path: Path) -> list[tuple[int, str, bool]]:
    """(line, literal, wrapped) for every Han string literal."""
    text = path.read_text(encoding="utf-8")
    rows = []
    for position, value in string_literals(text):
        if not HAN.search(value):
            continue
        rows.append((text[:position].count("\n") + 1, value,
                     wrapped(text, position)))
    return rows


def scan_js(path: Path) -> list[tuple[int, str]]:
    text = path.read_text(encoding="utf-8")
    rows = []
    for position, value in string_literals(text):
        if HAN.search(value):
            rows.append((text[:position].count("\n") + 1, value))
    return rows


def check_qml(paths, catalogue=None, base=None) -> tuple[int, list[str], list[str]]:
    """Return (wrapped count, missing findings, unwrapped-but-allowed findings)."""
    root = Path(base) if base is not None else ROOT
    catalogue_sources: dict[str, set[str]] = {}
    for context, message in messages(parse_catalogue(catalogue or CATALOGUE)):
        catalogue_sources.setdefault(context, set()).add(message.findtext("source") or "")
    wrapped_count = 0
    missing: list[str] = []
    allowed: list[str] = []
    for item in paths:
        path = Path(item)
        try:
            relative = str(path.relative_to(root))
        except ValueError:
            relative = path.name
        context = path.stem
        for line, value, is_wrapped in scan_qml(path):
            if is_wrapped:
                wrapped_count += 1
                if unescape(value) not in catalogue_sources.get(context, set()):
                    missing.append(f"{relative}:{line} qsTr 未按 {context} 上下文提取：{value}")
                continue
            reason = ALLOWED.get((relative, value))
            if reason:
                allowed.append(f"{relative}:{line} {value} — {reason}")
            else:
                missing.append(f"{relative}:{line} 未翻译也未登记：{value}")
    return wrapped_count, missing, allowed


def check_js(paths, base=None) -> list[str]:
    root = Path(base) if base is not None else ROOT
    findings: list[str] = []
    for item in paths:
        path = Path(item)
        try:
            relative = path.relative_to(root)
        except ValueError:
            relative = path.name
        for line, value in scan_js(path):
            findings.append(f"{relative}:{line} JS 不应内嵌界面文字：{value}")
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 QML/JS 界面文字覆盖")
    parser.add_argument("--ui", default=str(UI_DIR), help="QML/JS 目录")
    args = parser.parse_args()
    directory = Path(args.ui)
    qml = sorted(directory.rglob("*.qml"))
    javascript = sorted(directory.rglob("*.js"))
    if not qml:
        raise TranslationError(f"{directory} 下没有 QML 文件")
    declared = {path.resolve() for path in sources()}
    omitted = [str(path.relative_to(ROOT)) for path in qml if path.resolve() not in declared]
    if omitted:
        raise TranslationError("QML 文件未列入 i18n/SOURCES.txt：" + ", ".join(omitted))
    wrapped_count, missing, allowed = check_qml(qml)
    missing += check_js(javascript)
    print(f"QML 文件 {len(qml)} 个，qsTr 界面字符串 {wrapped_count} 处")
    print(f"允许的非界面 Literal {len(allowed)} 处：")
    for entry in allowed:
        print(f"  - {entry}")
    if missing:
        raise TranslationError("QML/JS 界面文字覆盖检查失败：\n  - " + "\n  - ".join(missing))
    print("覆盖检查通过：没有未翻译的界面字符串")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TranslationError as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1)
