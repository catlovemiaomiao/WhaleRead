from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import signal
import tempfile
import threading
import time
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
from book_identity import SCHEMA as BOOK_IDENTITY_SCHEMA, aggregate_sha256, file_sha256
from task_config import (validate as validate_task, language, language_family,
                         output_suffix, script_counts, task_lock)
from glossary import prepare_glossary, selected as select_terms, fingerprint
from provider_transport import chat_request, completion_url, load_credential, ProviderError


STATE_SCHEMA_VERSION = 2
OUTPUT_PROTOCOL = "segment-markers-v1"
NATIVE_PROMPT_REVISION = "hy-mt2-native-v1"
MARKER_PATTERN = re.compile(r"^<<<SEG:(\d{6})>>>[ \t]*$", re.MULTILINE)
CONTROL_ECHOES = ("只翻译此段", "最终输出必须", "专项规则：")
TERMINAL_LINE = re.compile(r"(?:[.!?…](?:[”’\"']|\)”?)?|[”’\"'])$")


class TranslationContractError(RuntimeError):
    """The translator returned content that cannot be mapped safely back to source segments."""


def emit(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)


# Stable failure codes the desktop renders in the current locale.  The raw
# message is always kept beside the code as diagnostic detail.
CODE_FILE_MISSING = "fileMissing"
CODE_SOURCE_CHANGED = "sourceChanged"
CODE_DIRECTION_LOCKED = "directionLocked"
CODE_CHECKPOINT_INCOMPATIBLE = "checkpointIncompatible"
CODE_CREDENTIALS_MISSING = "credentialsMissing"
CODE_REQUEST_TIMEOUT = "requestTimeout"
CODE_HTTP_FAILED = "httpFailed"
CODE_MODEL_FAILED = "modelFailed"
CODE_VALIDATION_FAILED = "validationFailed"
CODE_SAFETY_PAUSED = "safetyPaused"
CODE_UNKNOWN_ERROR = "unknownError"


def failure_code(exc: BaseException) -> str:
    """Classify a worker failure by exception type and origin, never by locale.

    The categories mirror the desktop's stable codes.  Text is only inspected
    for messages this worker itself raises, so a localized sentence can never
    change the classification.
    """
    if isinstance(exc, ProviderError):
        return (CODE_CREDENTIALS_MISSING if exc.code.startswith('missing_credential')
                else CODE_HTTP_FAILED if exc.status or exc.code == 'connection_failed'
                else CODE_MODEL_FAILED)
    if isinstance(exc, urllib.error.HTTPError):
        return CODE_HTTP_FAILED
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return CODE_REQUEST_TIMEOUT
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return CODE_REQUEST_TIMEOUT
        return CODE_HTTP_FAILED
    if isinstance(exc, TranslationContractError):
        return CODE_VALIDATION_FAILED
    if isinstance(exc, FileNotFoundError):
        return CODE_FILE_MISSING
    if isinstance(exc, PermissionError):
        return CODE_SAFETY_PAUSED
    if isinstance(exc, ValueError):
        text = str(exc)
        if ("认证存储中缺少" in text or "API 密钥" in text
                or "凭据，请配置" in text):
            return CODE_CREDENTIALS_MISSING
        if "长度上限" in text or "choices" in text or "空译文" in text:
            return CODE_MODEL_FAILED
        if "原文" in text and "不存在" in text:
            return CODE_FILE_MISSING
        if "原文" in text and any(marker in text for marker in (
                "不一致", "已变化", "分段方式已变化", "分段映射已变化",
                "副本不可用")):
            return CODE_SOURCE_CHANGED
        if any(marker in text for marker in (
                "旧版、未逐段验证的直译断点", "翻译模型或服务已改变",
                "翻译模型已改变", "旧译本无法确认原模型",
                "翻译规则或术语模式已改变", "术语表与既有断点不同")):
            return CODE_CHECKPOINT_INCOMPATIBLE
        if any(marker in text for marker in (
                "为避免覆盖", "必须位于当前任务目录内", "不能与原文相同")):
            return CODE_SAFETY_PAUSED
        return CODE_VALIDATION_FAILED
    if isinstance(exc, RuntimeError):
        text = str(exc)
        if "长度上限" in text or "choices" in text or "空译文" in text:
            return CODE_MODEL_FAILED
        return CODE_UNKNOWN_ERROR
    return CODE_UNKNOWN_ERROR


def failure_payload(exc: BaseException) -> dict[str, Any]:
    """A failure entry carrying both the stable code and the raw detail."""
    return {"code": failure_code(exc), "error": str(exc)[:600]}


def start_parent_watchdog(parent_pid: int) -> None:
    """Terminate this worker if its desktop parent disappears unexpectedly."""
    if parent_pid <= 1:
        return

    def watch() -> None:
        while True:
            time.sleep(1.0)
            if os.getppid() != parent_pid:
                os.kill(os.getpid(), signal.SIGTERM)
                return

    threading.Thread(target=watch, name="hy-parent-watchdog", daemon=True).start()


def atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(raw, path)
    except Exception:
        try:
            os.unlink(raw)
        except OSError:
            pass
        raise


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    atomic_text(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.is_file():
        return default
    loaded = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        raise ValueError(f"JSON 顶层必须是对象：{path}")
    return loaded


def within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def resolve_path(root: Path, raw: str) -> Path:
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = root / candidate
    return candidate.resolve()


def looks_hard_wrapped(text: str, raw_lines: list[str]) -> bool:
    """Recognize Latin-script prose wrapped at a fixed page/text width."""
    lines = [line.strip() for line in raw_lines if line.strip()]
    if len(lines) < 20:
        return False
    counts = script_counts(text)
    if counts["latin"] < 100 or counts["latin"] < counts["han"] * 2:
        return False
    lengths = sorted(len(line) for line in lines)
    median = lengths[len(lengths) // 2]
    regular_width = sum(55 <= len(line) <= 120 for line in lines) / len(lines)
    blank_count = sum(not line.strip() for line in raw_lines)
    return median >= 55 and regular_width >= 0.6 and blank_count <= max(3, len(lines) // 10)


def split_wrapped_prose(raw_lines: list[str]) -> tuple[list[str], list[dict[str, int | str]]]:
    """Join fixed-width lines into complete prose units before adding markers."""
    segments: list[str] = []
    layout: list[dict[str, int | str]] = []
    current: list[str] = []
    nonempty_index = 0

    def flush() -> None:
        if not current:
            return
        joined = current[0]
        for continuation in current[1:]:
            joined = joined + continuation.lstrip() if joined.endswith("-") else joined + " " + continuation.lstrip()
        segments.append(joined.strip())
        layout.append({"kind": "segment", "id": len(segments)})
        current.clear()

    for raw_line in raw_lines:
        line = raw_line.strip()
        if not line:
            flush()
            layout.append({"kind": "blank"})
            continue
        nonempty_index += 1
        heading = (
            len(line) <= 45
            and (
                (any(char.isalpha() for char in line) and line.upper() == line)
                or nonempty_index <= 3
                or bool(re.fullmatch(r"(?i)(?:chapter|book|part|prologue|epilogue)\s+[\w.-]+", line))
            )
        )
        if heading:
            flush()
            segments.append(line)
            layout.append({"kind": "segment", "id": len(segments)})
            continue
        current.append(line)
        if TERMINAL_LINE.search(line):
            flush()
    flush()
    return segments, layout


def split_source(text: str, mode: str) -> tuple[list[str], str, list[dict[str, int | str]]]:
    """Split source text while retaining its blank-line layout as render tokens."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff")
    raw_lines = normalized.split("\n")
    lines = [line.rstrip() for line in raw_lines if line.strip()]
    blocks = [block.strip("\n") for block in re.split(r"\n[ \t]*\n+", normalized) if block.strip()]

    selected = mode
    if mode == "auto":
        # 小说导出文件常见两种形态：每个非空行一段，或空行分段。
        if looks_hard_wrapped(normalized, raw_lines):
            selected = "wrapped_prose"
        else:
            selected = "nonempty_line" if len(lines) > max(1, len(blocks) * 2) else "blank_line"
    if selected == "wrapped_prose":
        segments, layout = split_wrapped_prose(raw_lines)
    elif selected == "nonempty_line":
        segments: list[str] = []
        layout: list[dict[str, int | str]] = []
        for raw_line in raw_lines:
            line = raw_line.rstrip()
            if line.strip():
                segments.append(line)
                layout.append({"kind": "segment", "id": len(segments)})
            else:
                layout.append({"kind": "blank"})
    elif selected == "blank_line":
        segments = []
        layout = []
        current: list[str] = []
        for raw_line in raw_lines:
            line = raw_line.rstrip()
            if line.strip():
                current.append(line)
                continue
            if current:
                segments.append("\n".join(current))
                layout.append({"kind": "segment", "id": len(segments)})
                current = []
            layout.append({"kind": "blank"})
        if current:
            segments.append("\n".join(current))
            layout.append({"kind": "segment", "id": len(segments)})
    else:
        raise ValueError("chunking.segment_mode 只能是 auto、wrapped_prose、nonempty_line 或 blank_line")
    if not segments:
        raise ValueError("原文没有可翻译的非空段落")
    return segments, selected, layout


def split_segments(text: str, mode: str) -> tuple[list[str], str]:
    """Compatibility helper for callers/tests that only need source segments."""
    segments, selected, _ = split_source(text, mode)
    return segments, selected


def _source_digest_with_names(paths: list[Path], names: list[str]) -> str:
    digest = hashlib.sha256()
    for path, name in zip(paths, names, strict=True):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def source_file_names(paths: list[Path]) -> list[str]:
    """Stable path spellings for checkpoints across macOS Unicode variants."""
    return [unicodedata.normalize("NFC", str(path)) for path in paths]


def source_digest(paths: list[Path]) -> str:
    """Hash source bytes only; locations are diagnostic metadata, not identity."""
    return aggregate_sha256([file_sha256(path) for path in paths])


def source_digest_matches(stored: object, paths: list[Path], recorded: object = None) -> bool:
    """Accept safe legacy hashes whose paths differ only by Unicode normalization."""
    if not isinstance(stored, str):
        return False
    raw_names = [str(path) for path in paths]
    candidates = {
        source_digest(paths),
        # Read-only compatibility with path-era checkpoints. A matching legacy
        # digest stays unchanged so an older app can still resume after rollback.
        _source_digest_with_names(paths, source_file_names(paths)),
        _source_digest_with_names(paths, raw_names),
    }
    if isinstance(recorded, list) and len(recorded) == len(paths):
        recorded_names = [str(value) for value in recorded]
        if all(unicodedata.normalize("NFC", saved) == unicodedata.normalize("NFC", current)
               for saved, current in zip(recorded_names, raw_names, strict=True)):
            candidates.add(_source_digest_with_names(paths, recorded_names))
    return stored in candidates


def chunk_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def segmentation_digest(segments: list[str], mode: str) -> str:
    digest = hashlib.sha256(mode.encode("utf-8"))
    for number, segment in enumerate(segments, 1):
        digest.update(number.to_bytes(8, "big"))
        digest.update(segment.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def segment_marker(number: int) -> str:
    return f"<<<SEG:{number:06d}>>>"


def marked_source(segments: list[str], start: int, end: int) -> str:
    return "\n".join(
        f"{segment_marker(number)}\n{segments[number - 1]}" for number in range(start, end + 1)
    )


def make_chunk(segments: list[str], start: int, end: int) -> dict[str, Any]:
    return {"start": start, "end": end, "text": "\n".join(segments[start - 1 : end])}


def make_chunks(
    segments: list[str],
    start: int,
    end: int,
    max_chars: int,
    max_segments: int = 20,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    current_start = start
    current_size = 0
    for number in range(start, end + 1):
        segment = segments[number - 1]
        if len(segment) > 12000:
            raise ValueError(f"第 {number} 段超过 12000 字符，请在准备任务时改用更合适的分段方式")
        projected = current_size + len(segment_marker(number)) + len(segment) + 2
        reached_segment_cap = number - current_start >= max_segments
        if number > current_start and (projected > max_chars or reached_segment_cap):
            chunks.append(make_chunk(segments, current_start, number - 1))
            current_start = number
            current_size = len(segment_marker(number)) + len(segment) + 2
        else:
            current_size = projected
    if current_start <= end:
        chunks.append(make_chunk(segments, current_start, end))
    return chunks


def compact_text(value: str) -> str:
    return re.sub(r"\s+", "", value)


def validate_segment_entries(
    entries: Any, segments: list[str], start: int, end: int, target_language: str = ""
) -> list[dict[str, Any]]:
    """Validate a complete, ordered 1:1 source-to-target segment mapping."""
    expected_ids = list(range(start, end + 1))
    if not isinstance(entries, list):
        raise TranslationContractError("模型分段输出不是列表")

    normalized: list[dict[str, Any]] = []
    ids: list[int] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise TranslationContractError("模型分段输出包含无效项目")
        try:
            number = int(entry["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise TranslationContractError("模型分段输出缺少有效段号") from exc
        text = entry.get("text")
        if not isinstance(text, str) or not text.strip():
            raise TranslationContractError(f"模型漏掉了第 {number} 段译文")
        cleaned = text.strip()
        if any(echo in cleaned for echo in CONTROL_ECHOES):
            raise TranslationContractError(f"模型回显了提示词，未翻译第 {number} 段")
        ids.append(number)
        normalized.append({"id": number, "text": cleaned})

    if ids != expected_ids:
        raise TranslationContractError(
            f"模型段号不完整或乱序：期望 {start}～{end}，实际 {ids[:3]}…{ids[-3:]}"
        )

    repeated: dict[str, tuple[int, str]] = {}
    for entry in normalized:
        number = int(entry["id"])
        value = compact_text(str(entry["text"])).casefold()
        source_value = compact_text(segments[number - 1]).casefold()
        previous = repeated.get(value)
        if len(value) >= 40 and previous and previous[1] != source_value:
            raise TranslationContractError(
                f"模型把同一段长译文重复分配给第 {previous[0]}、{number} 段，当前块未保存"
            )
        repeated[value] = (number, source_value)

    source_size = sum(len(compact_text(segments[number - 1])) for number in expected_ids)
    target_size = sum(len(compact_text(str(entry["text"]))) for entry in normalized)
    # 只挡住明显的提示回显或极短异常回复；单字标题、短对话仍由逐段非空规则放行。
    if source_size >= 40 and target_size < max(8, source_size // 12):
        raise TranslationContractError(
            f"模型译文异常短（源 {source_size} 字，译 {target_size} 字），当前块未保存"
        )
    validate_target_language(normalized, segments, start, end, target_language)
    return normalized


def validate_target_language(
    entries: list[dict[str, Any]], segments: list[str], start: int, end: int, target_language: str
) -> None:
    """Reject an obvious source echo before a translated chunk is persisted."""
    family = language_family(target_language)
    if family == "unknown":
        return
    source = "\n".join(segments[start - 1 : end])
    target = "\n".join(str(entry["text"]) for entry in entries)
    source_size = len(compact_text(source))
    if source_size < 40:
        return
    if compact_text(source).casefold() == compact_text(target).casefold():
        raise TranslationContractError("模型原样回传了源文，当前块未保存")
    counts = script_counts(target)
    if family == "han":
        if counts["han"] < 2 or (counts["latin"] >= 40 and counts["latin"] > counts["han"] * 4):
            raise TranslationContractError(
                "模型返回内容未达到简体中文语言门禁（疑似仍为源文），当前块未保存"
            )
    elif family == "latin":
        if counts["latin"] < 4 and counts["han"] + counts["kana"] + counts["hangul"] >= 20:
            raise TranslationContractError("模型返回内容未达到目标语言门禁，当前块未保存")
    elif family == "japanese":
        if counts["kana"] < 2 and counts["han"] + counts["latin"] >= 20:
            raise TranslationContractError("模型返回内容未达到日语语言门禁，当前块未保存")
    elif family == "korean":
        if counts["hangul"] < 2 and counts["han"] + counts["latin"] >= 20:
            raise TranslationContractError("模型返回内容未达到韩语语言门禁，当前块未保存")
    elif family in counts and family != "latin":
        # Arabic, Hebrew, Greek, Thai and Devanagari targets must show their
        # own script instead of an untranslated Latin echo.
        if counts[family] < 2 and counts["latin"] >= 20:
            raise TranslationContractError("模型返回内容未达到目标语言门禁，当前块未保存")


def parse_marked_translation(
    content: str, segments: list[str], start: int, end: int, target_language: str = ""
) -> list[dict[str, Any]]:
    """Parse the exact marker protocol returned by Hy; reject all loose prose."""
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    # A marker followed by its text on the same line still has an unambiguous
    # source ID. Normalize only line-leading markers; all completeness,
    # ordering, duplicate-ID and nonempty-body checks remain unchanged.
    normalized = re.sub(r"^(<<<SEG:\d{6}>>>)[ \t]*(?=\S)", r"\1\n", normalized, flags=re.MULTILINE)
    if not normalized:
        raise TranslationContractError("模型返回了空译文")
    if normalized.startswith("```") or "<<<SEG:" in normalized and not MARKER_PATTERN.search(normalized):
        raise TranslationContractError("模型未按分段标记协议返回译文")

    markers = list(MARKER_PATTERN.finditer(normalized))
    if not markers:
        raise TranslationContractError("模型返回中缺少段落标记")
    if normalized.count("<<<SEG:") != len(markers):
        raise TranslationContractError("模型返回中包含格式错误的段落标记")
    if normalized[: markers[0].start()].strip():
        raise TranslationContractError("模型在第一个段落标记前输出了额外说明")

    entries: list[dict[str, Any]] = []
    for index, match in enumerate(markers):
        next_start = markers[index + 1].start() if index + 1 < len(markers) else len(normalized)
        body = normalized[match.end() : next_start].strip()
        entries.append({"id": int(match.group(1)), "text": body})
    return validate_segment_entries(entries, segments, start, end, target_language)


def parse_plain_translation(
    content: str, segments: list[str], start: int, end: int, target_language: str = ""
) -> list[dict[str, Any]]:
    """Map Hy's unconstrained prose back to source paragraphs when unambiguous.

    Hy-MT2 is strongest with its native, short translation instruction.  The
    first attempt therefore avoids per-paragraph markers.  We accept it only
    when the translated paragraph count is an exact match; otherwise the
    caller retries with the model's documented delimiter instruction.
    """
    normalized = content.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized or normalized.startswith("```") or "<<<SEG:" in normalized:
        raise TranslationContractError("模型原生译文包含包装或意外段落标记")
    expected = end - start + 1
    blocks = [block.strip() for block in re.split(r"\n[ \t]*\n+", normalized) if block.strip()]
    if len(blocks) != expected:
        # Some providers normalize blank lines to single newlines.  A strict
        # one-nonempty-line-per-source-paragraph result is still unambiguous.
        lines = [line.strip() for line in normalized.splitlines() if line.strip()]
        if len(lines) == expected:
            blocks = lines
    if len(blocks) != expected:
        raise TranslationContractError(
            f"模型原生译文段落数不匹配：期望 {expected}，实际 {len(blocks)}"
        )
    return validate_segment_entries(
        [
            {"id": number, "text": text}
            for number, text in zip(range(start, end + 1), blocks, strict=True)
        ],
        segments,
        start,
        end,
        target_language,
    )


def read_chunk_entries(
    record: dict[str, Any], output_dir: Path, segments: list[str], target_language: str = ""
) -> list[dict[str, Any]]:
    try:
        start, end = int(record["start"]), int(record["end"])
        path = output_dir / str(record["path"])
    except (KeyError, TypeError, ValueError) as exc:
        raise TranslationContractError("直译断点记录不完整") from exc
    if record.get("protocol") != OUTPUT_PROTOCOL or not path.is_file():
        raise TranslationContractError("直译块不是已验证的新协议输出")
    payload = load_json(path, {})
    if payload.get("schema_version") != STATE_SCHEMA_VERSION:
        raise TranslationContractError("直译块版本不匹配")
    if int(payload.get("start", -1)) != start or int(payload.get("end", -1)) != end:
        raise TranslationContractError("直译块范围与断点记录不一致")
    return validate_segment_entries(payload.get("segments"), segments, start, end, target_language)


def record_is_valid(
    record: dict[str, Any], output_dir: Path, segments: list[str], target_language: str = ""
) -> bool:
    try:
        read_chunk_entries(record, output_dir, segments, target_language)
        return True
    except (OSError, ValueError, TranslationContractError):
        return False


def normalized_ranges(
    records: list[dict[str, Any]], output_dir: Path, segments: list[str], target_language: str = ""
) -> list[list[int]]:
    intervals: list[tuple[int, int]] = []
    for record in records:
        try:
            start, end = int(record["start"]), int(record["end"])
            path = output_dir / str(record["path"])
        except (KeyError, TypeError, ValueError):
            continue
        if start > 0 and end >= start and path.is_file() and record_is_valid(record, output_dir, segments, target_language):
            intervals.append((start, end))
    intervals.sort()
    merged: list[list[int]] = []
    for start, end in intervals:
        if not merged or start > merged[-1][1] + 1:
            merged.append([start, end])
        else:
            merged[-1][1] = max(merged[-1][1], end)
    return merged


def contiguous_records(
    records: list[dict[str, Any]], output_dir: Path, segments: list[str], target_language: str = ""
) -> tuple[list[dict[str, Any]], int]:
    contiguous: list[dict[str, Any]] = []
    cursor = 1
    for record in sorted(records, key=lambda item: (int(item["start"]), int(item["end"]))):
        start, end = int(record["start"]), int(record["end"])
        if start == cursor and record_is_valid(record, output_dir, segments, target_language):
            contiguous.append(record)
            cursor = end + 1
        elif start > cursor:
            break
        else:
            break
    return contiguous, cursor


def translations_from_records(
    records: list[dict[str, Any]], output_dir: Path, segments: list[str], target_language: str = ""
) -> dict[int, str]:
    translated: dict[int, str] = {}
    for record in records:
        for entry in read_chunk_entries(record, output_dir, segments, target_language):
            number = int(entry["id"])
            if number in translated:
                raise TranslationContractError(f"直译断点重复保存了第 {number} 段")
            translated[number] = str(entry["text"])
    return translated


def render_layout(
    layout: list[dict[str, int | str]], translations: dict[int, str], start: int, end: int
) -> str:
    positions = [
        index
        for index, token in enumerate(layout)
        if token.get("kind") == "segment" and start <= int(token["id"]) <= end
    ]
    if not positions:
        return ""
    lines: list[str] = []
    for token in layout[min(positions) : max(positions) + 1]:
        if token.get("kind") == "blank":
            lines.append("")
            continue
        number = int(token["id"])
        if start <= number <= end:
            try:
                lines.append(translations[number])
            except KeyError as exc:
                raise TranslationContractError(f"无法渲染第 {number} 段，译文缺失") from exc
    return "\n".join(lines).rstrip("\n") + "\n"


def uncovered_ranges(start: int, end: int, covered: list[list[int]]) -> list[tuple[int, int]]:
    gaps: list[tuple[int, int]] = []
    cursor = start
    for left, right in covered:
        if right < cursor:
            continue
        if left > end:
            break
        if left > cursor:
            gaps.append((cursor, min(end, left - 1)))
        cursor = max(cursor, right + 1)
        if cursor > end:
            break
    if cursor <= end:
        gaps.append((cursor, end))
    return gaps


def next_missing(total: int, covered: list[list[int]]) -> int | None:
    cursor = 1
    for left, right in covered:
        if left > cursor:
            return cursor
        if left <= cursor <= right:
            cursor = right + 1
    return cursor if cursor <= total else None


def load_key(auth_path: Path, provider_name: str = "none") -> str:
    if provider_name in {"none", "local", "ollama"}:
        # Ollama's OpenAI-compatible endpoint accepts and ignores this value.
        return "ollama"
    return load_credential(auth_path, provider_name)


def clean_rule(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "；".join(str(item).strip() for item in value if str(item).strip())
    return ""


def recent_translation_reference(
    state: dict[str, Any],
    output_dir: Path,
    segments: list[str],
    start: int,
    target_language: str,
    max_chars: int = 1800,
) -> str:
    """Return a small aligned source/translation tail for literary continuity."""
    if start <= 1 or not state.get("chunks"):
        return ""
    try:
        translated = translations_from_records(
            sorted(state.get("chunks") or [], key=lambda item: (int(item["start"]), int(item["end"]))),
            output_dir,
            segments,
            target_language,
        )
    except (OSError, ValueError, TranslationContractError):
        return ""
    pairs: list[str] = []
    used = 0
    for number in range(start - 1, max(0, start - 7), -1):
        target = translated.get(number)
        if not target:
            continue
        pair = f"原文：{segments[number - 1]}\n译文：{target}"
        if pairs and used + len(pair) > max_chars:
            break
        pairs.append(pair)
        used += len(pair)
    return "\n\n".join(reversed(pairs))


def build_native_prompt(
    config: dict[str, Any],
    segments: list[str],
    chunk: dict[str, Any],
    context_count: int,
    reference: str = "",
    *,
    marked: bool = False,
) -> str:
    """Build a short prompt aligned with Hy-MT2's documented templates."""
    languages = config.get("languages") or {}
    translation = config.get("translation") or {}
    context = config.get("context") or {}
    target_language = language(clean_rule(languages.get("target")))
    start, end = int(chunk["start"]), int(chunk["end"])
    before = "\n\n".join(segments[max(0, start - 1 - context_count) : start - 1])
    after = "\n\n".join(segments[end : min(len(segments), end + context_count)])

    sections: list[str] = []
    if reference:
        sections.append("【上文原文与已采用译文】\n" + reference)
    adjacent = []
    if before:
        adjacent.append("前文：\n" + before)
    if after:
        adjacent.append("后文：\n" + after)
    background = clean_rule(context.get("background"))
    if background and "仅填写" not in background:
        adjacent.insert(0, "作品背景：\n" + background)
    if adjacent:
        sections.append("【仅供理解的背景信息】\n" + "\n\n".join(adjacent))

    terms = select_terms(
        config.get("_glossary_entries", []), before + "\n" + chunk["text"] + "\n" + after
    )
    if terms:
        term_lines = [f"{row['source']} 翻译成 {row['target']}" for row in terms]
        sections.append("参考下面的翻译：\n" + "\n".join(term_lines))

    preferences: list[str] = []
    style = clean_rule(translation.get("style"))
    special = clean_rule(translation.get("special_requirements"))
    if style:
        preferences.append(f"译文风格符合：{style}")
    if special:
        preferences.append(special)

    if marked:
        task = (
            f"请将以下文本准确翻译为{target_language}。原样保留全部 <<<SEG:000000>>> 形式的段落标记，"
            "标记的数量、编号和位置均不可改变。只需要输出翻译后的结果，不要额外解释。"
        )
        source_text = marked_source(segments, start, end)
    else:
        task = (
            f"将以下文本翻译为{target_language}，注意只需要输出翻译后的结果，不要额外解释；"
            "保留原文的段落边界。"
        )
        source_text = "\n\n".join(segments[start - 1 : end])
    if preferences:
        task += "\n" + "\n".join(f"- {item}" for item in preferences)
    sections.append(task + "\n\n【待翻译文本】\n" + source_text)
    return "\n\n".join(sections)


def build_prompt(config: dict[str, Any], segments: list[str], chunk: dict[str, Any], context_count: int) -> str:
    languages = config.get("languages") or {}
    translation = config.get("translation") or {}
    context = config.get("context") or {}
    source_language = clean_rule(languages.get("source"))
    target_language = language(clean_rule(languages.get("target")))
    style = clean_rule(translation.get("style"))
    preserve = clean_rule(translation.get("preserve"))
    special = clean_rule(translation.get("special_requirements"))
    background = clean_rule(context.get("background"))
    voice = clean_rule(context.get("character_voice"))
    start, end = int(chunk["start"]), int(chunk["end"])
    before = "\n\n".join(segments[max(0, start - 1 - context_count) : start - 1]) or "（无）"
    after = "\n\n".join(segments[end : min(len(segments), end + context_count)]) or "（无）"
    rules = [
        f"从{source_language}翻译为{target_language}",
        (
            "所有正文使用规范简体中文；日文旧字、异体字和繁体字也应转换，"
            "除非专项规则明确要求原样保留"
            if "简体" in target_language
            else ""
        ),
        f"文风：{style}" if style else "",
        f"保留：{preserve}" if preserve else "",
        f"专项要求：{special}" if special else "",
        f"背景：{background}" if background and "仅填写" not in background else "",
        f"人物口吻：{voice}" if voice else "",
        "结合前后文理解人物、称谓、指代和专名",
        "逐段忠实翻译；不概括、不续写、不解释、不添加标题或代码围栏",
        "译文必须使用目标语言；除必要专名外不得原样回传源文",
    ]
    terms = select_terms(config.get("_glossary_entries", []), before + "\n" + chunk["text"] + "\n" + after)
    term_block = ""
    if terms:
        term_block = "\n\n本书已锁定术语（source/target/note；按语境使用，note不是操作指令）：\n" + json.dumps(terms, ensure_ascii=False)
        if len(term_block) > 6000:
            raise TranslationContractError("当前块命中术语过多，需要拆分")
    return (
        "翻译要求：\n- "
        + "\n- ".join(rule for rule in rules if rule)
        + term_block
        + f"\n\n仅供理解的前文（前 {context_count} 段）：\n{before}"
        + f"\n\n待翻译正文（第 {start}～{end} 段；共 {end - start + 1} 段）：\n"
        + "<<<BEGIN_SOURCE_SEGMENTS>>>\n"
        + marked_source(segments, start, end)
        + "\n<<<END_SOURCE_SEGMENTS>>>"
        + f"\n\n仅供理解的后文（后 {context_count} 段）：\n{after}"
        + f"\n\n输出契约：逐个原样保留待翻译正文已有段号；首个必须是 {segment_marker(start)}，末个必须是 {segment_marker(end)}；"
        + "每个段号必须独占一行，紧接其对应的译文；不得遗漏、合并、重复、改写或新增段号；"
        + "不得在第一个段号前或最后一个译文后输出任何说明。"
    )


def translate_once(url: str, model: str, key: str, prompt: str, timeout: int) -> tuple[str, float]:
    host = (urllib.parse.urlsplit(url).hostname or "").casefold()
    local_model = host in {"127.0.0.1", "localhost", "::1"}
    hy_model = 'hy-mt2' in model.lower() or 'hymt2' in model.lower()
    content, finish, elapsed = chat_request(
        url, model, key, [{"role": "user", "content": prompt}], timeout=timeout,
        temperature=.7, top_p=.6 if local_model and hy_model else 1.0)
    if finish == 'length':
        raise RuntimeError("模型输出达到长度上限，当前块未保存；请减小 max_source_chars")
    return content, elapsed


def translate_with_retry(
    url: str,
    model: str,
    key: str,
    prompt: str,
    timeout: int,
    validator: Any,
    retry_contract_errors: bool = False,
) -> tuple[list[dict[str, Any]], float]:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            content, elapsed = translate_once(url, model, key, prompt, timeout)
            return validator(content), elapsed
        except TranslationContractError as exc:
            last_error = exc
            # Multi-segment format failures are size-sensitive.  Let the
            # caller bisect immediately instead of paying for the same full
            # generation three times.  A single segment cannot be split, so
            # it keeps the normal transient retry budget.
            if not retry_contract_errors:
                raise
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, RuntimeError) as exc:
            last_error = exc
            if isinstance(exc, ProviderError) and not exc.retryable:
                raise
            if isinstance(exc, RuntimeError) and "长度上限" in str(exc):
                break
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
    if isinstance(last_error, TranslationContractError):
        raise last_error
    if isinstance(last_error, ProviderError):
        raise last_error
    raise RuntimeError(str(last_error)[:600])


def managed_name(config: dict[str, Any], source_path: Path) -> str:
    output = config.get("output") or {}
    target = language((config.get("languages") or {}).get("target") or "")
    suffix = output_suffix(target)
    rule = str(output.get("filename_rule") or f"原文件名.{suffix}.txt")
    name = rule.replace("原文件名", source_path.stem)
    return Path(name).name or f"{source_path.stem}.{suffix}.txt"


def rebuild_outputs(
    state: dict[str, Any],
    output_dir: Path,
    segments: list[str],
    layout: list[dict[str, int | str]],
    request_start: int,
    request_end: int,
    final_name: str,
    target_language: str = "",
    publish_epub: bool = True,
) -> tuple[str | None, str | None]:
    records = sorted(state.get("chunks") or [], key=lambda item: (int(item["start"]), int(item["end"])))
    contiguous, cursor = contiguous_records(records, output_dir, segments, target_language)
    total = len(segments)
    partial_path: Path | None = None
    final_path: Path | None = None
    if contiguous:
        combined = render_layout(
            layout, translations_from_records(contiguous, output_dir, segments, target_language), 1, cursor - 1
        )
        if cursor > total:
            final_path = output_dir / final_name
            atomic_text(final_path, combined)
            partial = output_dir / f"{final_name}.partial"
            if partial.is_file():
                partial.unlink()
        else:
            partial_path = output_dir / f"{final_name}.partial"
            atomic_text(partial_path, combined)

    selected = [
        item
        for item in records
        if int(item["start"]) >= request_start
        and int(item["end"]) <= request_end
        and record_is_valid(item, output_dir, segments, target_language)
    ]
    range_cursor = request_start
    range_records: list[dict[str, Any]] = []
    for item in selected:
        if int(item["start"]) == range_cursor:
            range_records.append(item)
            range_cursor = int(item["end"]) + 1
    range_path: Path | None = None
    if range_cursor > request_end and not (request_start == 1 and request_end == total):
        range_path = output_dir / f"第{request_start:06d}-{request_end:06d}段.txt"
        combined = render_layout(
            layout,
            translations_from_records(range_records, output_dir, segments, target_language),
            request_start,
            request_end,
        )
        atomic_text(range_path, combined)
    visible = final_path or partial_path
    if (publish_epub and contiguous
            and (output_dir.parent / '.epub-source.json').is_file()):
        from epub_translation import export
        if partial_path:
            atomic_text(output_dir / final_name, partial_path.read_text(encoding='utf-8'))
        export(output_dir.parent, output_dir / Path(final_name).with_suffix('.epub'), segments,
               translations_from_records(contiguous, output_dir, segments, target_language),
               language_name=target_language)
    return str(visible) if visible else None, str(range_path) if range_path else None


def publish_saved_outputs(raw_root: Path | str) -> dict[str, str | None]:
    """Rebuild user-visible TXT/EPUB only from already verified chunk files.

    This is the pause/repair path: it never contacts a model and never invents
    a segment. The same validation and renderer used by the translation worker
    decide the newest contiguous checkpoint that may be published.
    """
    task_root = Path(raw_root).expanduser().resolve()
    with task_lock(task_root):
        task_file = task_root / '翻译任务.yaml'
        if not task_file.is_file():
            raise ValueError('当前目录缺少翻译任务.yaml')
        config = yaml.safe_load(task_file.read_text(encoding='utf-8'))
        if not isinstance(config, dict):
            raise ValueError('翻译任务.yaml 顶层必须是对象')
        validate_task(task_root, config)
        source = config.get('source') or {}
        raw_files = source.get('files') or []
        if not isinstance(raw_files, list) or not raw_files:
            raise ValueError('翻译任务.yaml 没有 source.files')
        paths = [resolve_path(task_root, str(item)) for item in raw_files]
        if any(not within(task_root, path) or not path.is_file() for path in paths):
            raise ValueError('原文副本不可用，未发布阅读版')
        encoding = str(source.get('encoding') or 'UTF-8')
        requested_mode = str((config.get('chunking') or {}).get('segment_mode') or 'auto')
        segments: list[str] = []
        layout: list[dict[str, int | str]] = []
        selected_modes: list[str] = []
        for path in paths:
            current, selected, current_layout = split_source(
                path.read_text(encoding=encoding), requested_mode)
            if segments and layout and layout[-1].get('kind') != 'blank':
                layout.append({'kind': 'blank'})
            offset = len(segments)
            layout.extend([
                {**token, 'id': int(token['id']) + offset}
                if token.get('kind') == 'segment' else dict(token)
                for token in current_layout
            ])
            segments.extend(current)
            selected_modes.append(selected)
        selected_mode = (selected_modes[0] if len(set(selected_modes)) == 1
                         else '+'.join(selected_modes))
        output = config.get('output') or {}
        output_dir = resolve_path(task_root, str(output.get('directory') or '译文'))
        if not within(task_root, output_dir):
            raise ValueError('输出目录必须位于当前任务目录内')
        state = load_json(output_dir / '.hy-direct-state.json', {})
        if (state.get('schema_version') != STATE_SCHEMA_VERSION
                or state.get('protocol') != OUTPUT_PROTOCOL
                or not source_digest_matches(
                    state.get('source_hash'), paths, state.get('source_files'))
                or state.get('segment_hash') != segmentation_digest(segments, selected_mode)):
            raise ValueError('断点与当前原文或分段不一致，未发布阅读版')
        final_name = managed_name(config, paths[0])
        visible, _ = rebuild_outputs(
            state, output_dir, segments, layout, 1, len(segments), final_name,
            str((config.get('languages') or {}).get('target') or ''),
            publish_epub=True)
        if not visible:
            raise ValueError('还没有连续、可验证的译块可供发布')
        epub = output_dir / Path(final_name).with_suffix('.epub')
        return {'visible': visible, 'epub': str(epub) if epub.is_file() else None}


def run(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    root = Path(args.task_root).expanduser().resolve()
    with task_lock(root):
        return run_locked(args)


def run_locked(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    task_root = Path(args.task_root).expanduser().resolve()
    task_file = task_root / "翻译任务.yaml"
    if not task_file.is_file():
        raise ValueError("当前目录缺少翻译任务.yaml，请先使用准备任务")
    config = yaml.safe_load(task_file.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("翻译任务.yaml 顶层必须是对象")
    validate_task(task_root, config)
    languages = config.get("languages") or {}
    source_language = clean_rule(languages.get("source"))
    target_language = clean_rule(languages.get("target"))
    if not source_language or "请填写" in source_language or not target_language:
        raise ValueError("翻译任务.yaml 缺少有效的源语言或目标语言")

    source = config.get("source") or {}
    raw_files = source.get("files") or []
    if not isinstance(raw_files, list) or not raw_files:
        raise ValueError("翻译任务.yaml 没有 source.files")
    paths = [resolve_path(task_root, str(item)) for item in raw_files]
    outside_sources = [str(path) for path in paths if not within(task_root, path)]
    if outside_sources:
        raise ValueError("原文必须位于当前任务目录内；请先用准备任务复制原文：" + "；".join(outside_sources))
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise ValueError("原文文件不存在：" + "；".join(missing))
    encoding = str(source.get("encoding") or "UTF-8")
    chunking = config.get("chunking") or {}
    requested_mode = str(chunking.get("segment_mode") or "auto")
    segments: list[str] = []
    layout: list[dict[str, int | str]] = []
    selected_modes: list[str] = []
    for path in paths:
        current, selected, current_layout = split_source(path.read_text(encoding=encoding), requested_mode)
        if segments and layout and layout[-1].get("kind") != "blank":
            layout.append({"kind": "blank"})
        offset = len(segments)
        for token in current_layout:
            copied = dict(token)
            if copied.get("kind") == "segment":
                copied["id"] = int(copied["id"]) + offset
            layout.append(copied)
        segments.extend(current)
        selected_modes.append(selected)
    selected_mode = selected_modes[0] if len(set(selected_modes)) == 1 else "+".join(selected_modes)
    total = len(segments)
    segment_hash = segmentation_digest(segments, selected_mode)

    output = config.get("output") or {}
    output_dir = resolve_path(task_root, str(output.get("directory") or "译文"))
    if not within(task_root, output_dir):
        raise ValueError("输出目录必须位于当前任务目录内")
    output_dir.mkdir(parents=True, exist_ok=True)
    state_path = output_dir / ".hy-direct-state.json"
    state_existed = state_path.is_file()
    final_name = managed_name(config, paths[0])
    protected_outputs = (output_dir / final_name, output_dir / f"{final_name}.partial")
    overwrite_existing = output.get("overwrite_existing") is True
    if not state_existed and not overwrite_existing:
        collisions = [str(path) for path in protected_outputs if path.exists()]
        if collisions:
            raise ValueError(
                "检测到既有译文且没有直译断点，为避免覆盖已停止："
                + "；".join(collisions)
                + "。请另建任务目录，或确认后将 output.overwrite_existing 设为 true"
            )

    progress_path = resolve_path(task_root, str((config.get("progress") or {}).get("file") or "翻译进度.json"))
    if not within(task_root, progress_path) or progress_path in paths:
        raise ValueError("进度文件必须位于当前任务目录内，且不能与原文相同")
    digest = source_digest(paths)
    state = load_json(
        state_path,
        {
            "schema_version": STATE_SCHEMA_VERSION,
            "protocol": OUTPUT_PROTOCOL,
            "source_hash": digest,
            "source_hash_schema": BOOK_IDENTITY_SCHEMA,
            "source_files": source_file_names(paths),
            "segment_mode": selected_mode,
            "segment_hash": segment_hash,
            "total_segments": total,
            "chunks": [],
        },
    )
    if state_existed and (
        state.get("schema_version") != STATE_SCHEMA_VERSION or state.get("protocol") != OUTPUT_PROTOCOL
    ):
        raise ValueError(
            "检测到旧版、未逐段验证的直译断点；为避免复用不完整译文已停止。"
            "请另建任务目录，或先将旧 output 与翻译进度.json 归档后重跑。"
        )
    if not source_digest_matches(state.get("source_hash"), paths, state.get("source_files")):
        raise ValueError("原文内容已变化。为避免错位，未继续使用旧断点；请另建任务目录或人工核对状态")
    checkpoint_digest = str(state["source_hash"])
    checkpoint_schema = str(
        state.get("source_hash_schema")
        or (BOOK_IDENTITY_SCHEMA if checkpoint_digest == digest else "path-digest-legacy")
    )
    if state_existed and state.get("segment_mode") != selected_mode:
        raise ValueError("原文分段方式已变化。为避免错位，未继续使用旧断点；请归档旧译文后重跑")
    if state_existed and state.get("segment_hash") and state["segment_hash"] != segment_hash:
        raise ValueError("原文分段映射已变化。为避免错位，未继续使用旧断点；请归档旧译文后重跑")
    state["segment_hash"] = segment_hash

    engine_record = {
        "api_base": str(args.api_base).rstrip("/"),
        "model": str(args.model),
    }
    engine_identity = fingerprint(engine_record)
    if state.get("chunks"):
        saved_identity = state.get("engine_identity")
        legacy_models = {
            str(item.get("model"))
            for item in state.get("chunks") or []
            if isinstance(item, dict) and item.get("model")
        }
        if saved_identity and saved_identity != engine_identity:
            raise ValueError("翻译模型或服务已改变，请新建译本，不能混用已有译块")
        if not saved_identity and legacy_models and legacy_models != {str(args.model)}:
            raise ValueError("翻译模型已改变，请新建译本，不能混用已有译块")
        if not saved_identity and not legacy_models and str(args.model) != "hy-mt2-30b-q6":
            raise ValueError("旧译本无法确认原模型，请新建译本后再切换本地模型")
    state["engine_identity"] = engine_identity
    state["engine"] = engine_record

    mode = (config.get("glossary") or {}).get("mode", "none")
    prompt_mode = str(getattr(args, "prompt_mode", "markers") or "markers")
    if prompt_mode not in ("markers", "native"):
        raise ValueError("prompt_mode 只能是 markers 或 native")
    rules_hash = fingerprint({"languages": config.get("languages"), "translation": config.get("translation"),
                              "context": config.get("context"), "glossary": config.get("glossary", {"mode": "none"}),
                              "segment_mode": selected_mode,
                              "prompt_revision": NATIVE_PROMPT_REVISION if prompt_mode == "native" else "legacy-markers-v1"})
    if state.get("chunks") and ((state.get("rules_hash") and state["rules_hash"] != rules_hash) or (not state.get("rules_hash") and mode != "none")):
        raise ValueError("翻译规则或术语模式已改变，请另建任务，不能混用已有译块")
    state["rules_hash"] = rules_hash

    base_url = args.api_base.rstrip("/")
    url = completion_url(base_url)
    auth_provider = str(getattr(args, "auth_provider", "none"))
    key = load_key(Path(args.auth_path).expanduser(), auth_provider)
    entries = prepare_glossary(task_root, config, segments, digest,
        lambda prompt: translate_once(url, args.model, key, prompt, int(args.request_timeout)), emit)
    glossary_hash = fingerprint(entries)
    if state.get("chunks") and state.get("glossary_hash") and state["glossary_hash"] != glossary_hash:
        raise ValueError("术语表与既有断点不同，请另建任务以保持全书一致")
    state["glossary_hash"] = glossary_hash
    config["_glossary_entries"] = entries

    covered = normalized_ranges(state.get("chunks") or [], output_dir, segments, target_language)
    start = int(args.start_segment)
    end = int(args.end_segment)
    if start == 0:
        missing_start = next_missing(total, covered)
        if missing_start is None:
            visible, _ = rebuild_outputs(
                state, output_dir, segments, layout, 1, total, final_name, target_language
            )
            return {
                "type": "result",
                "status": "already_complete",
                "requested_range": [1, total],
                "total_segments": total,
                "completed_ranges": covered,
                "next_segment": None,
                "output_file": visible,
                "state_file": str(state_path),
                "elapsed_seconds": 0.0,
                "failures": [],
            }, True
        start = missing_start
    if end == 0:
        end = total
    if not (1 <= start <= end <= total):
        raise ValueError(f"翻译范围必须位于 1～{total} 段，当前为 {start}～{end}")
    context_count = max(0, min(20, int(args.context_segments)))
    max_chars = max(500, min(6000, int(chunking.get("max_source_chars") or 4000)))
    max_segments = max(1, min(200, int(chunking.get("max_segments") or 20)))
    parallel = max(1, min(2, int(chunking.get("parallel_requests") or 2)))
    gaps = uncovered_ranges(start, end, covered)
    todo: list[dict[str, Any]] = []
    for left, right in gaps:
        todo.extend(make_chunks(segments, left, right, max_chars, max_segments))

    base_url = args.api_base.rstrip("/")
    url = completion_url(base_url)
    auth_provider = str(getattr(args, "auth_provider", "none"))
    key = load_key(Path(args.auth_path).expanduser(), auth_provider)
    model = args.model
    chunks_dir = output_dir / ".hy-direct-chunks"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    failures: list[dict[str, Any]] = []
    started_at = time.monotonic()
    completed_now = 0
    epub_task = (task_root / '.epub-source.json').is_file()
    last_epub_publish_at = 0.0
    last_epub_publish_chunk = 0
    total_source_chars = sum(len(compact_text(segment)) for segment in segments)

    emit(
        {
            "type": "started",
            "total_segments": total,
            "total_source_chars": total_source_chars,
            "pending_chunks": len(todo),
            "prompt_mode": prompt_mode,
        }
    )

    def translate_chunk(
        chunk: dict[str, Any],
    ) -> tuple[
        list[tuple[dict[str, Any], list[dict[str, Any]], float]],
        list[dict[str, Any]],
    ]:
        chunk_start, chunk_end = int(chunk["start"]), int(chunk["end"])
        emit({"type": "working", "range": [chunk_start, chunk_end], "phase": "translate"})
        try:
            # When bisection reaches one hard-wrapped source line, adjacent
            # context can tempt 模型to translate the continuation and emit a
            # duplicate marker. The line itself is still complete input for
            # this fallback; removing context keeps the 1:1 contract exact.
            chunk_context = 0 if int(chunk["start"]) == int(chunk["end"]) else context_count
            if prompt_mode == "native":
                reference = (
                    recent_translation_reference(
                        state,
                        output_dir,
                        segments,
                        int(chunk["start"]),
                        target_language,
                    )
                    if parallel == 1
                    else ""
                )
                prompt = build_native_prompt(
                    config, segments, chunk, chunk_context, reference, marked=False
                )
                try:
                    translated, elapsed = translate_with_retry(
                        url,
                        model,
                        key,
                        prompt,
                        int(args.request_timeout),
                        lambda content: parse_plain_translation(
                            content,
                            segments,
                            int(chunk["start"]),
                            int(chunk["end"]),
                            target_language,
                        ),
                    )
                except TranslationContractError:
                    # Escalate structure only after the native answer proved
                    # impossible to map.  Hy-MT2 explicitly supports exact
                    # delimiter preservation, so this remains model-native.
                    fallback = build_native_prompt(
                        config, segments, chunk, chunk_context, reference, marked=True
                    )
                    emit(
                        {
                            "type": "working",
                            "range": [chunk_start, chunk_end],
                            "phase": "structure_retry",
                        }
                    )
                    translated, elapsed = translate_with_retry(
                        url,
                        model,
                        key,
                        fallback,
                        int(args.request_timeout),
                        lambda content: parse_marked_translation(
                            content,
                            segments,
                            int(chunk["start"]),
                            int(chunk["end"]),
                            target_language,
                        ),
                        retry_contract_errors=int(chunk["start"]) == int(chunk["end"]),
                    )
            else:
                prompt = build_prompt(config, segments, chunk, chunk_context)
                translated, elapsed = translate_with_retry(
                    url,
                    model,
                    key,
                    prompt,
                    int(args.request_timeout),
                    lambda content: parse_marked_translation(
                        content, segments, int(chunk["start"]), int(chunk["end"]), target_language
                    ),
                    retry_contract_errors=int(chunk["start"]) == int(chunk["end"]),
                )
            return [(chunk, translated, elapsed)], []
        except (TranslationContractError, RuntimeError) as exc:
            start, end = int(chunk["start"]), int(chunk["end"])
            # A malformed multi-segment reply is recoverable: reduce the
            # response contract to smaller groups.  Other transport/API errors
            # must remain visible rather than silently multiplying requests.
            if not isinstance(exc, TranslationContractError) and "长度上限" not in str(exc):
                raise
            if start >= end:
                return [], [{"range": [start, end], **failure_payload(exc)}]
            middle = (start + end) // 2
            emit(
                {
                    "type": "working",
                    "range": [start, end],
                    "phase": "split",
                    "split_ranges": [[start, middle], [middle + 1, end]],
                }
            )
            left_items, left_failures = translate_chunk(make_chunk(segments, start, middle))
            if parallel == 1:
                # Save an accepted left branch before translating the right
                # branch.  This makes bisection visible and recoverable instead
                # of withholding the whole original chunk until every leaf is
                # finished; it also lets the right branch inherit the adopted
                # translation tail.
                persist(left_items, left_failures)
                left_items, left_failures = [], []
            right_items, right_failures = translate_chunk(make_chunk(segments, middle + 1, end))
            if parallel == 1:
                persist(right_items, right_failures)
                right_items, right_failures = [], []
            return left_items + right_items, left_failures + right_failures

    def job(
        chunk: dict[str, Any],
    ) -> tuple[
        list[tuple[dict[str, Any], list[dict[str, Any]], float]],
        list[dict[str, Any]],
    ]:
        return translate_chunk(chunk)

    def persist(
        resolved_items: list[tuple[dict[str, Any], list[dict[str, Any]], float]],
        resolved_failures: list[dict[str, Any]],
    ) -> None:
        nonlocal completed_now, covered, last_epub_publish_at, last_epub_publish_chunk
        failures.extend(resolved_failures)
        for resolved, translated, elapsed in resolved_items:
            source_hash = chunk_digest(resolved["text"])
            relative = (
                Path(".hy-direct-chunks")
                / f"{resolved['start']:06d}-{resolved['end']:06d}-{source_hash[:10]}.json"
            )
            atomic_json(
                output_dir / relative,
                {
                    "schema_version": STATE_SCHEMA_VERSION,
                    "protocol": OUTPUT_PROTOCOL,
                    "start": resolved["start"],
                    "end": resolved["end"],
                    "source_sha256": source_hash,
                    "segments": translated,
                },
            )
            state["chunks"] = [
                item
                for item in state.get("chunks") or []
                if not (
                    int(item.get("start", -1)) == resolved["start"]
                    and int(item.get("end", -1)) == resolved["end"]
                )
            ]
            state["chunks"].append(
                {
                    "start": resolved["start"],
                    "end": resolved["end"],
                    "path": str(relative),
                    "protocol": OUTPUT_PROTOCOL,
                    "source_sha256": source_hash,
                    "model": model,
                    "seconds": round(elapsed, 3),
                    "translated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            atomic_json(state_path, state)
            completed_now += 1
            covered = normalized_ranges(state["chunks"], output_dir, segments, target_language)
            next_segment = next_missing(total, covered)
            completed_segments = sum(right - left + 1 for left, right in covered)
            completed_source_chars = sum(
                len(compact_text(segments[number - 1]))
                for left, right in covered
                for number in range(left, right + 1)
            )
            percent = (
                round(completed_source_chars * 100.0 / total_source_chars, 2)
                if total_source_chars
                else 0.0
            )
            progress = load_json(progress_path, {})
            progress.update(
                {
                    "schema_version": 3,
                    "protocol": OUTPUT_PROTOCOL,
                    "status": "completed" if next_segment is None else "in_progress",
                    "source_files": source_file_names(paths),
                    "source_hash": checkpoint_digest,
                    "source_hash_schema": checkpoint_schema,
                    "segment_mode": selected_mode,
                    "total_segments": total,
                    "completed_segments": completed_segments,
                    "total_source_chars": total_source_chars,
                    "completed_source_chars": completed_source_chars,
                    "percent": percent,
                    "completed_ranges": covered,
                    "next_segment": next_segment,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            atomic_json(progress_path, progress)
            now = time.monotonic()
            publish_epub = (not epub_task or completed_now == 1 or next_segment is None
                            or completed_now - last_epub_publish_chunk >= 5
                            or now - last_epub_publish_at >= 300)
            visible, _ = rebuild_outputs(
                state, output_dir, segments, layout, start, end, final_name,
                target_language, publish_epub=publish_epub)
            if epub_task and publish_epub:
                last_epub_publish_at = now
                last_epub_publish_chunk = completed_now
            emit(
                {
                    "type": "progress",
                    "completed_chunks": completed_now,
                    "new_chunks": len(todo),
                    "range": [resolved["start"], resolved["end"]],
                    "seconds": round(elapsed, 3),
                    "completed_segments": completed_segments,
                    "total_segments": total,
                    "completed_source_chars": completed_source_chars,
                    "total_source_chars": total_source_chars,
                    "percent": percent,
                    "next_segment": next_segment,
                    "output_file": visible,
                }
            )

    if todo and parallel == 1:
        # Persist before constructing the next prompt so it can carry a small
        # aligned source/translation tail.  This is the default literary mode.
        for chunk in todo:
            try:
                persist(*job(chunk))
            except Exception as exc:
                failures.append({"range": [chunk["start"], chunk["end"]], **failure_payload(exc)})
    elif todo:
        # Optional speed mode.  It intentionally gives up rolling translated
        # context while preserving adjacent source context and all QA gates.
        with ThreadPoolExecutor(max_workers=min(parallel, len(todo))) as pool:
            futures = {pool.submit(job, chunk): chunk for chunk in todo}
            for future in as_completed(futures):
                chunk = futures[future]
                try:
                    persist(*future.result())
                except Exception as exc:
                    failures.append({"range": [chunk["start"], chunk["end"]], **failure_payload(exc)})

    covered = normalized_ranges(state.get("chunks") or [], output_dir, segments, target_language)
    next_segment = next_missing(total, covered)
    visible, range_file = rebuild_outputs(
        state, output_dir, segments, layout, start, end, final_name, target_language
    )
    summary = {
        "type": "result",
        "status": "partial_failure" if failures else ("completed" if next_segment is None else "range_completed"),
        "requested_range": [start, end],
        "total_segments": total,
        "total_source_chars": total_source_chars,
        "completed_source_chars": sum(
            len(compact_text(segments[number - 1]))
            for left, right in covered
            for number in range(left, right + 1)
        ),
        "segment_mode": selected_mode,
        "new_chunks": completed_now,
        "reused_ranges": covered if not todo else [],
        "completed_ranges": covered,
        "next_segment": next_segment,
        "output_file": visible,
        "range_file": range_file,
        "state_file": str(state_path),
        "elapsed_seconds": round(time.monotonic() - started_at, 3),
        "failures": failures,
    }
    return summary, not failures


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-root", required=True)
    parser.add_argument("--start-segment", type=int, default=0)
    parser.add_argument("--end-segment", type=int, default=0)
    parser.add_argument("--context-segments", type=int, default=3)
    parser.add_argument("--api-base", default="http://127.0.0.1:11434/v1")
    parser.add_argument("--model", default="jingdu-hy-mt2:7b-q4")
    parser.add_argument("--auth-path", required=True)
    parser.add_argument("--auth-provider", default="none")
    parser.add_argument("--request-timeout", type=int, default=120)
    parser.add_argument(
        "--prompt-mode",
        choices=("markers", "native"),
        default="markers",
        help="native tries Hy-MT2's short official prompt before delimiter fallback",
    )
    parser.add_argument(
        "--parent-pid",
        type=int,
        default=0,
        help="exit if this desktop parent disappears; 0 disables the watchdog",
    )
    args = parser.parse_args()
    start_parent_watchdog(args.parent_pid)
    try:
        result, success = run(args)
        emit(result)
        return 0 if success else 2
    except Exception as exc:
        # Keep ``error`` for older desktops; the structured fields are additive.
        emit({"type": "error", "error": str(exc)[:1000],
              "message_code": failure_code(exc), "message_args": [], "detail": str(exc)[:1000]})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
