"""Read-only, incremental paragraph model for the native live reader.

Qt virtualizes delegates; a long book is never one enormous TextEdit. Only
atomic, contiguous output is read. Reader settings are separate from task data.
"""
from __future__ import annotations

import re
import codecs
from pathlib import Path

from PySide6.QtCore import QAbstractListModel, QModelIndex, Qt, Property, Signal, Slot

import ui_messages


HEADING = re.compile(
    r"^(?:#{1,6}\s*)?(?:第.{1,20}[章节卷部回]|(?:chapter|part|book)\s+\S+|"
    r"序章|序言|前言|楔子|引子|尾声|后记|终章|结语|附录|致谢)",
    re.I,
)


def decode_reading_text(data: bytes) -> str:
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        return data.decode('utf-32')
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return data.decode('utf-16')
    for encoding in ('utf-8-sig', 'gb18030'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise ValueError('无法识别 TXT 编码，请另存为 UTF-8 后打开。')


def paragraph_spans(text: str) -> list[tuple[str, int, int]]:
    # Outputs already have intentional line breaks. Do not reinterpret markup
    # as HTML, and bound an individual delegate even on malformed single lines.
    result = []
    offset = 0
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.strip()
        start = offset + len(raw_line) - len(raw_line.lstrip())
        offset += len(raw_line)
        if not line:
            continue
        while len(line) > 1400:
            cut = max(line.rfind(mark, 700, 1400) + 1 for mark in "。！？.!?；; ")
            cut = cut if cut >= 700 else 1400
            result.append((line[:cut], start, start + cut))
            start += cut
            line = line[cut:]
        if line:
            result.append((line, start, start + len(line)))
    return result


def paragraphs(text: str) -> list[str]:
    return [row[0] for row in paragraph_spans(text)]


def prepare_document(path: Path) -> dict:
    """Read and classify text without touching a Qt model."""
    path = Path(path)
    for _ in range(2):
        before = path.stat()
        if before.st_size > 50 * 1024 * 1024:
            raise ValueError("阅读文件超过 50 MB，请按卷打开。")
        values = paragraphs(decode_reading_text(path.read_bytes()))
        after = path.stat()
        before_key = (before.st_dev, before.st_ino, before.st_size,
                      before.st_mtime_ns, before.st_ctime_ns)
        after_key = (after.st_dev, after.st_ino, after.st_size,
                     after.st_mtime_ns, after.st_ctime_ns)
        signature = (str(path), *after_key)
        if before_key == after_key:
            headings = [len(value) < 90 and bool(HEADING.match(value)) for value in values]
            chapters = [{"title": value[:70], "position": index}
                        for index, (value, heading) in enumerate(zip(values, headings)) if heading]
            return dict(signature=signature, rows=values, headings=headings, chapters=chapters)
    raise OSError("阅读文件正在替换，请稍后重试。")


class ReaderModel(QAbstractListModel):
    updated = Signal(bool)  # True only when the document identity changed
    resetStarting = Signal()  # Lets the view retain its row across same-book replacement.
    metaChanged = Signal()
    TextRole = int(Qt.ItemDataRole.UserRole) + 1
    HeadingRole = TextRole + 1
    SourceRole = HeadingRole + 1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[str] = []
        self.heading_rows: list[bool] = []
        self._chapters: list[dict] = []
        self.source_rows: list[str] = []
        self._path = ""
        self._signature = None
        self._query = ""
        self._matches: list[int] = []
        self._match_cursor = -1
        self._error = ""

    def roleNames(self):
        return {self.TextRole: b"paragraphText", self.HeadingRole: b"isHeading",
                self.SourceRole: b"sourceText"}

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.rows)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not 0 <= index.row() < len(self.rows):
            return None
        value = self.rows[index.row()]
        if role in (self.TextRole, Qt.ItemDataRole.DisplayRole):
            return value
        if role == self.HeadingRole:
            return self.heading_rows[index.row()] if index.row() < len(self.heading_rows) else False
        if role == self.SourceRole:
            return self.source_rows[index.row()] if index.row() < len(self.source_rows) else ''
        return None

    @Property(bool, notify=metaChanged)
    def hasSources(self):  # noqa: N802
        return any(self.source_rows)

    def set_sources(self, values):
        values = list(values) if len(values) == len(self.rows) else []
        if values == self.source_rows:
            return
        self.source_rows = values
        if self.rows:
            self.dataChanged.emit(self.index(0), self.index(len(self.rows) - 1), [self.SourceRole])
        self.metaChanged.emit()

    @Property(int, notify=metaChanged)
    def count(self):
        return len(self.rows)

    @Property(str, notify=metaChanged)
    def error(self):
        return self._error

    @Property(str, notify=metaChanged)
    def searchSummary(self):  # noqa: N802
        if not self._query:
            return ""
        if not self._matches:
            return ui_messages.render(ui_messages.Message(ui_messages.MessageCode.SEARCH_EMPTY))
        return ui_messages.render(ui_messages.Message(
            ui_messages.MessageCode.SEARCH_POSITION,
            (max(0, self._match_cursor) + 1, len(self._matches))))

    @Property("QVariantList", notify=metaChanged)
    def chapters(self):
        return list(self._chapters)

    def clear(self):
        if not (self.rows or self.source_rows or self._path or self._error or self._query):
            return
        self.resetStarting.emit()
        self.beginResetModel()
        self.rows = []
        self.heading_rows = []
        self._chapters = []
        self.source_rows = []
        self._path = ""
        self._signature = None
        self._error = ''
        self._query = ''
        self._match_cursor = -1
        self.endResetModel()
        self._refresh_matches()
        self.metaChanged.emit()
        self.updated.emit(True)

    def apply_prepared(self, prepared: dict, *, identity: str):
        values = list(prepared['rows'])
        headings = list(prepared['headings'])
        chapters = list(prepared['chapters'])
        signature = tuple(prepared['signature'])
        if identity == self._path and signature == self._signature:
            return True
        new_document = identity != self._path
        if new_document:
            self._query = ""
            self._match_cursor = -1
        if new_document or len(values) < len(self.rows) or values[:len(self.rows)] != self.rows:
            self.resetStarting.emit()
            self.beginResetModel()
            self.rows = values
            self.heading_rows = headings
            self._chapters = chapters
            self.source_rows = []
            self.endResetModel()
        elif len(values) > len(self.rows):
            start = len(self.rows)
            self.beginInsertRows(QModelIndex(), start, len(values) - 1)
            self.rows.extend(values[start:])
            self.heading_rows.extend(headings[start:])
            self._chapters = chapters
            self.source_rows = []
            self.endInsertRows()
        else:
            self.heading_rows = headings
            self._chapters = chapters
        self._path = identity
        self._signature = signature
        self._error = ""
        self._refresh_matches()
        self.metaChanged.emit()
        self.updated.emit(new_document)
        return True

    def load(self, path: Path, *, identity: str | None = None):
        identity = identity or str(path)
        try:
            stat = Path(path).stat()
            signature = (str(path), stat.st_dev, stat.st_ino, stat.st_size,
                         stat.st_mtime_ns, stat.st_ctime_ns)
            if identity == self._path and signature == self._signature:
                return True
            prepared = prepare_document(path)
        except (OSError, ValueError) as exc:
            self._error = f"暂时无法读取译文：{exc}"[:240]
            self.metaChanged.emit()
            return False  # Keep the last valid page during atomic replacement.
        return self.apply_prepared(prepared, identity=identity)

    def _refresh_matches(self):
        query = self._query.casefold()
        self._matches = [i for i, text in enumerate(self.rows) if query in text.casefold()] if query else []
        self._match_cursor = min(self._match_cursor, len(self._matches) - 1)

    @Slot(str, result=int)
    def search(self, query):
        self._query = query.strip()[:200]
        self._match_cursor = -1
        self._refresh_matches()
        return self.nextMatch(1)

    @Slot(int, result=int)
    def nextMatch(self, direction):  # noqa: N802
        if not self._matches:
            self.metaChanged.emit()
            return -1
        self._match_cursor = (self._match_cursor + (1 if direction >= 0 else -1)) % len(self._matches)
        self.metaChanged.emit()
        return self._matches[self._match_cursor]
