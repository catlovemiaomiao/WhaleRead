"""Independent, persistent reading columns and book-local bookmarks.

Reading columns deliberately do not own translation workers. A running task can
keep writing one book while other books remain visible beside it.
"""
from __future__ import annotations

from apple_services import choose_open_file

import hashlib
import json
import sys
import uuid
from concurrent.futures import CancelledError, ThreadPoolExecutor
from datetime import datetime
from functools import partial
from pathlib import Path

import yaml
from PySide6.QtCore import QObject, QEvent, Qt, Property, QUrl, Signal, Slot
from PySide6.QtWidgets import QFileDialog

from book_identity import canonical_path, file_sha256
from bookshelf import Bookshelf
from epub_reader import cache_stats, clear_epub_cache, inspect_epub
import ui_messages
from reader import ReaderModel
from epub_session import EpubSession, release_prepared
from reader_loader import (epub_work_signature, existing_target, inspect_source,
                           is_epub_review_text, path_signature, prepare_book,
                           publish_epub_checkpoint)


NOTE_COLORS = {'yellow', 'rose', 'blue', 'green'}


class ReaderLoadSignals(QObject):
    ready = Signal(object)


def read_list(settings, key):
    try:
        value = json.loads(settings.value(key, '[]', type=str))
        return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []
    except (ValueError, TypeError):
        return []


def note_document_id(reader_identity):
    identity = str(reader_identity or '')
    return hashlib.sha256(identity.encode()).hexdigest()[:24] if identity else ''


def stable_note_anchor(anchor):
    """Comparable identity for anchors that survive reflow and translation."""
    if not isinstance(anchor, dict):
        return None
    try:
        if anchor.get('kind') == 'source-segment':
            segment = int(anchor.get('segment'))
            if segment <= 0:
                return None
            return ('source-segment', segment, int(anchor.get('offset', 0) or 0))
        if anchor.get('kind') == 'epub-block':
            chapter = str(anchor.get('chapter') or '')
            block = int(anchor.get('block'))
            if not chapter or block < 0:
                return None
            return ('epub-block', chapter, block)
    except (TypeError, ValueError, OverflowError):
        return None
    return None


class EpubWheelBridge(QObject):
    """Handle both window-delivered and direct Chromium delegate wheel events."""
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner

    def eventFilter(self, watched, event):
        if event.type() != QEvent.Type.Wheel or not self.owner.epubScrollLinked:
            return False
        from PySide6.QtQuick import QQuickWindow, QQuickItem
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            return False
        def find(item):
            if not item.isVisible():
                return None
            if item.objectName() == 'epubWebView' and item.contains(item.mapFromScene(event.position())):
                return item
            for child in item.childItems():
                found = find(child)
                if found is not None:
                    return found
            return None
        web = None
        if isinstance(watched, QQuickWindow):
            web = find(watched.contentItem())
        elif isinstance(watched, QQuickItem):
            # Native macOS events can arrive directly at Chromium's delegate,
            # without first passing through the QQuickWindow event filter.
            item = watched
            while item is not None:
                if item.objectName() == 'epubWebView':
                    web = item
                    break
                item = item.parentItem()
        if web is None or not web.property('scrollReady'):
            return False
        identity = web.property('readingColumnId')
        column = next((c for c in self.owner.readingColumns if c.columnId == identity), None)
        if not column or not self.owner._epub_pair(column):
            return False
        delta = event.pixelDelta().y() or event.angleDelta().y() / 120 * 90
        if not delta:
            return False
        column.epub.wheelRequested.emit(-delta)
        event.accept()
        return True


