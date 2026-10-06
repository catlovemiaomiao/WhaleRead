"""Print a location-independent SHA-256 inventory digest for a macOS app.

The digest covers every relative path, entry type, executable permission bit,
regular-file content, and symlink target.  It intentionally ignores mtimes and
the absolute parent directory, so the same signed bundle keeps its identity when
copied from staging to the installation directory.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import stat
from pathlib import Path


def bundle_digest(app: Path) -> tuple[str, int, int]:
    app = app.resolve()
    digest = hashlib.sha256()
    regular_files = 0
    symlinks = 0
    entries = sorted(app.rglob("*"), key=lambda path: path.relative_to(app).as_posix())
    for path in entries:
        relative = path.relative_to(app).as_posix().encode("utf-8")
        mode = path.lstat().st_mode
        executable = b"1" if mode & stat.S_IXUSR else b"0"
        if path.is_symlink():
            kind = b"L"
            payload = os.readlink(path).encode("utf-8")
            symlinks += 1
        elif path.is_dir():
            kind = b"D"
            payload = b""
        elif path.is_file():
            kind = b"F"
            file_hash = hashlib.sha256()
            with path.open("rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    file_hash.update(block)
            payload = file_hash.digest()
            regular_files += 1
        else:
            kind = b"O"
            payload = b""
        for value in (kind, executable, relative, payload):
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
    return digest.hexdigest(), regular_files, symlinks


def main() -> int:
    parser = argparse.ArgumentParser(description="Hash a relocatable app-bundle inventory")
    parser.add_argument("app")
    args = parser.parse_args()
    app = Path(args.app)
    if not app.is_dir():
        parser.error(f"bundle not found: {app}")
    value, files, links = bundle_digest(app)
    print(f"{value}  {app.resolve()}")
    print(f"regular_files={files} symlinks={links}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
