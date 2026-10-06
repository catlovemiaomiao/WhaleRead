"""Extract translatable interface strings into i18n/whaleread_en.ts.

    python scripts/i18n_extract.py [--output PATH]

lupdate receives a staged copy of the catalogue, so translations that were
already reviewed are preserved and only new or changed strings become
unfinished.  The staged file replaces the reviewed catalogue only after every
guard passes.  Offline; writes nothing outside i18n/ unless ``--output`` says
otherwise.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from i18n_common import (CATALOGUE, ROOT, TranslationError,
                         guard_catalogue_loss, messages, parse_catalogue, sources, tool)


def main() -> int:
    parser = argparse.ArgumentParser(description="提取界面可翻译字符串")
    parser.add_argument("--output", default=str(CATALOGUE), help="输出 .ts 路径")
    args = parser.parse_args()
    target = Path(args.output)
    if not target.is_absolute():
        target = ROOT / target
    target.parent.mkdir(parents=True, exist_ok=True)
    before = 0
    if target.is_file():
        before = len(messages(parse_catalogue(target)))
    handle = tempfile.NamedTemporaryFile(
        prefix=f".{target.stem}-", suffix=target.suffix, dir=target.parent, delete=False)
    staged = Path(handle.name)
    handle.close()
    staged.unlink()
    if target.is_file():
        shutil.copyfile(target, staged)
    try:
        command = [tool("pyside6-lupdate"),
                   *[str(path) for path in sources()],
                   "-no-obsolete", "-ts", str(staged)]
        completed = subprocess.run(command, cwd=str(ROOT), capture_output=True, text=True)
        if completed.returncode != 0:
            print(completed.stdout, end="")
            print(completed.stderr, end="", file=sys.stderr)
            raise TranslationError(f"lupdate 失败，退出码 {completed.returncode}")
        rows = messages(parse_catalogue(staged))
        guard_catalogue_loss(before, len(rows))
        os.replace(staged, target)
    finally:
        staged.unlink(missing_ok=True)
    unfinished = sum(1 for _, message in rows
                     if (message.find("translation") is None
                         or message.find("translation").get("type") == "unfinished"))
    try:
        shown = target.relative_to(ROOT)
    except ValueError:
        shown = target
    print(f"{shown}: {len(rows)} 条消息，{unfinished} 条待翻译")
    if unfinished:
        print("新的或改动过的字符串需要人工填写英文译文，然后运行 "
              "scripts/i18n_compile.py 和 scripts/i18n_check.py")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TranslationError as error:
        print(f"错误：{error}", file=sys.stderr)
        raise SystemExit(1)
