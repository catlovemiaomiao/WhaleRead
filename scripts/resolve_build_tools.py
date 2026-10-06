"""Resolve build tools from ONE selected interpreter.

    python scripts/resolve_build_tools.py <query>

Queries:

* ``pyinstaller`` — prints ``ok`` when PyInstaller is importable
* ``pyside6``     — prints ``ok`` when PySide6 is importable
* ``qt-root``     — prints the Qt root directory of that PySide6
* ``tool <name>`` — prints the absolute path of a console script shipped beside
  the interpreter (for example ``pyside6-lrelease``)

Every query exits non-zero with a specific message when it cannot be answered,
so a build fails loudly instead of silently using a different environment.
Nothing here reads the source tree or mutates shared Qt state.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path


class ResolutionError(RuntimeError):
    pass


def interpreter_bin(name: str) -> Path:
    """A console script beside the running interpreter, never from PATH first."""
    candidates = [
        Path(sys.executable).resolve().parent / name,
        Path(sys.executable).resolve().parent.parent / "bin" / name,
    ]
    resolved = Path(sys.executable).resolve()
    if resolved.parent.name == "bin":
        candidates.append(resolved.parent / name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ResolutionError(f"{name} not found beside the selected interpreter "
                          f"({sys.executable})")


def resolve(query: str, argument: str = "") -> str:
    if query in ("pyside6", "qt-root"):
        spec = importlib.util.find_spec("PySide6")
        if spec is None or not spec.origin:
            raise ResolutionError("PySide6 is not importable from the selected interpreter")
        if query == "pyside6":
            return "ok"
        return str(Path(spec.origin).parent / "Qt")
    if query == "pyinstaller":
        if importlib.util.find_spec("PyInstaller") is None:
            raise ResolutionError("PyInstaller is not importable from the selected interpreter")
        return "ok"
    if query == "tool":
        if not argument:
            raise ResolutionError("tool query needs a tool name")
        return str(interpreter_bin(argument))
    raise ResolutionError(f"unknown query: {query}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Resolve build tools from the selected interpreter")
    parser.add_argument("query")
    parser.add_argument("argument", nargs="?", default="")
    args = parser.parse_args()
    try:
        print(resolve(args.query, args.argument))
    except ResolutionError as error:
        print(str(error), file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
