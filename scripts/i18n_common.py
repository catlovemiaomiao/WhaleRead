"""Shared helpers for the WhaleRead interface-translation scripts.

Everything is offline and deterministic: the Qt tools come from the running
interpreter, the source list is an explicit manifest, and no timestamp, random
value or network access enters the generated files.
"""
from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
I18N_DIR = ROOT / "i18n"
SOURCES_MANIFEST = I18N_DIR / "SOURCES.txt"
CATALOGUE = I18N_DIR / "whaleread_en.ts"
COMPILED = I18N_DIR / "whaleread_en.qm"
EXPECTED_LANGUAGE = "en_US"


class TranslationError(RuntimeError):
    """A translation resource failed a required check."""


def tool(name: str) -> str:
    """Resolve a PySide6 tool next to the running interpreter, never from PATH."""
    directories = (Path(sys.executable).parent, Path(sys.executable).resolve().parent)
    for directory in dict.fromkeys(directories):
        candidate = directory / name
        if candidate.is_file():
            return str(candidate)
    raise TranslationError(f"找不到 {name}；请使用打包了 PySide6 的解释器运行")
    # no network, no package installation: a missing tool is a hard failure


def sources() -> list[Path]:
    if not SOURCES_MANIFEST.is_file():
        raise TranslationError(f"缺少源文件清单：{SOURCES_MANIFEST.relative_to(ROOT)}")
    listed: list[Path] = []
    for raw in SOURCES_MANIFEST.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        path = (ROOT / line).resolve()
        if not path.is_file():
            raise TranslationError(f"清单中的源文件不存在：{line}")
        listed.append(path)
    if not listed:
        raise TranslationError("源文件清单为空")
    return listed


def parse_catalogue(path: Path | None = None) -> ET.Element:
    target = path or CATALOGUE
    if not target.is_file():
        raise TranslationError(f"缺少翻译源文件：{target.relative_to(ROOT)}")
    try:
        return ET.parse(target).getroot()
    except ET.ParseError as exc:
        raise TranslationError(f"翻译源文件不是有效 XML：{exc}") from exc


def guard_catalogue_loss(before: int, after: int) -> None:
    """Refuse an extraction that would silently drop reviewed translations.

    Deleting and regenerating the catalogue, or losing i18n/SOURCES.txt scope,
    can erase reviewed English.  A large drop is treated as an error instead of
    a successful run.
    """
    if before and after < before * 0.9:
        raise TranslationError(
            f"提取后消息数从 {before} 降到 {after}，疑似丢失已审阅译文；"
            "请检查 i18n/SOURCES.txt 与工作区状态，不要提交这次结果")


def messages(root: ET.Element) -> list[tuple[str, ET.Element]]:
    result: list[tuple[str, ET.Element]] = []
    for context in root.findall("context"):
        name = context.findtext("name") or ""
        for message in context.findall("message"):
            result.append((name, message))
    return result