class ReaderColumn(QObject):
    """One independently scrolling reader surface."""
    changed = Signal()
    readingAboutToChange = Signal()

    @staticmethod
    def _descriptor_reader_identity(descriptor):
        descriptor = descriptor or {}
        identity = str(descriptor.get('id') or '')
        return str(
            descriptor.get('reader_identity')
            or (identity if identity.startswith('book:') else '')
            or descriptor.get('job') or descriptor.get('source') or '')

    def __init__(self, owner, descriptor=None, *, column_id='', reader=None,
                 reader_identity='', manual_empty=False, autoload=True):
        super().__init__(owner)
        self.owner = owner
        self._epub_peer = None
        self._group_parent = None
        self._epub_mode = str((descriptor or {}).get('epub_mode') or '')
        self.epub = EpubSession(self)
        self.epub.changed.connect(owner.changed)
        self._column_id = column_id or uuid.uuid4().hex
        self._book = dict(descriptor or {})
        self._manual_empty = bool(manual_empty and not self._book.get('id'))
        self._read_file = str(self._book.get('reading_file') or '')
        self._machine_path = str(self._book.get('machine_path') or '')
        self._reader_identity = str(
            reader_identity or self._descriptor_reader_identity(self._book))
        self.reader = reader or ReaderModel(self)
        # A restored first column may reuse the model from a transient column
        # built while restoring the last task.  Transfer QObject ownership
        # before that old column receives deleteLater(), otherwise the model is
        # destroyed as QML starts evaluating the delegate.
        if self.reader.parent() is not self:
            self.reader.setParent(self)
        self._bilingual_signature = None
        self._alignment_signature = None
        self._alignment_data = ([], [])
        self._bilingual_message = ''
        self._reading_mode = str((descriptor or {}).get('reading_mode') or '')
        self._notes_revision = 0
        self._loading = False
        self._loading_title = ''
        self._load_token = ''
        self._refresh_signature = None
        self._refresh_token = ''
        if self._book and autoload:
            self.refresh()

    @Property(str, constant=True)
    def columnId(self):  # noqa: N802
        return self._column_id

    @Property(QObject, constant=True)
    def epubSession(self):
        return self.epub

    @Property(bool, notify=changed)
    def epubActive(self):
        return Path(self.readingPath).suffix.lower() == '.epub' or bool(getattr(self, '_epub_translation_path', ''))

    @Property(bool, notify=changed)
    def epubTranslated(self):
        return bool(getattr(self, '_epub_translation_path', ''))

    @Property(QObject, notify=changed)
    def epubPeer(self):
        return self._epub_peer

    @Property(str, notify=changed)
    def epubMode(self):
        return self._epub_mode or ('translated' if self.epubTranslated else 'original')

    @Slot(str, result=bool)
    def setEpubMode(self, mode):
        if self._group_parent:
            return self._group_parent.setEpubMode(mode)
        if mode not in ('original', 'translated', 'bilingual') or not self.epubActive:
            return False
        self.activate()
        if not self.epubTranslated and mode != 'original':
            book = self.owner._epub_task_descriptor(Path(self.epub.path))
            if not book:
                self.owner._error = ui_messages.Message(
                    ui_messages.MessageCode.READING_TRANSLATION_UNAVAILABLE, (),
                    '请先在翻译任务区开始翻译。')
                self.owner.changed.emit()
                return False
            self.owner.library.remember(book['source'], job=book['job'],
                                        progress=(self.owner._progress
                                                  if book['job'] == self.owner._job_path else 0),
                                        enrich=False)
            requested = {**book, 'epub_mode': mode}
            self.owner._submit_book_load(
                self, requested, self.owner._reading_file_for_row(requested),
                purpose='switch')
            return True
        if self.epubTranslated and mode in ('original', 'bilingual') and self._epub_peer is None:
            try:
                job = Path(self.readingJobPath)
                source = (job / json.loads((job / '.epub-source.json').read_text())['source']).resolve()
                if not source.is_relative_to(job.resolve()) or not source.is_file():
                    raise ValueError('原文副本不可用')
                peer_descriptor = {**self._book, 'source': str(source),
                                   'reading_file': str(source), 'epub_mode': 'original'}
                peer = ReaderColumn(self.owner, peer_descriptor,
                                    column_id=self.columnId + '-original',
                                    reader_identity=self._reader_identity,
                                    autoload=False)
                # Both editions belong to one logical book and therefore one
                # book-scoped marginal-note ledger.
                peer._group_parent = self
                peer.setParent(self)
                self._epub_peer = peer
                self.owner._submit_book_load(peer, peer_descriptor, str(source),
                                             purpose='peer')
            except (OSError, ValueError, KeyError, TypeError) as exc:
                self.owner._error = ui_messages.Message(
                    ui_messages.MessageCode.EPUB_OPEN_FAILED, (), str(exc))
                self.owner.changed.emit()
                return False
        self._epub_mode = mode
        self.owner._sync_owner_from_column(self)
        self.changed.emit()
        self.owner._persist_tabs()
        self.owner.shelfChanged.emit()
        self.owner.changed.emit()
        return True

    @Property(str, notify=changed)
    def readingMode(self):  # noqa: N802
        """One reading-mode contract for every flow-layout book.

        EPUB keeps its own renderer/session, but exposes the same three modes to
        QML.  TXT/Markdown use the verified source alignment already owned by
        this column.  Legacy ``readerBilingual`` remains a compatibility alias.
        """
        if self.epubActive:
            return self.epubMode
        if self._reading_mode in {'translated', 'original', 'bilingual'}:
            return self._reading_mode
        legacy = self.owner.settings.value('reader/bilingual', False, type=bool)
        if self._reader_identity:
            legacy = self.owner.settings.value(self._key('reader/bilingual_book/'), legacy, type=bool)
        fallback = 'bilingual' if legacy else 'translated'
        if self._reader_identity:
            value = self.owner.settings.value(self._key('reader/mode_book/'), fallback, type=str)
            return value if value in {'translated', 'original', 'bilingual'} else fallback
        return fallback

    @Slot(str, result=bool)
    def setReadingMode(self, mode):  # noqa: N802
        if self.epubActive:
            return self.setEpubMode(mode)
        if mode not in {'translated', 'original', 'bilingual'}:
            return False
        if not self._reader_identity:
            # Preserve the pre-Reader-Core global bilingual preference even
            # when the library is empty.  There is no source document against
            # which an original-only mode could be meaningful yet.
            if mode == 'original':
                return False
            self._reading_mode = mode
            self.owner.settings.setValue('reader/bilingual', mode == 'bilingual')
            self.changed.emit()
            self.owner.changed.emit()
            return True
        if mode in {'original', 'bilingual'}:
            self._refresh_bilingual()
            if not self.reader.hasSources:
                self.changed.emit()
                self.owner.changed.emit()
                return False
        self._reading_mode = mode
        self.owner.settings.setValue('reader/bilingual', mode == 'bilingual')
        if self._reader_identity:
            self.owner.settings.setValue(self._key('reader/mode_book/'), mode)
            self.owner.settings.setValue(self._key('reader/bilingual_book/'), mode == 'bilingual')
        self.owner._persist_tabs()
        self.changed.emit()
        self.owner.changed.emit()
        return True

    # Use the exact Python QObject subclass at the QML boundary.  Declaring a
    # Python-defined item model merely as QObject makes SIP attempt a lossy
    # down-conversion when this column is nested inside a QVariantList.
    @Property(QObject, constant=True)
    def readerModel(self):  # noqa: N802
        return self.reader

    @Property(str, notify=changed)
    def bookId(self):  # noqa: N802
        return str(self._book.get('id') or '')

    @Property(bool, notify=changed)
    def empty(self):
        return not self.bookId

    @Property(bool, notify=changed)
    def loading(self):
        return self._loading

    @Property(str, notify=changed)
    def loadingTitle(self):  # noqa: N802
        return self._loading_title

    @Property(str, notify=changed)
    def readerTitle(self):  # noqa: N802
        return str(self._book.get('title') or ui_messages.render(
            ui_messages.Message(ui_messages.MessageCode.READER_EMPTY_TITLE)))

    @Property(str, notify=changed)
    def readingJobPath(self):  # noqa: N802
        return str(self._book.get('job') or '')

    @Property(str, notify=changed)
    def readingMachinePath(self):  # noqa: N802
        job = self.readingJobPath
        if not job:
            return ''
        if (self.owner._output_path and self.owner._job_path
                and canonical_path(job) == canonical_path(self.owner._job_path)):
            return self.owner._output_path
        # This property is evaluated while QML builds the first window. Never
        # traverse a protected book directory here: the loader resolves the
        # path in its worker and stores the result on the descriptor.
        return self._machine_path

    @Property(str, notify=changed)
    def readingPath(self):  # noqa: N802
        return self._read_file or self.readingMachinePath

    @Property(bool, notify=changed)
    def readingExternal(self):  # noqa: N802
        return bool(self._book) and not bool(self.readingJobPath)

    def _key(self, prefix):
        return prefix + hashlib.sha256(self._reader_identity.encode()).hexdigest()[:24]

    @Property(int, notify=changed)
    def savedReadingPosition(self):  # noqa: N802
        return int(self.owner.settings.value(self._key('reading/'), 0, type=int)) if self._reader_identity else 0

    @Slot(int)
    def saveReadingPosition(self, row):  # noqa: N802
        if self._reader_identity and row >= 0:
            self.owner.settings.setValue(self._key('reading/'), row)

    @Property(bool, notify=changed)
    def readerBilingual(self):  # noqa: N802
        return self.readingMode == 'bilingual'

    @readerBilingual.setter
    def readerBilingual(self, value):  # noqa: N802
        self.setReadingMode('bilingual' if value else 'translated')

    @Property(str, notify=changed)
    def bilingualMessage(self):  # noqa: N802
        return ui_messages.render_value(self._bilingual_message)

    def _target(self):
        raw = self.readingPath
        if not raw:
            return None
        path = Path(raw).expanduser().resolve()
        if not path.is_file() and str(path).endswith('.partial'):
            path = Path(str(path)[:-8])
        return path if path.is_file() else None

    def _epub_edition_identity(self, target):
        if not self.readingJobPath:
            return None
        target = Path(target).resolve()
        job = Path(self.readingJobPath).resolve()
        source = Path(str(self._book.get('source') or '')).expanduser().resolve()
        return ('original' if target == source or target.is_relative_to(job / '原文')
                else 'translation')

    def _alignment(self):
        # EPUB paragraphs are anchored inside the rendered chapter DOM.  The
        # flow-reader alignment below rebuilds the translated TXT snapshot and
        # is both irrelevant to EPUB bookmarks/notes and expensive enough to
        # stall the GUI when broad ``changed`` notifications re-evaluate QML
        # properties during a shelf switch.  EPUB note painting consumes the
        # persisted source-segment/epub-block anchors directly.
        if self.epubActive:
            return [], []
        target = self._target()
        if not self.readingJobPath or target is None:
            return [], []
        stat = target.stat()
        signature = (self.readingJobPath, str(target), stat.st_mtime_ns, stat.st_size, self.reader.count)
        if signature != self._alignment_signature:
            sys.path.insert(0, str(self.owner.root / 'runtime/tools'))
            import post_edit
            from annotations import bilingual_alignment
            root = Path(self.readingJobPath).resolve()
            self._alignment_data = bilingual_alignment(
                root, target, post_edit.snapshot(root), post_edit.engine, self.reader.rows)
            self._alignment_signature = signature
        return self._alignment_data

    def _refresh_bilingual(self):
        if not self.readingJobPath:
            self.reader.set_sources([])
            self._bilingual_message = ui_messages.Message(
                ui_messages.MessageCode.BILINGUAL_STANDALONE)
            return
        try:
            sources, _ = self._alignment()
            self.reader.set_sources(sources)
            self._bilingual_signature = self._alignment_signature
            self._bilingual_message = ''
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, IndexError):
            self.reader.set_sources([])
            self._bilingual_message = ui_messages.Message(
                ui_messages.MessageCode.BILINGUAL_WAITING)

    def refresh(self):
        target = self._target()
        if target is not None and target.suffix.lower() == '.epub':
            self.epub.open(
                target, cache_identity=self._reader_identity,
                edition_identity=self._epub_edition_identity(target))
            if self.epub.book and Path(self.epub.path) == target:
                self.reader.clear()
            if (self.readingJobPath and self.epub.translation_source_hash
                    and not target.resolve().is_relative_to(Path(self.readingJobPath).resolve() / '原文')):
                first = not self.epubTranslated
                self._epub_translation_path = str(target)
                if first:
                    self.changed.emit()
            return True
        if target is None:
            # A blank column is polled every two seconds. Do not reset its QML
            # view over and over, and retain the last valid page during the tiny
            # interval in which an atomic replacement may be observed.
            # A genuinely different book must never inherit the old book's
            # paragraphs merely because its first output does not exist yet.
            stale_document = (not self._book or (self._reader_identity
                              and self.reader._path != self._reader_identity))
            if stale_document and (self.reader.count or self.reader._path):
                self.reader.clear()
            if self._book:
                self._bilingual_message = ui_messages.Message(
                    ui_messages.MessageCode.BILINGUAL_NO_TRANSLATION
                    if self.readingJobPath else ui_messages.MessageCode.READING_FILE_MOVED)
            return False
        if self.readingJobPath and not self._read_file:
            job = Path(self.readingJobPath)
            original = Path(self._book.get('source') or '')
            try:
                from epub_translation import attach, export
                if not (job / '.epub-source.json').is_file() and original.suffix.lower() == '.epub' and original.is_file():
                    attach(job, original)
                if (job / '.epub-source.json').is_file():
                    companion = target.with_name(target.name.removesuffix('.partial')).with_suffix('.epub')
                    if not companion.exists() or companion.stat().st_mtime_ns < target.stat().st_mtime_ns:
                        sys.path.insert(0, str(self.owner.root / 'runtime/tools'))
                        import post_edit
                        book = post_edit.snapshot(job)
                        if target.name.endswith('.partial'):
                            from task_config import atomic
                            atomic(target.with_name(target.name.removesuffix('.partial')), target.read_text(encoding='utf-8'))
                        from task_config import task_target_language
                        export(job, companion, book['segments'], book['translated'],
                               language_name=task_target_language(job))
                    first = not getattr(self, '_epub_translation_path', '')
                    self._epub_translation_path = str(companion)
                    self.epub.open(
                        companion, cache_identity=self._reader_identity,
                        edition_identity='translation')
                    if self.epub.book:
                        self.reader.clear()
                    if first:
                        self.changed.emit()
                    return True
            except (OSError, ValueError, RuntimeError, KeyError) as exc:
                self.owner._error = ui_messages.Message(
                    ui_messages.MessageCode.EPUB_EXPORT_FAILED, (), str(exc))
                self.owner.changed.emit()
        if self._read_file and self.readingJobPath:
            self.owner.post_editor.syncCurrentReadingFor(
                self.readingJobPath, target, self.readingMachinePath)
        changed = self.reader.load(target, identity=self._reader_identity or str(target))
        if changed:
            self.epub.close()
        if self.readingMode in {'original', 'bilingual'}:
            if changed:
                self._alignment_signature = None
            self._refresh_bilingual()
        return changed

    def _assign_book(self, descriptor, reading_file=None):
        self._dispose_peer()
        self._epub_mode = str(descriptor.get('epub_mode') or '')
        self._epub_translation_path = ''
        self._book = dict(descriptor)
        self._reading_mode = str(self._book.get('reading_mode') or '')
        self._read_file = str((self._book.get('reading_file') if reading_file is None else reading_file) or '')
        self._book['reading_file'] = self._read_file
        self._machine_path = str(self._book.get('machine_path') or '')
        self._manual_empty = False
        self._reader_identity = self._descriptor_reader_identity(self._book)
        self._bilingual_signature = self._alignment_signature = None
        self._alignment_data = ([], [])
        self._bilingual_message = ''
        self._notes_revision += 1
        self._loading = False
        self._loading_title = ''
        self._load_token = ''

    def _dispose_peer(self):
        peer, self._epub_peer = self._epub_peer, None
        if peer is not None:
            peer._group_parent = None
            peer.dispose()
            peer.deleteLater()

    def dispose(self):
        """Release every resource owned by this column on every exit path."""
        self._dispose_peer()
        futures = getattr(self.owner, '_reader_load_futures', {})
        future = futures.pop(self.columnId, None)
        if future is not None:
            future.cancel()
        self._load_token = self._refresh_token = ''
        self._loading = False
        self.epub.close()

    def begin_loading(self, token, title):
        self._load_token = str(token)
        self._refresh_token = ''
        self._loading = True
        self._loading_title = str(title or '图书')
        self.changed.emit()

    def finish_loading(self, token, error=''):
        if str(token) != self._load_token:
            return False
        self._loading = False
        self._loading_title = ''
        self._load_token = ''
        if error:
            self.owner._error = ui_messages.Message(
                ui_messages.MessageCode.READING_UNAVAILABLE, (), str(error)[:300])
            self.owner.changed.emit()
        self.changed.emit()
        return True

    def accept_prepared(self, descriptor, reading_file, payload, token):
        if str(token) != self._load_token:
            return False
        descriptor = dict(descriptor)
        if payload.get('machine_path'):
            descriptor['machine_path'] = str(payload['machine_path'])
        # A restored column already owns this logical book before its worker
        # finishes.  Emitting here would make QML save its not-yet-restored
        # visible row (zero) over the persisted position.  Only a real logical
        # book switch needs to flush the old viewport first.  Editions of one
        # content-addressed book intentionally share a position.
        if self._descriptor_reader_identity(descriptor) != self._reader_identity:
            self.readingAboutToChange.emit()
        self._assign_book(descriptor, reading_file)
        kind = payload.get('kind')
        if kind == 'epub':
            self._epub_translation_path = (str(payload.get('reading_path') or '')
                                           if payload.get('translated') else '')
            self.reader.clear()
            self.epub.accept_prepared(payload['prepared'], defer_same_path=False)
        elif kind == 'text':
            self.epub.close()
            self.reader.apply_prepared(payload['prepared'], identity=self._reader_identity)
            sources = list(payload.get('sources') or [])
            anchors = list(payload.get('alignment_anchors') or [])
            self.reader.set_sources(sources)
            self._alignment_data = (sources, anchors)
            self._alignment_signature = payload.get('alignment_signature')
            self._bilingual_signature = self._alignment_signature
            self._bilingual_message = ui_messages.legacy_message(
                payload.get('bilingual_message') or '')
        else:
            self.epub.close()
            self.reader.clear()
            self._bilingual_message = ui_messages.legacy_message(
                payload.get('message') or '尚无可读内容。')
        self.changed.emit()
        return True

    def set_book(self, descriptor, reading_file=None):
        self.readingAboutToChange.emit()
        self._assign_book(descriptor, reading_file)
        self.refresh()
        self.changed.emit()

    def clear_book(self, *, manual=True):
        self.readingAboutToChange.emit()
        self._dispose_peer()
        self._epub_mode = ''
        self._epub_translation_path = ''
        self._book = {}
        self._reading_mode = ''
        self._manual_empty = bool(manual)
        self._read_file = self._reader_identity = self._bilingual_message = ''
        self._machine_path = ''
        self._bilingual_signature = self._alignment_signature = None
        self._alignment_data = ([], [])
        self._notes_revision += 1
        self._loading = False
        self._loading_title = ''
        self._load_token = ''
        self.epub.close()
        self.reader.clear()
        self.changed.emit()

    def serialize(self):
        return {'column_id': self._column_id, **self._book, 'reading_file': self._read_file,
                'machine_path': self._machine_path,
                'manual_empty': self._manual_empty, 'epub_mode': self.epubMode,
                'reading_mode': self.readingMode}

    @Slot()
    def activate(self):
        self.owner.activateReadingColumn(self._group_parent.columnId if self._group_parent else self._column_id)

    @Slot(str)
    def openShelfBook(self, identity):  # noqa: N802
        self.owner.openBookInColumn(self._column_id, identity)

    @Slot(str)
    def openShelfBookAsync(self, identity):  # noqa: N802
        self.owner.openBookInColumnAsync(self._column_id, identity)

    @Slot()
    def pickReadingFile(self):  # noqa: N802
        directory = str(Path(self.readingPath).parent) if self.readingPath else str(Path.home() / 'Downloads')
        path, _ = choose_open_file(
            None,
            ui_messages.render(ui_messages.Message(ui_messages.MessageCode.DIALOG_OPEN_BOOK)),
            directory,
            ui_messages.render(ui_messages.Message(ui_messages.MessageCode.DIALOG_READING_FILTER)))
        if path:
            try:
                selected = self.owner.file_access.selected(path, read_only=True)
                self.owner.importShelfBookInColumnAsync(self._column_id, str(selected))
            except OSError as exc:
                self.owner._error = ui_messages.message_from_worker_error(exc)
                self.owner.changed.emit()

    @Slot()
    def followTranslation(self):  # noqa: N802
        self.owner.followTranslationInColumnAsync(self._column_id)

    def _bookmark_rows(self):
        return read_list(self.owner.settings, self._key('reader/bookmarks_v1/')) if self._reader_identity else []

    def _bookmark_position(self, mark, anchors):
        if mark.get('segment') and anchors:
            matches = [(i, anchor) for i, anchor in enumerate(anchors) if anchor['segment'] == mark['segment']]
            if matches:
                return min(matches, key=lambda pair: abs(pair[1]['offset'] - mark.get('offset', 0)))[0]
            return -1
        matches = [i for i, text in enumerate(self.reader.rows) if text == mark.get('text')]
        return min(matches, key=lambda i: abs(i - mark.get('row', 0))) if matches else -1

    @Property('QVariantList', notify=changed)
    def bookmarks(self):
        marks = self._bookmark_rows()
        try:
            _, anchors = self._alignment()
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, IndexError):
            anchors = []
        projected = []
        for mark in marks:
            position = self._bookmark_position(mark, anchors)
            row = dict(mark, position=position)
            if mark.get('generated_title') is True:
                paragraph = position if position >= 0 else int(mark.get('row', 0) or 0)
                row['title'] = ui_messages.render(ui_messages.Message(
                    ui_messages.MessageCode.BOOKMARK_PARAGRAPH,
                    (paragraph + 1, str(mark.get('title') or mark.get('text') or ''))))
            projected.append(row)
        return projected

    @Slot(int, str)
    def addBookmark(self, row, title):  # noqa: N802
        if not self._reader_identity or not 0 <= row < self.reader.count:
            return
        try:
            _, anchors = self._alignment()
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, IndexError):
            anchors = []
        marks = self._bookmark_rows()
        for mark in marks:
            if self._bookmark_position(mark, anchors) == row:
                if title.strip():
                    mark['title'] = title.strip()[:100]
                    mark['generated_title'] = False
                    self.owner.settings.setValue(self._key('reader/bookmarks_v1/'), json.dumps(marks, ensure_ascii=False))
                    self.changed.emit()
                return
        text = self.reader.rows[row]
        # A user-written title is content and is stored verbatim.  Only the
        # generated fallback is marked, so the display layer can localize it
        # without ever rewriting what the reader typed.
        typed = title.strip()[:100]
        mark = {'id': uuid.uuid4().hex, 'row': row, 'text': text,
                'title': typed or text[:36],
                'generated_title': not typed,
                'created': datetime.now().isoformat(timespec='minutes')}
        if row < len(anchors):
            mark.update(anchors[row])
        marks.append(mark)
        self.owner.settings.setValue(self._key('reader/bookmarks_v1/'), json.dumps(marks, ensure_ascii=False))
        self.changed.emit()

    @Slot(str)
    def removeBookmark(self, identity):  # noqa: N802
        marks = [mark for mark in self._bookmark_rows() if mark.get('id') != identity]
        self.owner.settings.setValue(self._key('reader/bookmarks_v1/'), json.dumps(marks, ensure_ascii=False))
        self.changed.emit()

    def _document_id(self):
        return note_document_id(self._reader_identity)

    def _note_key(self):
        return self._key('reader/paragraph_notes_v1/')

    def _note_rows(self):
        """Return only notes whose embedded document id matches this book."""
        document_id = self._document_id()
        if not document_id:
            return []
        return [row for row in read_list(self.owner.settings, self._note_key())
                if row.get('document_id') == document_id and isinstance(row.get('anchor'), dict)]

    def _save_notes(self, rows):
        if not self._reader_identity:
            return
        self.owner.settings.setValue(self._note_key(), json.dumps(rows, ensure_ascii=False))
        self.owner.settings.sync()
        self._notes_revision += 1
        self.changed.emit()
        self.owner.changed.emit()

    def _row_anchor(self, row):
        if not 0 <= row < self.reader.count:
            return None
        try:
            _, anchors = self._alignment()
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, IndexError):
            anchors = []
        if row < len(anchors):
            return {'kind': 'source-segment', **anchors[row]}
        text = self.reader.rows[row]
        occurrence = sum(1 for value in self.reader.rows[:row] if value == text)
        return {'kind': 'text', 'text': text, 'row': row, 'occurrence': occurrence}

    def _note_position(self, note, anchors=None):
        anchor = note.get('anchor') or {}
        if anchor.get('kind') == 'source-segment' and anchor.get('segment') and anchors:
            try:
                segment = int(anchor.get('segment'))
                offset = int(anchor.get('offset', 0) or 0)
            except (TypeError, ValueError, OverflowError):
                return -1
            matches = [(i, value) for i, value in enumerate(anchors)
                       if value.get('segment') == segment]
            if matches:
                return min(matches, key=lambda pair: abs(pair[1].get('offset', 0) - offset))[0]
            return -1
        if anchor.get('kind') == 'text':
            matches = [i for i, text in enumerate(self.reader.rows) if text == anchor.get('text')]
            try:
                occurrence = int(anchor.get('occurrence', 0) or 0)
                old_row = int(anchor.get('row', 0) or 0)
            except (TypeError, ValueError, OverflowError):
                return -1
            if 0 <= occurrence < len(matches):
                return matches[occurrence]
            return min(matches, key=lambda i: abs(i - old_row)) if matches else -1
        return -1

    @Property(int, notify=changed)
    def notesRevision(self):  # noqa: N802
        return self._notes_revision

    @Property('QVariantList', notify=changed)
    def paragraphNotes(self):  # noqa: N802
        rows = self._note_rows()
        try:
            _, anchors = self._alignment()
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, IndexError):
            anchors = []
        return [{**row, 'position': self._note_position(row, anchors)} for row in rows]

    @Slot(int, result='QVariantMap')
    def paragraphNoteAt(self, row):  # noqa: N802
        return next((note for note in self.paragraphNotes if note.get('position') == row), {})

    @Slot(str, result='QVariantMap')
    def paragraphNoteById(self, identity):  # noqa: N802
        return next((note for note in self._note_rows() if note.get('id') == identity), {})

    @Slot('QVariantMap', result='QVariantMap')
    def paragraphNoteForEpubAnchor(self, raw_anchor):  # noqa: N802
        signature = self._epub_anchor_signature(dict(raw_anchor or {}))
        if signature is None:
            return {}
        return next((note for note in self._note_rows()
                     if self._epub_anchor_signature(note.get('anchor') or {}) == signature), {})

    @Slot(int, str, str, result=str)
    def saveParagraphNote(self, row, color, content):  # noqa: N802
        anchor = self._row_anchor(row)
        if anchor is None:
            return ''
        rows = self._note_rows()
        existing = next((note for note in self.paragraphNotes if note.get('position') == row), None)
        now = datetime.now().isoformat(timespec='minutes')
        if existing:
            identity = existing['id']
            note = next(note for note in rows if note.get('id') == identity)
            note.update(anchor=anchor, color=color if color in NOTE_COLORS else 'yellow',
                        content=str(content), excerpt=self.reader.rows[row][:240], updated=now)
        else:
            identity = uuid.uuid4().hex
            rows.append({'id': identity, 'document_id': self._document_id(), 'type': 'note',
                         'anchor': anchor, 'edition': 'translation',
                         'color': color if color in NOTE_COLORS else 'yellow',
                         'content': str(content), 'excerpt': self.reader.rows[row][:240],
                         'created': now, 'updated': now})
        self._save_notes(rows)
        return identity

    @staticmethod
    def _epub_anchor_signature(anchor):
        """Return a comparable anchor without trusting persisted settings.

        Notes live in QSettings and can outlast several application versions.
        A malformed old row must be ignored rather than making every note in
        the current book inaccessible.
        """
        return stable_note_anchor(anchor)

    @Slot('QVariantMap', str, str, result=str)
    def saveEpubParagraphNote(self, raw_anchor, color, content):  # noqa: N802
        allowed = {'kind', 'segment', 'offset', 'segments', 'source', 'chapter', 'block', 'edition'}
        anchor = {str(key): value for key, value in dict(raw_anchor or {}).items() if key in allowed}
        if not self._reader_identity or anchor.get('kind') not in {'source-segment', 'epub-block'}:
            return ''
        try:
            anchor['block'] = int(anchor.get('block', -1))
            if anchor.get('segment') is not None:
                anchor['segment'] = int(anchor['segment'])
            if anchor.get('offset') is not None:
                anchor['offset'] = int(anchor['offset'])
        except (TypeError, ValueError):
            return ''
        signature = self._epub_anchor_signature(anchor)
        if signature is None:
            return ''
        rows = self._note_rows()
        note = next((row for row in rows
                     if self._epub_anchor_signature(row.get('anchor') or {}) == signature), None)
        now = datetime.now().isoformat(timespec='minutes')
        excerpt = str(anchor.get('source') or '')[:240]
        if note:
            note.update(anchor=anchor, color=color if color in NOTE_COLORS else 'yellow',
                        content=str(content), excerpt=excerpt, updated=now)
        else:
            note = {'id': uuid.uuid4().hex, 'document_id': self._document_id(), 'type': 'note',
                    'anchor': anchor, 'edition': str(anchor.get('edition') or 'translation'),
                    'color': color if color in NOTE_COLORS else 'yellow',
                    'content': str(content), 'excerpt': excerpt,
                    'created': now, 'updated': now}
            rows.append(note)
        self._save_notes(rows)
        return note['id']

    @Slot(str)
    def removeParagraphNote(self, identity):  # noqa: N802
        rows = [row for row in self._note_rows() if row.get('id') != identity]
        self._save_notes(rows)


