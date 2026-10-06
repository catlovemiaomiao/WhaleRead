"""Check the reviewed English catalogue and the compiled runtime resource.

    python scripts/i18n_check.py

Fails (non-zero) when:

* the catalogue has unfinished, empty or obsolete messages;
* a source file in i18n/SOURCES.txt has a translatable literal the catalogue
  does not know about (extraction was not re-run);
* a translation changes the set of ``%1``/``%n``/``%L1`` placeholders;
* an English numerus message does not provide exactly the two English forms;
* the compiled .qm is missing, unreadable, empty, or its content no longer
  matches the reviewed catalogue.

Content is compared, not timestamps, so a fresh checkout cannot fail because
of file modification order.  Offline only.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

from i18n_common import (CATALOGUE, COMPILED, EXPECTED_LANGUAGE, ROOT,
                         TranslationError, messages, parse_catalogue, sources)

PLACEHOLDER = re.compile(r"%(\d+|n|L\d+)")
QML_TR = re.compile(r"qsTr\(\s*\"((?:[^\"\\]|\\.)*)\""
                    r"(?:\s*,\s*\"((?:[^\"\\]|\\.)*)\")?")
PY_TR = re.compile(r"(?:QT_TRANSLATE_NOOP|QCoreApplication\.translate)\("
                   r"\s*\"([^\"]*)\"\s*,\s*\"((?:[^\"\\]|\\.)*)\"")


def placeholders(text: str) -> Counter[str]:
    """Count placeholders so duplicated or dropped occurrences also fail."""
    return Counter(PLACEHOLDER.findall(text.replace("%%", "")))


def unescape(text: str) -> str:
    return text.replace('\\"', '"').replace("\\'", "'").replace("\\n", "\n")


# (pattern, group holding the source literal) per file kind.  Qt expressions in
# Python put the context first, QML's qsTr puts the source first.
PATTERNS = {
    ".qml": ((QML_TR, 1), (PY_TR, 2)),
    ".py": ((PY_TR, 2),),
}


def declared_strings() -> set[str]:
    """Every translatable literal found in the manifest files."""
    found: set[str] = set()
    for path in sources():
        text = path.read_text(encoding="utf-8")
        for pattern, group in PATTERNS.get(path.suffix, PATTERNS[".py"]):
            for match in pattern.finditer(text):
                found.add(unescape(match.group(group)))
    return found


def check_catalogue(path=None) -> list[tuple[str, str, str, str]]:
    """Return (context, source, comment, translation) rows after validation."""
    root = parse_catalogue(path)
    language = root.get("language") or ""
    if language != EXPECTED_LANGUAGE:
        raise TranslationError(f"catalogue language 应为 {EXPECTED_LANGUAGE}，实际为 {language!r}")
    rows: list[tuple[str, str, str, str]] = []
    problems: list[str] = []
    for context, message in messages(root):
        source = message.findtext("source") or ""
        comment = message.findtext("comment") or ""
        translation = message.find("translation")
        if translation is None:
            problems.append(f"[{context}] 缺少 <translation>：{source}")
            continue
        translation_type = translation.get("type")
        if translation_type == "unfinished":
            problems.append(f"[{context}] 未翻译：{source}")
            continue
        if translation_type in {"obsolete", "vanished"}:
            problems.append(f"[{context}] 已废弃条目：{source}")
            continue
        expected = placeholders(source)
        if message.get("numerus") == "yes":
            forms = [node.text or "" for node in translation.findall("numerusform")]
            if len(forms) != 2:
                problems.append(f"[{context}] 英文复数应有 2 个形式，实际 {len(forms)} 个：{source}")
                continue
            for index, form in enumerate(forms):
                if not form.strip():
                    problems.append(f"[{context}] 复数第 {index + 1} 个形式为空：{source}")
                elif placeholders(form) != expected:
                    problems.append(f"[{context}] 复数占位符不一致：{source} -> {form}")
            rows.append((context, source, comment, forms[0]))
            rows.append((context, source, comment, forms[1]))
        else:
            # Keep leading/trailing spaces: several reviewed entries are
            # intentional prefix/suffix fragments joined with runtime values.
            value = translation.text or ""
            if not value.strip():
                problems.append(f"[{context}] 译文为空：{source}")
                continue
            if placeholders(value) != expected:
                problems.append(f"[{context}] 占位符不一致：{source} -> {value}")
            rows.append((context, source, comment, value))
    if problems:
        raise TranslationError("翻译源文件校验失败：\n  - " + "\n  - ".join(problems))
    return rows


def check_coverage(root) -> int:
    known = {message.findtext("source") or "" for _, message in messages(root)}
    missing = sorted(declared_strings() - known)
    if missing:
        raise TranslationError("以下界面字符串尚未提取（请运行 scripts/i18n_extract.py）：\n  - "
                               + "\n  - ".join(missing))
    return len(known)


def check_compiled(rows: list[tuple[str, str, str, str]], compiled=None) -> None:
    """Reload the .qm and require every reviewed translation to come back."""
    from PySide6.QtCore import QCoreApplication, QTranslator
    app = QCoreApplication.instance() or QCoreApplication([])
    target = Path(compiled) if compiled is not None else COMPILED
    if not target.is_file():
        raise TranslationError(f"缺少编译产物：{target}（请运行 scripts/i18n_compile.py）")
    translator = QTranslator()
    if not translator.load(str(target)) or translator.isEmpty():
        raise TranslationError(f"{target} 无法载入或没有任何译文")
    grouped: dict[tuple[str, str, str], list[str]] = {}
    for context, source, comment, value in rows:
        grouped.setdefault((context, source, comment), []).append(value)
    mismatched: list[str] = []
    # Install so ``%n`` is substituted exactly as the running application does.
    app.installTranslator(translator)
    try:
        for (context, source, comment), values in grouped.items():
            if "%n" in source:
                if len(values) != 2:
                    mismatched.append(f"[{context}] 复数形式数量异常：{source}")
                    continue
                for number in (0, 1, 2, 5):
                    expected = (values[0] if number == 1 else values[1]).replace("%n", str(number))
                    actual = QCoreApplication.translate(context, source, comment or None, number)
                    if actual != expected:
                        mismatched.append(f"[{context}] 编译产物复数不一致（n={number}）："
                                          f"{source} -> {actual!r} != {expected!r}")
            else:
                actual = QCoreApplication.translate(context, source, comment or None)
                if actual != values[0]:
                    mismatched.append(f"[{context}] 编译产物不一致："
                                      f"{source} -> {actual!r} != {values[0]!r}")
    finally:
        app.removeTranslator(translator)
    if mismatched:
        raise TranslationError("编译产物与审阅后的 catalogue 不一致：\n  - "
                               + "\n  - ".join(mismatched[:20]))


def main() -> int:
    parser = argparse.ArgumentParser(description="校验界面翻译资源")
    parser.add_argument("--catalogue", default=str(CATALOGUE), help="输入 .ts 路径")
    parser.add_argument("--qm", default=str(COMPILED), help="编译产物 .qm 路径")
    args = parser.parse_args()
    catalogue = Path(args.catalogue)
    catalogue = catalogue if catalogue.is_absolute() else ROOT / catalogue
    qm = Path(args.qm)
    qm = qm if qm.is_absolute() else ROOT / qm
    root = parse_catalogue(catalogue)
    rows = check_catalogue(catalogue)
    known = check_coverage(root)
    check_compiled(rows, qm)
    print(f"{catalogue}: {known} 条消息，全部已翻译且占位符/复数一致")
    print(f"{qm}: 与 catalogue 内容一致，可被 QTranslator 载入")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TranslationError as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1)
