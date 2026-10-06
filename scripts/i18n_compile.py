"""Compile the reviewed catalogue into i18n/whaleread_en.qm.

    python scripts/i18n_compile.py [--catalogue PATH] [--output PATH]

The compiled file is what the application loads at runtime.  A catalogue with
unfinished messages still compiles, so this script also refuses to report
success when nothing usable was produced: it reloads the .qm with QTranslator
and fails if the catalogue is missing, empty, unreadable or blank.  Offline.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from i18n_check import check_catalogue
from i18n_common import CATALOGUE, COMPILED, ROOT, TranslationError, tool

PROBE_CONTEXT = "Main"
PROBE_SOURCE = "设置"


def resolve(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def verify(path: Path) -> None:
    """Reload the compiled catalogue; a fake success is a hard failure."""
    from PySide6.QtCore import QCoreApplication, QTranslator
    QCoreApplication.instance() or QCoreApplication([])
    translator = QTranslator()
    if not translator.load(str(path)) or translator.isEmpty():
        raise TranslationError(f"{path} 无法载入或没有任何译文")
    probe = translator.translate(PROBE_CONTEXT, PROBE_SOURCE)
    if not probe or probe == PROBE_SOURCE:
        raise TranslationError(f"{path} 缺少可用的英文条目")


def main() -> int:
    parser = argparse.ArgumentParser(description="编译界面翻译资源")
    parser.add_argument("--catalogue", default=str(CATALOGUE), help="输入 .ts 路径")
    parser.add_argument("--output", default=str(COMPILED), help="输出 .qm 路径")
    args = parser.parse_args()
    catalogue = resolve(args.catalogue)
    output = resolve(args.output)
    if not catalogue.is_file():
        raise TranslationError(f"缺少翻译源文件：{catalogue}")
    # lrelease normally skips unfinished entries and still exits successfully.
    # Refuse that partial output before it can be mistaken for a reviewed build.
    check_catalogue(catalogue)
    command = [tool("pyside6-lrelease"), str(catalogue), "-qm", str(output)]
    completed = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True)
    if completed.returncode != 0:
        print(completed.stdout, end="")
        print(completed.stderr, end="", file=sys.stderr)
        raise TranslationError(f"lrelease 失败，退出码 {completed.returncode}")
    print(completed.stdout, end="")
    if not output.is_file() or output.stat().st_size == 0:
        raise TranslationError(f"lrelease 未生成 {output}")
    verify(output)
    print(f"{output}: {output.stat().st_size} 字节，已可载入")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TranslationError as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1)
