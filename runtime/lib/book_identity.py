"""Path-independent identities for imported books and their source assets."""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import unicodedata
from pathlib import Path


SCHEMA = "sha256-bytes-v1"
TASK_SCHEMA = 1
TASK_IDENTITY_FILE = ".whaleread-identity.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def valid_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def canonical_path(path: str | Path) -> str:
    """Serialize a path consistently without using it as document identity."""
    # ``Path.resolve()`` walks the filesystem.  On macOS that can trigger a
    # Files & Folders permission request before the application's first window
    # exists.  Paths are only aliases in the v2 identity model, so a lexical
    # absolute path is both sufficient and safe during startup.
    value = os.path.abspath(os.path.expanduser(os.fspath(path)))
    return unicodedata.normalize("NFC", value)


def file_sha256(path: str | Path) -> str:
    """Hash one stable file without loading the whole asset into memory."""
    path = Path(path).expanduser().resolve()
    for _ in range(2):
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        after = path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns,
                before.st_ctime_ns) == (after.st_dev, after.st_ino, after.st_size,
                                        after.st_mtime_ns, after.st_ctime_ns):
            return digest.hexdigest()
    raise OSError(f"文件正在替换，无法确认内容身份：{path.name}")


def aggregate_sha256(values: list[str]) -> str:
    """Combine ordered asset hashes; a single source keeps its ordinary SHA-256."""
    if not values or any(not valid_sha256(value) for value in values):
        raise ValueError("书籍内容哈希无效")
    if len(values) == 1:
        return values[0]
    digest = hashlib.sha256(b"whaleread-assets-v1\0")
    for value in values:
        digest.update(bytes.fromhex(value))
    return digest.hexdigest()


def canonical_text(value: str) -> str:
    """Normalize representation only; paragraph whitespace remains meaningful."""
    return unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))


def text_sha256(values: list[str]) -> str:
    """Hash ordered extracted texts with unambiguous length boundaries."""
    if not values:
        raise ValueError("书籍正文为空")
    digest = hashlib.sha256(b"whaleread-text-v1\0")
    for value in values:
        encoded = canonical_text(value).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def book_id(source_hash: str) -> str:
    if not valid_sha256(source_hash):
        raise ValueError("书籍内容哈希无效")
    return "book:" + source_hash


def task_identity(task_root: str | Path, source_hash: str) -> str:
    """Return a persisted, path-independent identity for one translation task."""
    if not valid_sha256(source_hash):
        raise ValueError("书籍内容哈希无效")
    root = Path(task_root).expanduser().resolve()
    marker = root / TASK_IDENTITY_FILE
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
        task_id = str(value.get("task_id") or "")
        if (value.get("schema") == TASK_SCHEMA and value.get("book_sha256") == source_hash
                and _SHA256.fullmatch(task_id)):
            return task_id
        raise ValueError("翻译任务的书籍身份记录不一致")
    except FileNotFoundError:
        pass
    if not root.is_dir():
        # Shelf callers can inspect an unavailable legacy task. Keep that
        # fallback path-independent; a real task always persists a random id.
        return hashlib.sha256(b"whaleread-unpersisted-task-v1\0"
                              + bytes.fromhex(source_hash)).hexdigest()
    task_id = secrets.token_hex(32)
    payload = json.dumps({"schema": TASK_SCHEMA, "book_sha256": source_hash,
                          "task_id": task_id}, ensure_ascii=False, indent=2) + "\n"
    fd, temporary = tempfile.mkstemp(dir=root, prefix=".book-id-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # The complete temp file becomes the marker without replacing an
            # id that another app process may have won concurrently.
            os.link(temporary, marker)
        except FileExistsError:
            value = json.loads(marker.read_text(encoding="utf-8"))
            existing = str(value.get("task_id") or "")
            if (value.get("schema") != TASK_SCHEMA
                    or value.get("book_sha256") != source_hash
                    or _SHA256.fullmatch(existing) is None):
                raise ValueError("翻译任务的书籍身份记录不一致")
            return existing
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return task_id


def shelf_id(kind: str, source_hash: str, task_root: str | Path = "") -> str:
    """Address a shelf edition by content plus a persistent task identity."""
    if kind == "task":
        return "task:" + source_hash + ":" + task_identity(task_root, source_hash)
    return "source:" + source_hash


def reader_token(identity: str) -> str:
    return hashlib.sha256(str(identity).encode("utf-8")).hexdigest()[:24]


def legacy_path_id(kind: str, path: str | Path) -> str:
    return hashlib.sha256((str(kind) + ":" + canonical_path(path)).encode()).hexdigest()[:24]