class ReadingSessions(QObject):
    """Controller surface preserving the 1.6 API while adding real columns."""
    changed = Signal()
    shelfChanged = Signal()
    columnsChanged = Signal()

    @staticmethod
    def _cache_size_text(size):
        value = float(max(0, int(size or 0)))
        for unit in ('B', 'KB', 'MB', 'GB'):
            if value < 1024 or unit == 'GB':
                return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
            value /= 1024

    def _cache_message(self):
        """The cache state as a stable message, rendered on read.

        Building the message here (and rendering in the getter) is what lets an
        already-computed summary re-render after a locale switch without
        touching the cache or recomputing statistics.
        """
        if getattr(self, '_reader_cache_busy', False):
            return ui_messages.Message(
                ui_messages.MessageCode.CACHE_COUNTING
                if getattr(self, '_reader_cache_operation', '') == 'stats'
                else ui_messages.MessageCode.CACHE_CLEARING)
        message = getattr(self, '_reader_cache_message', '')
        if message:
            return message if isinstance(message, ui_messages.Message) else \
                ui_messages.legacy_message(message)
        stats = getattr(self, '_reader_cache_stats', None)
        if stats is None:
            return ui_messages.Message(ui_messages.MessageCode.CACHE_NOT_COUNTED)
        size = self._cache_size_text(stats.get('physical_bytes', stats['bytes']))
        managed = stats.get('managed', stats['books'])
        active = stats.get('active') or 0
        unknown = stats.get('unknown') or 0
        if active and unknown:
            return ui_messages.Message(
                ui_messages.MessageCode.CACHE_SUMMARY_ACTIVE_UNKNOWN,
                (size, managed, active, unknown))
        if active:
            return ui_messages.Message(ui_messages.MessageCode.CACHE_SUMMARY_ACTIVE,
                                       (size, managed, active))
        if unknown:
            return ui_messages.Message(ui_messages.MessageCode.CACHE_SUMMARY_UNKNOWN,
                                       (size, managed, unknown))
        return ui_messages.Message(ui_messages.MessageCode.CACHE_SUMMARY, (size, managed))

    @Property('QVariantMap', notify=changed)
    def readerCacheState(self):  # noqa: N802
        message = self._cache_message()
        return {'code': message.code, 'args': list(message.args),
                'detail': message.detail, 'text': ui_messages.render(message)}

    @Property(str, notify=changed)
    def readerCacheSummary(self):  # noqa: N802
        return ui_messages.render(self._cache_message())

    @Property(str, notify=changed)
    def readerCacheDetail(self):  # noqa: N802
        """Untranslated cache diagnostic, separate from the localized state."""
        return ui_messages.detail_text(self._cache_message())

    @Property(bool, notify=changed)
    def readerCacheBusy(self):  # noqa: N802
        return bool(getattr(self, '_reader_cache_busy', False))

    @Slot()
    def refreshReaderCacheStats(self):  # noqa: N802
        identities = tuple(str(row.get('job') or row.get('source') or '')
                           for row in self.library.rows)
        self._submit_cache_job(
            'stats', partial(cache_stats, self.storage_context.cache_root,
                             known_identities=identities))

    @Slot()
    def clearReaderCache(self):  # noqa: N802
        self._submit_cache_job(
            'clear', partial(clear_epub_cache, self.storage_context.cache_root))

    def _submit_cache_job(self, operation, work):
        if getattr(self, '_reader_cache_busy', False):
            return False
        self._ensure_reader_loader()
        self._reader_cache_busy = True
        self._reader_cache_operation = operation
        self._reader_cache_message = ''
        self.changed.emit()
        future = self._reader_executor.submit(work)
        self._reader_load_futures['__cache__'] = future
        signals = self._reader_loader_signals

        def deliver(completed):
            if self._reader_loader_stopping:
                try:
                    payload = completed.result()
                    if isinstance(payload, dict) and payload.get('kind') == 'epub':
                        release_prepared(payload.get('prepared'))
                except Exception:
                    pass
                return
            try:
                result = dict(ok=True, purpose='cache-' + operation,
                              stats=completed.result())
            except CancelledError:
                return
            except Exception as exc:
                result = dict(ok=False, purpose='cache-' + operation, error=str(exc))
            signals.ready.emit(result)

        future.add_done_callback(deliver)
        return True

    def _ensure_reader_loader(self):
        if getattr(self, '_reader_executor', None) is not None:
            return
        self._reader_loader_stopping = False
        self._reader_load_futures = {}
        self._reader_loader_signals = ReaderLoadSignals(self)
        self._reader_loader_signals.ready.connect(
            self._complete_reader_load, Qt.ConnectionType.QueuedConnection)
        self._reader_executor = ThreadPoolExecutor(max_workers=1,
                                                   thread_name_prefix='whale-reader')

    def _submit_shelf_metadata(self, row):
        row = dict(row or {})
        identity = str(row.get('id') or '')
        source = Path(str(row.get('source') or '')).expanduser().resolve()
        if not identity or source.suffix.lower() != '.epub':
            return False
        cover_path = str(row.get('cover_path') or '')
        self._ensure_reader_loader()
        key = '__metadata__:' + identity
        if key in self._reader_load_futures:
            return False

        def inspect():
            before = source.stat()
            current = [before.st_dev, before.st_ino, before.st_size,
                       before.st_mtime_ns, before.st_ctime_ns]
            if (row.get('metadata_signature') == current and row.get('title')
                    and (not cover_path or Path(cover_path).is_file())):
                return None
            metadata = inspect_epub(
                source, thumbnail_root=self.storage_context.thumbnail_root)
            after = source.stat()
            before_key = (before.st_dev, before.st_ino, before.st_size,
                          before.st_mtime_ns, before.st_ctime_ns)
            after_key = (after.st_dev, after.st_ino, after.st_size,
                         after.st_mtime_ns, after.st_ctime_ns)
            if before_key != after_key:
                raise OSError('EPUB 正在替换，稍后再扫描封面')
            return metadata, list(after_key)

        future = self._reader_executor.submit(inspect)
        self._reader_load_futures[key] = future
        signals = self._reader_loader_signals

        def deliver(completed):
            if self._reader_loader_stopping:
                return
            try:
                payload = completed.result()
                if payload is None:
                    result = dict(ok=True, unchanged=True, purpose='metadata',
                                  key=key, identity=identity, source=str(source))
                else:
                    metadata, signature = payload
                    result = dict(ok=True, purpose='metadata', key=key,
                                  identity=identity, source=str(source),
                                  metadata=metadata, signature=signature)
            except CancelledError:
                return
            except Exception:
                result = dict(ok=False, purpose='metadata', key=key,
                              identity=identity, source=str(source))
            signals.ready.emit(result)

        future.add_done_callback(deliver)
        return True

    def _submit_source_inspection(self, source):
        """Queue optional source statistics without delaying source selection."""
        source = Path(source).expanduser().resolve()
        self._ensure_reader_loader()
        key = '__source-inspect__'
        previous = self._reader_load_futures.get(key)
        if previous is not None:
            previous.cancel()
        token = uuid.uuid4().hex
        self._source_inspection_token = token
        future = self._reader_executor.submit(inspect_source, source)
        self._reader_load_futures[key] = future
        signals = self._reader_loader_signals

        def deliver(completed):
            if self._reader_loader_stopping:
                return
            try:
                payload = completed.result()
                result = dict(ok=True, purpose='source-inspect', key=key,
                              token=token, payload=payload)
            except CancelledError:
                return
            except Exception as exc:
                result = dict(ok=False, purpose='source-inspect', key=key,
                              token=token, source=str(source), error=str(exc))
            signals.ready.emit(result)

        future.add_done_callback(deliver)
        return True

    def _refresh_shelf_metadata(self):
        for row in list(self.library.rows):
            self._submit_shelf_metadata(row)

    def _submit_epub_publication(self, task_root):
        root = Path(str(task_root or '')).expanduser().resolve()
        if not (root / '.epub-source.json').is_file():
            return False
        self._ensure_reader_loader()
        key = '__publication__:' + hashlib.sha256(str(root).encode()).hexdigest()[:16]
        if key in self._reader_load_futures:
            return False
        future = self._reader_executor.submit(publish_epub_checkpoint, self.root, root)
        self._reader_load_futures[key] = future
        signals = self._reader_loader_signals

        def deliver(completed):
            if self._reader_loader_stopping:
                return
            try:
                payload = completed.result()
                result = dict(ok=True, purpose='publication', key=key,
                              task_root=str(root), payload=payload)
            except CancelledError:
                return
            except Exception as exc:
                result = dict(ok=False, purpose='publication', key=key,
                              task_root=str(root), error=str(exc))
            signals.ready.emit(result)

        future.add_done_callback(deliver)
        return True

    def _shutdown_reader_loader(self):
        self._reader_loader_stopping = True
        for future in getattr(self, '_reader_load_futures', {}).values():
            future.cancel()
        executor = getattr(self, '_reader_executor', None)
        if executor is not None:
            # Local book preparation is bounded and holds explicit cache
            # leases. Orderly shutdown waits for the current filesystem unit
            # so no worker outlives its settings/cache sandbox.
            executor.shutdown(wait=True, cancel_futures=True)
        self._reader_executor = None
        self._reader_load_futures = {}
        for column in getattr(self, '_reading_columns', []):
            column.dispose()
        from epub_reader import drain_cache_cleanup
        drain_cache_cleanup()

    def _machine_path_for_book(self, book):
        job = str(book.get('job') or '')
        if not job:
            return ''
        if (self._output_path and self._job_path
                and Path(job).resolve() == Path(self._job_path).resolve()):
            return self._output_path
        try:
            root = Path(job).resolve()
            config = yaml.safe_load((root / '翻译任务.yaml').read_text(encoding='utf-8'))
            return self._managed_output(root, config)
        except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError):
            return ''

    def _submit_book_load(self, column, book, reading_file, *, purpose='switch',
                          request_signature=None, allow_epub_export=None):
        worker_book = dict(book)
        try:
            for field in ('job', 'retained_source', 'source'):
                if worker_book.get(field):
                    try:
                        worker_book[field] = str(self.file_access.ensure(worker_book[field]))
                    except OSError:
                        if field != 'source' or not worker_book.get('retained_source'):
                            raise
            if reading_file:
                reading_file = str(self.file_access.ensure(reading_file))
        except OSError as exc:
            self._error = ui_messages.message_from_worker_error(exc)
            self.changed.emit()
            return
        self._ensure_reader_loader()
        token = uuid.uuid4().hex
        column_id = column.columnId
        previous = self._reader_load_futures.get(column_id)
        if previous is not None:
            previous.cancel()
        if purpose != 'refresh':
            column.begin_loading(token, book.get('title'))
        else:
            column._refresh_token = token
            column._refresh_signature = request_signature
        worker_book['_requested_reading_mode'] = column.readingMode
        worker_book['_allow_epub_export'] = (
            purpose != 'refresh' if allow_epub_export is None
            else bool(allow_epub_export)
        )
        def prepare():
            machine_path = self._machine_path_for_book(worker_book)
            payload = prepare_book(
                self.root, worker_book, str(reading_file or ''), machine_path,
                self.storage_context)
            payload['machine_path'] = str(machine_path or '')
            return payload

        future = self._reader_executor.submit(prepare)
        self._reader_load_futures[column_id] = future
        signals = self._reader_loader_signals

        def deliver(completed):
            if self._reader_loader_stopping:
                try:
                    payload = completed.result()
                    if isinstance(payload, dict) and payload.get('kind') == 'epub':
                        release_prepared(payload.get('prepared'))
                except Exception:
                    pass
                return
            try:
                payload = completed.result()
                result = dict(ok=True, column=column_id, token=token, purpose=purpose,
                              book=dict(book), reading_file=str(reading_file or ''),
                              payload=payload)
            except CancelledError:
                return
            except Exception as exc:  # Worker errors are rendered on the GUI thread.
                result = dict(ok=False, column=column_id, token=token, purpose=purpose,
                              error=str(exc))
            signals.ready.emit(result)

        future.add_done_callback(deliver)

    @Slot(object)
    def _complete_reader_load(self, result):
        if result.get('purpose') == 'shelf-import-hash':
            column = next((row for row in self._reading_columns
                           if row.columnId == result.get('column')), None)
            if column is None or column._load_token != result.get('token'):
                return
            self._reader_load_futures.pop(column.columnId, None)
            if not result.get('ok'):
                column.finish_loading(
                    result.get('token'),
                    '添加图书失败：' + str(result.get('error') or '无法读取文件'))
                return
            try:
                path = Path(result['path']).resolve()
                identity = self.library.remember(
                    path, reading_file=str(path), enrich=False,
                    content_digest=result['digest'])
                source_book = next(
                    dict(row) for row in self.library.rows if row['id'] == identity)
                book, reading_file = self._translated_book_for_shelf_import(source_book)
                if book is None:
                    book, reading_file = source_book, str(path)
            except (OSError, ValueError, KeyError, TypeError, StopIteration) as exc:
                column.finish_loading(
                    result.get('token'), '添加图书失败：' + str(exc))
                return
            self._submit_book_load(column, book, reading_file, purpose='path')
            return
        if result.get('purpose') == 'source-inspect':
            if result.get('token') != getattr(self, '_source_inspection_token', ''):
                return
            self._reader_load_futures.pop(result.get('key', ''), None)
            payload = result.get('payload') or {}
            source = str(payload.get('path') or result.get('source') or '')
            if source != getattr(self, '_source_path', ''):
                return
            if result.get('ok'):
                payload_detection = payload.get('detection') or {}
                if hasattr(self, '_apply_detected_language'):
                    self._apply_detected_language(payload_detection)
                else:
                    target = getattr(self, '_target_language', '简体中文')
                    detected = payload.get('language') or ui_messages.render(
                        ui_messages.Message(ui_messages.MessageCode.LANGUAGE_PENDING))
                    self._language_label = ui_messages.Message(
                        ui_messages.MessageCode.LANGUAGE_DIRECTION, (detected, target))
                self._source_info = ui_messages.Message(
                    ui_messages.MessageCode.SOURCE_INSPECTED,
                    (self._cache_size_text(payload['size']), f"{payload['characters']:,}"))
            else:
                self._error = ui_messages.message_from_worker_error(
                    str(result.get('error') or '')[:500])
            self.changed.emit()
            return
        if result.get('purpose') == 'publication':
            self._reader_load_futures.pop(result.get('key', ''), None)
            if result.get('ok'):
                if (result.get('task_root') == getattr(self, '_job_path', '')
                        and getattr(self, '_pausing', False)):
                    self._status_detail = ui_messages.Message(
                        ui_messages.MessageCode.PAUSE_EPUB_PUBLISHED)
                self._refresh_readers()
            else:
                self._error = ui_messages.Message(
                    ui_messages.MessageCode.EPUB_EXPORT_FAILED, (),
                    str(result.get('error') or '')[:500])
            self.changed.emit()
            return
        if result.get('purpose') == 'metadata':
            self._reader_load_futures.pop(result.get('key', ''), None)
            if not result.get('ok') or result.get('unchanged'):
                return
            row = next((item for item in self.library.rows
                        if item.get('id') == result.get('identity')
                        and str(Path(str(item.get('source') or '')).resolve())
                        == result.get('source')), None)
            if row is None:
                return
            metadata = result.get('metadata') or {}
            row.update(
                title=str(metadata.get('title') or row.get('title') or ''),
                author=str(metadata.get('author') or ''),
                cover_path=str(metadata.get('cover') or ''),
                metadata_signature=list(result.get('signature') or []))
            row['cover'] = (QUrl.fromLocalFile(row['cover_path']).toString()
                            if row.get('cover_path') else '')
            self.library.save()
            self.shelfChanged.emit()
            self.changed.emit()
            return
        if result.get('purpose') in {'cache-clear', 'cache-stats'}:
            self._reader_load_futures.pop('__cache__', None)
            self._reader_cache_busy = False
            self._reader_cache_operation = ''
            if result.get('ok'):
                stats = result['stats']
                self._reader_cache_stats = dict(stats)
                if result['purpose'] == 'cache-clear':
                    freed = stats.get('physical_freed', stats['freed'])
                    remaining = stats.get('physical_bytes', stats['remaining'])
                    self._reader_cache_freed = self._cache_size_text(freed)
                    self._reader_cache_remaining = (
                        self._cache_size_text(remaining) if remaining else '')
                    self._reader_cache_message = ui_messages.Message(
                        ui_messages.MessageCode.CACHE_CLEARED
                        if self._reader_cache_remaining else
                        ui_messages.MessageCode.CACHE_CLEARED_EMPTY,
                        (self._reader_cache_freed,) if not self._reader_cache_remaining
                        else (self._reader_cache_freed, self._reader_cache_remaining))
                else:
                    self._reader_cache_message = ''
            else:
                # Raw diagnostic kept verbatim; the category is the failure code.
                self._reader_cache_message = ui_messages.message_from_worker_error(
                    str(result.get('error') or ''),
                    ui_messages.MessageCode.CACHE_CLEAR_FAILED
                    if result['purpose'] == 'cache-clear'
                    else ui_messages.MessageCode.CACHE_COUNT_FAILED)
            self.changed.emit()
            return
        views = [view for row in self._reading_columns
                 for view in (row, row._epub_peer) if view is not None]
        column = next((row for row in views
                       if row.columnId == result.get('column')), None)
        purpose = result.get('purpose', 'switch')
        expected = column._refresh_token if column is not None and purpose == 'refresh' else (
            column._load_token if column is not None else '')
        if column is None or expected != result.get('token'):
            payload = result.get('payload') or {}
            if payload.get('kind') == 'epub':
                release_prepared(payload.get('prepared'))
            return
        self._reader_load_futures.pop(column.columnId, None)
        if purpose == 'refresh':
            column._refresh_token = ''
            if not result.get('ok'):
                column._refresh_signature = None
                return
            payload = result['payload']
            column._machine_path = str(payload.get('machine_path') or column._machine_path)
            if column.bookId != result['book'].get('id'):
                if payload.get('kind') == 'epub':
                    release_prepared(payload.get('prepared'))
                return
            if payload.get('kind') == 'epub':
                column._epub_translation_path = (str(payload.get('reading_path') or '')
                                                   if payload.get('translated') else '')
                column.reader.clear()
                column.epub.accept_prepared(payload['prepared'])
            elif payload.get('kind') == 'text':
                column.epub.close()
                column.reader.apply_prepared(
                    payload['prepared'], identity=column._reader_identity)
                sources = list(payload.get('sources') or [])
                anchors = list(payload.get('alignment_anchors') or [])
                column.reader.set_sources(sources)
                column._alignment_data = (sources, anchors)
                column._alignment_signature = payload.get('alignment_signature')
                column._bilingual_signature = column._alignment_signature
                column._bilingual_message = ui_messages.legacy_message(
                    payload.get('bilingual_message') or '')
            else:
                return
            column.changed.emit()
            self.changed.emit()
            return
        if not result.get('ok'):
            column.finish_loading(result.get('token'), '打开图书失败：' + result.get('error', ''))
            return
        metadata = result.get('payload', {}).get('shelf_metadata') or {}
        shelf_row = next((row for row in self.library.rows
                          if row.get('id') == result['book'].get('id')), None)
        if shelf_row is not None and metadata:
            source = Path(str(shelf_row.get('source') or ''))
            try:
                stat = source.stat()
                signature = [stat.st_dev, stat.st_ino, stat.st_size,
                             stat.st_mtime_ns, stat.st_ctime_ns]
            except OSError:
                signature = []
            shelf_row.update(
                title=str(metadata.get('title') or shelf_row.get('title') or ''),
                author=str(metadata.get('author') or ''),
                cover_path=str(metadata.get('cover') or ''),
                metadata_signature=signature)
            if shelf_row.get('cover_path'):
                shelf_row['cover'] = QUrl.fromLocalFile(shelf_row['cover_path']).toString()
            self.library.save()
            result['book'].update(shelf_row)
        requested_epub_mode = str(result['book'].get('epub_mode') or '')
        if not column.accept_prepared(result['book'], result['reading_file'],
                                      result['payload'], result['token']):
            payload = result.get('payload') or {}
            if payload.get('kind') == 'epub':
                release_prepared(payload.get('prepared'))
            return
        if (column.epubTranslated and requested_epub_mode in {'original', 'bilingual'}
                and column._epub_peer is None):
            column.setEpubMode(requested_epub_mode)
        self._reading_manual_empty = False
        self._sync_owner_from_column(column._group_parent or column)
        self._persist_tabs()
        self.shelfChanged.emit()
        self.changed.emit()
        self.shelfBookOpened.emit()
        if purpose == 'path':
            self.readingFileOpened.emit()

    def _ensure_column(self, *, reader=None):
        if not getattr(self, '_reading_columns', None):
            column = ReaderColumn(self, column_id=uuid.uuid4().hex, reader=reader)
            self._reading_columns = [column]
            self._active_column_id = column.columnId
        return self._active_column()

    def _managed_reading_job(self, raw_path):
        path = Path(raw_path).resolve()
        if self._job_path:
            current = Path(self._job_path).resolve()
            if path.is_relative_to(current):
                return '' if path.is_relative_to(current / '原文') else str(current)
        for parent in path.parents:
            if parent.parent in self._task_roots() and (parent / '翻译任务.yaml').is_file():
                return '' if path.is_relative_to(parent / '原文') else str(parent)
        return ''

    def _active_column(self):
        columns = getattr(self, '_reading_columns', [])
        return next((column for column in columns if column.columnId == self._active_column_id),
                    columns[0] if columns else None)

    def _sync_owner_from_column(self, column=None):
        column = column or self._active_column()
        if not column:
            return
        self._active_column_id = column.columnId
        self._active_book = column.bookId
        self._read_file = column._read_file
        self._reader_identity = column._reader_identity
        self.reader = column.reader

    def appendAskAnswerToParagraphNote(self, origin, question, answer, entry_id,
                                       answer_locale=''):  # noqa: N802
        """Append one completed AI turn to its frozen book/paragraph origin.

        The active column is deliberately irrelevant here: the reader may
        have switched from book A to book B while the answer was pending. The
        answer's frozen locale is metadata on the existing note record; the
        prose is stored once and is never rewritten during an interface switch.
        """
        origin = dict(origin or {})
        reader_identity = str(origin.get('reader_identity') or '')
        document_id = note_document_id(reader_identity)
        if not reader_identity or origin.get('document_id') != document_id:
            return {'ok': False, 'message': '原书身份已经失效，未写入随笔。'}
        raw_anchor = origin.get('anchor') or {}
        allowed = {'kind', 'segment', 'offset', 'segments', 'source', 'chapter', 'block', 'edition'}
        anchor = {str(key): value for key, value in dict(raw_anchor).items() if key in allowed}
        signature = stable_note_anchor(anchor)
        question, answer, entry_id = str(question).strip(), str(answer).strip(), str(entry_id).strip()
        raw_locale = str(answer_locale or '').strip().casefold().replace('_', '-')
        saved_locale = ('zh-CN' if raw_locale == 'zh' or raw_locale.startswith('zh-') else
                        'en' if raw_locale == 'en' or raw_locale.startswith('en-') else '')
        if signature is None or not question or not answer or not entry_id:
            return {'ok': False, 'message': '这次问答没有稳定的段落锚点，未写入随笔。'}

        key = 'reader/paragraph_notes_v1/' + document_id
        rows = [row for row in read_list(self.settings, key)
                if row.get('document_id') == document_id and isinstance(row.get('anchor'), dict)]
        note = next((row for row in rows if stable_note_anchor(row.get('anchor')) == signature), None)
        if note and entry_id in [str(value) for value in note.get('ask_entries', [])]:
            return {'ok': True, 'id': note.get('id', ''), 'already': True,
                    'message': '这轮问答已经在本段随笔里。'}

        addition = f'问：{question}\nAI 回答：{answer}'
        previous = str((note or {}).get('content') or '').rstrip()
        combined = previous + ('\n\n' if previous else '') + addition
        now = datetime.now().isoformat(timespec='minutes')
        if note:
            note['content'] = combined
            note['updated'] = now
            note['ask_entries'] = ([str(value) for value in note.get('ask_entries', [])]
                                   + [entry_id])[-50:]
            saved_rows = note.get('ask_entry_locales')
            locale_rows = dict(saved_rows) if isinstance(saved_rows, dict) else {}
            if saved_locale:
                locale_rows[entry_id] = saved_locale
            kept = set(note['ask_entries'])
            locale_rows = {key: value for key, value in locale_rows.items() if key in kept}
            if locale_rows:
                note['ask_entry_locales'] = locale_rows
        else:
            note = {
                'id': uuid.uuid4().hex,
                'document_id': document_id,
                'type': 'note',
                'anchor': anchor,
                'edition': str(anchor.get('edition') or origin.get('edition') or 'translation'),
                'color': 'yellow',
                'content': combined,
                'excerpt': str(origin.get('excerpt') or anchor.get('source') or '')[:240],
                'ask_entries': [entry_id],
                'created': now,
                'updated': now,
            }
            if saved_locale:
                note['ask_entry_locales'] = {entry_id: saved_locale}
            rows.append(note)
        self.settings.setValue(key, json.dumps(rows, ensure_ascii=False))
        self.settings.sync()

        notified = set()
        for column in getattr(self, '_reading_columns', []):
            for candidate in (column, column._epub_peer):
                if (candidate and candidate._reader_identity == reader_identity
                        and id(candidate) not in notified):
                    notified.add(id(candidate))
                    candidate._notes_revision += 1
                    candidate.changed.emit()
        self.changed.emit()
        title = str(origin.get('title') or '原书')
        return {'ok': True, 'id': note['id'], 'already': False,
                'message': f'已追加到《{title}》的本段随笔。'}

    @Property('QVariantList', notify=columnsChanged)
    def readingColumns(self):  # noqa: N802
        return list(getattr(self, '_reading_columns', []))

    @Property(str, notify=changed)
    def activeReadingColumnId(self):  # noqa: N802
        return getattr(self, '_active_column_id', '')

    @Property('QVariantList', notify=columnsChanged)
    def readingTabs(self):
        return [{**column.serialize(), 'current': column.columnId == self._active_column_id}
                for column in getattr(self, '_reading_columns', []) if not column.empty]

    def _current_tab(self):
        column = self._active_column()
        return column._book if column and not column.empty else None

    @Property(str, notify=changed)
    def readingJobPath(self):
        column = self._active_column()
        return column.readingJobPath if column else ''

    @Property(str, notify=changed)
    def readingMachinePath(self):
        column = self._active_column()
        return column.readingMachinePath if column else ''

    @Property(str, notify=changed)
    def readingPath(self):
        column = self._active_column()
        return column.readingPath if column else ''

    def _persist_tabs(self):
        columns = [column.serialize() for column in getattr(self, '_reading_columns', [])]
        self.settings.setValue('reader/columns_v4', json.dumps(columns, ensure_ascii=False))
        self.settings.setValue('reader/active_column_v4', self._active_column_id)
        self.settings.setValue('reader/open_tabs_v2', json.dumps(
            [{k: row.get(k, '') for k in ('id', 'kind', 'title', 'source', 'job',
                                           'reading_file', 'reader_identity', 'book_hash')}
             for row in columns if row.get('id')], ensure_ascii=False))
        self.settings.setValue('reader/active_tab_v2', self._active_book)

    def _valid_descriptor(self, row):
        if not isinstance(row, dict):
            return None
        row = self.library.upgrade_descriptor(row)
        result = {key: str(row.get(key) or '') for key in
                  ('id', 'kind', 'title', 'source', 'job', 'reading_file', 'column_id',
                   'machine_path', 'epub_mode', 'reading_mode', 'reader_identity', 'book_hash',
                   'identity_schema')}
        result['manual_empty'] = bool(row.get('manual_empty', False))
        if not result['id'] and not result['kind'] and result['column_id']:
            return {'column_id': result['column_id'], 'manual_empty': True}
        if not result['id'] or result['kind'] not in ('task', 'text'):
            return None
        if result['kind'] == 'task' and Path(result['job']).resolve().parent != self.workspace.resolve():
            return None
        if result['reading_file'] and result['job'] and not Path(result['reading_file']).resolve().is_relative_to(Path(result['job']).resolve()):
            result['reading_file'] = ''
        return result

    def _restore_tabs(self, rows, active):
        if rows and not self.settings.value('reader/groups_v3_migrated', False, type=bool):
            self.settings.setValue('reader/pre_groups_v3_columns', json.dumps(rows, ensure_ascii=False))
            merged, seen = [], {}
            for raw in rows:
                row = dict(raw)
                source = Path(str(row.get('source') or ''))
                descriptor = self._epub_task_descriptor(source) if source.suffix.lower() == '.epub' else None
                if descriptor:
                    old_id = row.get('column_id')
                    row = {**descriptor, 'reading_file': '', 'column_id': old_id, 'epub_mode': 'translated'}
                    key = descriptor['id']
                elif source.suffix.lower() == '.epub':
                    key = str(source.resolve())
                else:
                    key = None
                if key and key in seen:
                    if raw.get('column_id') == active:
                        active = seen[key]['column_id']
                    continue
                merged.append(row)
                if key:
                    seen[key] = row
            rows = merged
            self.settings.setValue('reader/groups_v3_migrated', True)
        old_reader = self.reader
        old_columns = list(getattr(self, '_reading_columns', []))
        restored = []
        seen_columns = set()
        for raw in rows:
            row = self._valid_descriptor(raw)
            if not row:
                continue
            column_id = row.pop('column_id') or uuid.uuid4().hex
            manual_empty = bool(row.pop('manual_empty', False))
            if column_id in seen_columns:
                column_id = uuid.uuid4().hex
            seen_columns.add(column_id)
            restored.append(ReaderColumn(self, row, column_id=column_id,
                                         reader=old_reader if not restored else None,
                                         manual_empty=manual_empty, autoload=False))
        if not restored:
            restored = [ReaderColumn(self, column_id=uuid.uuid4().hex, reader=old_reader)]
        self._reading_columns = restored
        for column in old_columns:
            if column not in restored:
                column.dispose()
                column.deleteLater()
        match = next((column for column in restored if column.columnId == active or column.bookId == active), None)
        self._active_column_id = (match or restored[0]).columnId
        self._reading_manual_empty = bool((match or restored[0])._manual_empty)
        self._sync_owner_from_column()
        self._persist_tabs()
        for column in restored:
            if not column.empty:
                self._submit_book_load(
                    column, dict(column._book),
                    self._reading_file_for_row(column._book, verify=False),
                    purpose='restore')
        self.columnsChanged.emit()
        self.shelfChanged.emit()
        self.changed.emit()

    @Slot(str)
    def activateReadingColumn(self, identity):  # noqa: N802
        column = next((row for row in self._reading_columns if row.columnId == identity), None)
        if column and self._active_column_id != identity:
            self._sync_owner_from_column(column)
            self._reading_manual_empty = bool(column._manual_empty)
            self._persist_tabs()
            self.changed.emit()

    @Slot(result=str)
    def addReadingColumn(self):  # noqa: N802
        self.settings.setValue('reader/groups_v3_migrated', True)
        column = ReaderColumn(self, column_id=uuid.uuid4().hex, manual_empty=True)
        self._reading_columns.append(column)
        self._reading_manual_empty = True
        self._sync_owner_from_column(column)
        self._persist_tabs()
        self.columnsChanged.emit()
        self.shelfChanged.emit()
        self.changed.emit()
        return column.columnId

    @Slot(str)
    def removeReadingColumn(self, identity):  # noqa: N802
        column = next((row for row in self._reading_columns if row.columnId == identity), None)
        if not column:
            return
        if len(self._reading_columns) == 1:
            column.clear_book(manual=True)
            self._reading_manual_empty = True
            self._sync_owner_from_column(column)
        else:
            index = self._reading_columns.index(column)
            was_active = column.columnId == self._active_column_id
            self._reading_columns.remove(column)
            if was_active:
                self._sync_owner_from_column(self._reading_columns[min(index, len(self._reading_columns) - 1)])
            else:
                self._sync_owner_from_column(self._active_column())
            self._reading_manual_empty = bool(self._active_column()._manual_empty)
            column.dispose()
            column.deleteLater()
        self._persist_tabs()
        self.columnsChanged.emit()
        self.shelfChanged.emit()
        self.changed.emit()

    def _reading_file_for_row(self, row, *, verify=True):
        if not verify:
            return str(row.get('reading_file')
                       or (row.get('source') if row.get('kind') == 'text' else '')
                       or '')
        if row.get('kind') == 'text':
            return str(self._shelf_source(row))
        saved = str(row.get('reading_file') or '')
        if (saved and Path(saved).is_file()
                and Path(saved).resolve().is_relative_to(Path(row['job']).resolve())
                and not Path(saved).resolve().is_relative_to(Path(row['job']).resolve() / '原文')):
            return saved
        editions = sorted((Path(row['job']) / '译后校对').glob('*.当前阅读版.txt'))
        return str(editions[-1]) if editions else ''

    @Slot(str, str)
    def openBookInColumn(self, column_id, identity):  # noqa: N802
        column = next((row for row in self._reading_columns if row.columnId == column_id), None)
        identity = self.library.resolve_id(identity)
        book = next((dict(item) for item in self.library.rows if item['id'] == identity), None)
        if not column or not book:
            return
        source = self._shelf_source(book)
        if not source.is_file():
            self._error = ui_messages.Message(
                ui_messages.MessageCode.READING_FILE_MOVED, (),
                '这本书的文件已移动，请重新选择。')
            self.changed.emit()
            return
        book['machine_path'] = self._machine_path_for_book(book)
        reading_file = self._reading_file_for_row(book)
        if is_epub_review_text(book, reading_file):
            self._submit_book_load(column, book, reading_file)
            return
        column.set_book(book, reading_file)
        self._reading_manual_empty = False
        self._sync_owner_from_column(column)
        self._persist_tabs()
        self.shelfChanged.emit()
        self.changed.emit()
        self.shelfBookOpened.emit()

    @Slot(str, str)
    def openBookInColumnAsync(self, column_id, identity):  # noqa: N802
        column = next((row for row in self._reading_columns if row.columnId == column_id), None)
        identity = self.library.resolve_id(identity)
        book = next((dict(item) for item in self.library.rows if item['id'] == identity), None)
        if not column or not book:
            return
        if column.bookId == identity and not column.loading:
            return
        source = self._shelf_source(book)
        if not source.is_file():
            self._error = ui_messages.Message(
                ui_messages.MessageCode.READING_FILE_MOVED, (),
                '这本书的文件已移动，请重新选择。')
            self.changed.emit()
            return
        self._submit_book_load(column, book, self._reading_file_for_row(book))

    def _book_for_reading_path(self, raw_path, *, enrich=True):
        path = self.file_access.ensure(raw_path)
        if not path.is_file() or path.suffix.lower() not in {'.txt', '.md', '.partial', '.epub'}:
            self._error = ui_messages.Message(
                ui_messages.MessageCode.READING_UNAVAILABLE, (),
                '请选择可读取的 EPUB 或 TXT 文本文件。')
            self.changed.emit()
            return None, None
        managed = self._managed_reading_job(path)
        if managed:
            book = next((dict(row) for row in self.library.rows
                         if row.get('job') and Path(row['job']).resolve() == Path(managed).resolve()), None)
            if book is None:
                config_path = Path(managed) / '翻译任务.yaml'
                if config_path.is_file():
                    config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
                    source = Path(managed) / config['source']['files'][0]
                    identity = self.library.remember(
                        source, job=managed, reading_file=str(path), restore=self._restoring,
                        enrich=enrich)
                    book = next(dict(row) for row in self.library.rows if row['id'] == identity)
                else:  # Compatibility for a manually constructed local task.
                    identity = Bookshelf.key('task', managed, self._source_path or str(path))
                    book = {'id': identity, 'kind': 'task', 'title': path.stem,
                            'source': self._source_path or str(path), 'job': managed,
                            'reader_identity': Bookshelf.logical_key(
                                'task', managed, self._source_path or str(path))}
        else:
            identity = self.library.remember(
                path, reading_file=str(path), restore=self._restoring, enrich=enrich)
            book = next(dict(row) for row in self.library.rows if row['id'] == identity)
        return path, book

    def _translated_book_for_shelf_import(self, source_book):
        """Resolve a readable task edition by exact source bytes, never title/path."""
        digest = str((source_book or {}).get('book_hash') or '')
        for row in self.library.task_candidates_for_hash(
                digest, preferred_job=self._job_path):
            book = dict(row)
            try:
                root = Path(book['job']).resolve()
                if (root.parent != self.workspace.resolve()
                        or not (root / '翻译任务.yaml').is_file()):
                    continue
                reading_file = self._reading_file_for_row(book)
                machine_path = self._machine_path_for_book(book)
                if existing_target(reading_file or machine_path) is not None:
                    return book, reading_file
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None, ''

    @Slot(str, str)
    def importShelfBookInColumnAsync(self, column_id, raw_path):  # noqa: N802
        """Add a source to the shelf and open its verified translation if known."""
        column = next((row for row in self._reading_columns
                       if row.columnId == column_id), None)
        if column is None:
            return
        path = self.file_access.ensure(raw_path)
        if (not path.is_file()
                or path.suffix.lower() not in {'.txt', '.md', '.partial', '.epub'}):
            self._error = ui_messages.Message(
                ui_messages.MessageCode.READING_UNAVAILABLE, (),
                '请选择可读取的 EPUB 或 TXT 文本文件。')
            self.changed.emit()
            return
        self._ensure_reader_loader()
        token = uuid.uuid4().hex
        previous = self._reader_load_futures.get(column_id)
        if previous is not None:
            previous.cancel()
        column.begin_loading(token, path.stem)
        future = self._reader_executor.submit(file_sha256, path)
        self._reader_load_futures[column_id] = future
        signals = self._reader_loader_signals

        def deliver(completed):
            if self._reader_loader_stopping:
                return
            try:
                digest = completed.result()
                result = dict(ok=True, purpose='shelf-import-hash',
                              column=column_id, token=token, path=str(path),
                              digest=digest)
            except CancelledError:
                return
            except Exception as exc:
                result = dict(ok=False, purpose='shelf-import-hash',
                              column=column_id, token=token, path=str(path),
                              error=str(exc))
            signals.ready.emit(result)

        future.add_done_callback(deliver)

    @Slot(str, str)
    def openPathInColumn(self, column_id, raw_path):  # noqa: N802
        path, book = self._book_for_reading_path(raw_path)
        if path is None:
            return
        column = next((row for row in self._reading_columns if row.columnId == column_id), None)
        if column:
            if is_epub_review_text(book, path):
                self._submit_book_load(column, book, str(path), purpose='path')
                return
            column.set_book(book, str(path))
            self._reading_manual_empty = False
            self._sync_owner_from_column(column)
            self._persist_tabs()
            self.shelfChanged.emit()
            self.changed.emit()
            self.readingFileOpened.emit()

    @Slot(str, str)
    def openPathInColumnAsync(self, column_id, raw_path):  # noqa: N802
        column = next((row for row in self._reading_columns if row.columnId == column_id), None)
        if column is None:
            return
        path, book = self._book_for_reading_path(raw_path, enrich=False)
        if path is not None:
            self._submit_book_load(column, book, str(path), purpose='path')

    @Slot(str)
    def followTranslationInColumn(self, column_id):  # noqa: N802
        if not self._job_path:
            return
        current = next((row for row in self.library.rows if row.get('job') == self._job_path), None)
        identity = current['id'] if current else Bookshelf.key('task', self._job_path, self._source_path)
        if not any(row['id'] == identity for row in self.library.rows):
            self.library.remember(self._source_path, job=self._job_path, progress=self._progress)
        self.openBookInColumn(column_id, identity)

    @Slot(str)
    def followTranslationInColumnAsync(self, column_id):  # noqa: N802
        if not self._job_path:
            return
        current = next((row for row in self.library.rows if row.get('job') == self._job_path), None)
        identity = current['id'] if current else Bookshelf.key('task', self._job_path, self._source_path)
        if not any(row['id'] == identity for row in self.library.rows):
            self.library.remember(
                self._source_path, job=self._job_path, progress=self._progress,
                enrich=False)
        self.openBookInColumnAsync(column_id, identity)

    @Slot(result=bool)
    def manageReadingBook(self):  # noqa: N802
        """Explicitly bind translation/review controls to the focused column."""
        column = self._active_column()
        if not column or not column.readingJobPath:
            self._error = ui_messages.Message(
                ui_messages.MessageCode.READING_UNAVAILABLE, (),
                '请先在阅读栏中选择一本已关联翻译任务的书。')
        elif self._job_path and Path(column.readingJobPath).resolve() == Path(self._job_path).resolve():
            return True
        elif self.running or self.post_editor.busy:
            self._error = ui_messages.Message(
                ui_messages.MessageCode.READING_UNAVAILABLE, (),
                '另一本书的任务正在进行。并列阅读不受影响；切换翻译 / 校对任务前请先暂停。')
        else:
            source = self._shelf_source(column._book)
            if source.is_file():
                self._select_source(
                    source, project=Path(column.readingJobPath).resolve(),
                    reader_async=True)
                return bool(self._job_path and Path(self._job_path).resolve() == Path(column.readingJobPath).resolve())
            self._error = ui_messages.Message(
                ui_messages.MessageCode.READING_FILE_MOVED, (),
                '这本书的原文已移动，暂时不能切换翻译 / 校对任务。')
        self.changed.emit()
        return False

    @Slot(str)
    def openShelfBook(self, identity):  # noqa: N802
        column = self._active_column() or self._ensure_column()
        self.openBookInColumn(column.columnId, identity)

    @Slot(str)
    def openShelfBookAsync(self, identity):  # noqa: N802
        column = self._active_column() or self._ensure_column()
        self.openBookInColumnAsync(column.columnId, identity)

    def _epub_task_descriptor(self, source):
        """Resolve versions by exact original bytes, never by a similar title."""
        source = Path(source).resolve()
        candidates = [dict(r) for r in self.library.rows if r.get('kind') == 'task']
        jobs = [self._job_path]
        if source.parent.name == '原文':
            jobs.append(str(source.parent.parent))
        for raw in jobs:
            if raw and not any(r.get('job') == raw for r in candidates):
                candidates.append(dict(
                    id=Bookshelf.key('task', raw, self._source_path or str(source)),
                    kind='task', job=raw, source=self._source_path or str(source),
                    title=source.stem))
        digest = None
        for row in candidates:
            job = Path(row['job']).resolve()
            try:
                if job.parent != self.workspace.resolve():
                    continue
                meta = json.loads((job / '.epub-source.json').read_text())
                original = (job / meta['source']).resolve()
                if not original.is_relative_to(job) or not original.is_file():
                    continue
                if source != original:
                    if digest is None:
                        digest = hashlib.sha256(source.read_bytes()).hexdigest()
                    if digest != meta['sha256']:
                        continue
                # The retained original is authoritative even if the import moved.
                row['source'] = str(original)
                row.pop('reading_file', None)
                return row
            except (OSError, ValueError, KeyError, TypeError):
                continue
        return None

    def _epub_pair(self, current=None):
        current = current or self._active_column()
        if current and current._group_parent:
            current = current._group_parent
        if not current or current.epubMode != 'bilingual' or not current._epub_peer:
            return []
        other = current._epub_peer
        return [current, other] if current.epub.book and other.epub.book else []

    @Property('QVariantMap', notify=changed)
    def epubPairNavigation(self):
        pair = self._epub_pair()
        return dict(active=bool(pair),
                    columnIds=[c.columnId for c in pair],
                    previous=bool(pair) and all(c.epub.chapter > 0 for c in pair),
                    next=bool(pair) and all(c.epub.chapter + 1 < len(c.epub.book['chapters']) for c in pair))

    @Property(bool, notify=changed)
    def readingOriginalOnLeft(self):  # noqa: N802
        legacy = self.settings.value('reader/epub_original_on_left', True, type=bool)
        return self.settings.value('reader/original_on_left', legacy, type=bool)

    @Property(bool, notify=changed)
    def epubOriginalOnLeft(self):
        return self.readingOriginalOnLeft

    @Slot()
    def swapReadingSides(self):  # noqa: N802
        current = self._active_column()
        if not self._epub_pair() and not (current and current.reader.hasSources):
            return
        value = not self.readingOriginalOnLeft
        self.settings.setValue('reader/original_on_left', value)
        self.settings.setValue('reader/epub_original_on_left', value)
        self.settings.sync()
        self.changed.emit()

    @Slot()
    def swapEpubSides(self):
        if not self._epub_pair():
            return
        self.swapReadingSides()

    @Property('QVariantMap', notify=changed)
    def epubColumnOrder(self):
        # Change visual positions, not the object list: native reader instances,
        # selected text, bookmarks, progress and active translation stay intact.
        columns = getattr(self, '_reading_columns', [])
        order = {c.columnId: index for index, c in enumerate(columns)}
        return order

    @Property(bool, notify=changed)
    def readingScrollLinked(self):  # noqa: N802
        return self.settings.value('reader/scroll_linked', True, type=bool)

    @readingScrollLinked.setter
    def readingScrollLinked(self, value):  # noqa: N802
        self.settings.setValue('reader/scroll_linked', bool(value))
        self.settings.sync()
        self.changed.emit()

    @Property(bool, notify=changed)
    def epubScrollLinked(self):
        return self.readingScrollLinked

    @epubScrollLinked.setter
    def epubScrollLinked(self, value):
        self.readingScrollLinked = value

    @Slot(str, float, result=bool)
    def scrollEpubPair(self, identity, fraction):
        views = [view for c in self._reading_columns for view in (c, c._epub_peer) if view]
        current = next((c for c in views if c.columnId == identity), None)
        if current is None or not self.epubScrollLinked:
            return False
        pair = self._epub_pair(current)
        if not pair:
            return False
        import math
        if not math.isfinite(fraction):
            return False
        fraction = min(1., max(0., fraction))
        for column in pair:
            column.epub.savePosition(fraction)
            if column is not current:
                column.epub.scrollRequested.emit(fraction)
        return True

    @Slot(int)
    def navigateEpubPair(self, delta):
        pair = self._epub_pair()
        if delta not in (-1, 1) or not pair:
            return
        targets = [(c, c.epub.chapter + delta) for c in pair]
        if any(not 0 <= chapter < len(c.epub.book['chapters']) for c, chapter in targets):
            return
        for column, chapter in targets:
            column.epub.navigate(chapter, 0.)

    @Slot(str)
    def selectReadingTab(self, identity):  # noqa: N802
        column = next((row for row in self._reading_columns if row.bookId == identity), None)
        if column:
            self.activateReadingColumn(column.columnId)

    @Slot(str)
    def closeReadingTab(self, identity):  # noqa: N802
        column = next((row for row in self._reading_columns if row.bookId == identity), None)
        if column:
            self.removeReadingColumn(column.columnId)

    def _register_task_tab(self, *, async_load=False):
        if not self._job_path:
            return
        current = next((row for row in self.library.rows if row.get('job') == self._job_path), None)
        identity = current['id'] if current else Bookshelf.key('task', self._job_path, self._source_path)
        book = next((dict(row) for row in self.library.rows if row['id'] == identity), None)
        if not book:
            return
        column = next((row for row in self._reading_columns if row.bookId == identity), None)
        appended = False
        deferred = False
        if column is None:
            column = self._active_column()
            if not column:
                column = ReaderColumn(self, column_id=uuid.uuid4().hex)
                self._reading_columns.append(column)
                self._active_column_id = column.columnId
                appended = True
            reading_file = self._read_file or self._reading_file_for_row(book)
            deferred = bool(async_load or is_epub_review_text(book, reading_file))
            if deferred:
                self._submit_book_load(column, book, reading_file, purpose='task')
            else:
                column.set_book(book, reading_file)
        elif (is_epub_review_text(book, column._read_file)
              and not column.epubActive and not column.loading):
            deferred = True
            self._submit_book_load(
                column, book, column._read_file, purpose='task')
        self._reading_manual_empty = False
        if not deferred or column.bookId == identity:
            self._sync_owner_from_column(column)
            self._persist_tabs()
        if appended:
            self.columnsChanged.emit()

    def _remember_reading_tab(self, row, reading_file=''):
        column = self._ensure_column(reader=self.reader)
        column.set_book(row, reading_file)
        self._reading_manual_empty = False
        self._sync_owner_from_column(column)
        self._persist_tabs()

    def _activate_reading_tab(self, identity):
        self.selectReadingTab(identity)
        return any(row.bookId == identity for row in self._reading_columns)

    def _queue_reader_refresh(self, column):
        if column.loading:
            return True
        book = dict(column._book)
        if not book:
            return True
        reading_file = str(column._read_file or '')
        machine_path = self._machine_path_for_book(book)
        target = column._target()
        if target is None:
            return True
        try:
            signature = epub_work_signature(book, reading_file, machine_path)
        except OSError:
            return True
        if signature is not None and signature[0] == 'reviewed-epub':
            review_source, companion, machine_source, machine_companion = signature[1:]
            needs_sync = bool(
                machine_source is not None and machine_source[4] > review_source[4]
            )
            if needs_sync:
                if column._refresh_token or column._refresh_signature == signature:
                    return True
                self._submit_book_load(
                    column, book, reading_file, purpose='refresh',
                    request_signature=signature, allow_epub_export=False)
                return True
            needs_export = bool(
                companion is None or companion[4] < review_source[4]
            )
            if needs_export:
                machine_ready = bool(
                    machine_source is None
                    or (machine_companion is not None
                        and machine_companion[4] >= machine_source[4])
                )
                if (machine_ready and not column._refresh_token
                        and column._refresh_signature != signature):
                    self._submit_book_load(
                        column, book, reading_file, purpose='refresh',
                        request_signature=signature, allow_epub_export=True)
                return True
            current = companion
            if current == getattr(column.epub, '_signature', None):
                column._refresh_signature = signature
                return True
            if column._refresh_token or column._refresh_signature == signature:
                return True
            self._submit_book_load(
                column, book, reading_file, purpose='refresh',
                request_signature=signature, allow_epub_export=False)
            return True
        if signature is None:
            try:
                target_signature = path_signature(target)
                machine = Path(machine_path).expanduser().resolve() if machine_path else None
                machine_signature = (path_signature(machine)
                                     if machine is not None and machine.is_file() else None)
                needs_current_sync = bool(
                    reading_file and book.get('job') and machine_signature
                    and Path(reading_file).name.endswith('.当前阅读版.txt')
                    and machine_signature[4] > target_signature[4])
                signature = ('text', target_signature, machine_signature)
            except OSError:
                return True
            if (not needs_current_sync
                    and tuple(signature[1]) == getattr(column.reader, '_signature', None)):
                column._refresh_signature = signature
                return True
            if column._refresh_token or column._refresh_signature == signature:
                return True
            self._submit_book_load(column, book, reading_file, purpose='refresh',
                                   request_signature=signature)
            return True
        if signature[0] == 'epub':
            current = signature[1]
        else:
            source, companion = signature[1], signature[2]
            current = (companion if companion is not None
                       and companion[4] >= source[4] else None)
            # Translation checkpoints are durable immediately, while EPUB is
            # intentionally a coalesced display publication. A timer refresh
            # must not turn every checkpoint back into a whole-book export.
            if current is None:
                return True
        if current is not None and current == getattr(column.epub, '_signature', None):
            column._refresh_signature = signature
            return True
        if column._refresh_token or column._refresh_signature == signature:
            return True
        self._submit_book_load(column, book, reading_file, purpose='refresh',
                               request_signature=signature)
        return True

    def _refresh_readers(self):
        for column in list(getattr(self, '_reading_columns', [])):
            self._queue_reader_refresh(column)
        self._sync_owner_from_column()

    def _alignment(self):
        column = self._active_column()
        return column._alignment() if column else ([], [])

    def _bookmark_rows(self):
        column = self._active_column()
        return column._bookmark_rows() if column else []

    @Property('QVariantList', notify=changed)
    def bookmarks(self):
        column = self._active_column()
        return column.bookmarks if column else []

    @Slot(int, str)
    def addBookmark(self, row, title):  # noqa: N802
        column = self._active_column()
        if column:
            column.addBookmark(row, title)

    @Slot(str)
    def removeBookmark(self, identity):  # noqa: N802
        column = self._active_column()
        if column:
            column.removeBookmark(identity)
