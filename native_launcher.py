from __future__ import annotations

import hashlib
import json
import os
os.environ.setdefault('QSG_RENDER_LOOP', 'basic')
import re
import subprocess
import sys
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path

import yaml
from PySide6.QtCore import (
    QLockFile,
    QObject,
    QProcess,
    QProcessEnvironment,
    QSettings,
    QStandardPaths,
    QTimer,
    QUrl,
    Property,
    Signal,
    Slot,
)
from PySide6.QtGui import QDesktopServices, QFontDatabase, QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication, QFileDialog


APP_NAME = "鲸读"
sys.path.insert(0, str(Path(__file__).resolve().parent / 'runtime/lib'))
from application_identity import bundle_identifier, application_settings
from provider_profiles import ProviderProfiles, DEFAULT_LOCAL_PROFILE, LOCAL_API_BASE, LOCAL_PROFILES
INSTANCE_KEY = f"{bundle_identifier()}.{os.getuid() if hasattr(os, 'getuid') else 'user'}"
INSTANCE_ACTIVATE_MESSAGE = b"activate\n"
# Compatibility metadata for the local preset; user routes live in
# ProviderProfiles and each AI feature selects its own immutable route.
API_BASE = ""
MODEL = ""
DEFAULT_MODEL_PROFILE = DEFAULT_LOCAL_PROFILE
MODEL_PROFILE_ORDER = tuple(LOCAL_PROFILES)
MODEL_PROFILES = {
    pid: dict(row, short=row['label'], auth_provider='none', local=True)
    for pid, row in LOCAL_PROFILES.items()
}
THEME_IDS = {"jade", "ocean", "amber", "sakura"}
BACKGROUND_IDS = {"paper", "warm", "mist", "night"}
SUPPORTED_SUFFIXES = {".txt", ".md", ".rtf", ".doc", ".docx", ".epub", ".pdf"}


def instance_lock_path(instance_key: str = INSTANCE_KEY, temp_directory: str | None = None) -> Path:
    base = temp_directory or QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.TempLocation
    )
    if not base:
        base = "/tmp"
    safe_key = re.sub(r"[^A-Za-z0-9_.-]+", "-", instance_key)
    return Path(base) / f"{safe_key}.lock"


def instance_server_path(instance_key: str, lock_path: Path | None = None) -> Path:
    # Unix socket names are capped at ~104 bytes on macOS; container paths are
    # long. Keep the leaf short and locate it inside this app's temp directory.
    leaf = 'wr-' + hashlib.sha256(instance_key.encode()).hexdigest()[:12] + '.sock'
    return Path(lock_path or instance_lock_path(instance_key)).parent / leaf


def notify_running_instance(instance_key: str = INSTANCE_KEY, timeout_ms: int = 1200,
                            server_path: str | None = None) -> bool:
    """Ask the primary process to show its window, without starting another UI."""
    deadline = time.monotonic() + max(0, timeout_ms) / 1000.0
    while True:
        socket = QLocalSocket()
        socket.connectToServer(server_path or str(instance_server_path(instance_key)))
        remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
        if socket.waitForConnected(min(150, remaining_ms)):
            socket.write(INSTANCE_ACTIVATE_MESSAGE)
            socket.flush()
            socket.waitForBytesWritten(min(150, remaining_ms))
            socket.disconnectFromServer()
            return True
        socket.abort()
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.04)


class SingleInstanceGuard(QObject):
    """Own the per-user app lock and relay activation requests to the first UI."""

    activationRequested = Signal()

    def __init__(
        self,
        instance_key: str = INSTANCE_KEY,
        lock_path: Path | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._instance_key = instance_key
        self._lock = QLockFile(str(lock_path or instance_lock_path(instance_key)))
        self._server_path = str(instance_server_path(instance_key, lock_path))
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._receive_activation_requests)
        self._primary = False

    def claim(self) -> bool:
        if not self._lock.tryLock(0):
            if notify_running_instance(self._instance_key, server_path=self._server_path):
                return False
            # QLockFile normally removes dead-owner locks itself. This handles
            # the remaining recoverable stale-file case without ever removing
            # a lock held by a live process.
            if not self._lock.removeStaleLockFile() or not self._lock.tryLock(0):
                return False

        QLocalServer.removeServer(self._server_path)
        if not self._server.listen(self._server_path):
            self._lock.unlock()
            raise RuntimeError(f"无法建立鲸读单实例通道：{self._server.errorString()}")
        self._primary = True
        return True

    def close(self) -> None:
        if not self._primary:
            return
        self._server.close()
        self._lock.unlock()
        self._primary = False

    def _receive_activation_requests(self) -> None:
        received = False
        while self._server.hasPendingConnections():
            connection = self._server.nextPendingConnection()
            if connection is None:
                continue
            connection.readAll()
            connection.disconnectFromServer()
            connection.deleteLater()
            received = True
        if received:
            self.activationRequested.emit()


def activate_window(window: QObject) -> None:
    """Restore and foreground the existing QML window."""
    show_normal = getattr(window, "showNormal", None)
    if callable(show_normal):
        show_normal()
    else:
        show = getattr(window, "show", None)
        if callable(show):
            show()
    raise_window = getattr(window, "raise_", None)
    if callable(raise_window):
        raise_window()
    request_activate = getattr(window, "requestActivate", None)
    if callable(request_activate):
        request_activate()


def resource_root() -> Path:
    return Path(__file__).resolve().parent


ROOT = resource_root()
sys.path.insert(0, str(ROOT / "runtime/lib"))
from task_config import (  # noqa: E402
    AUTO_LANGUAGE,
    COMMON_LANGUAGES,
    LanguageDetectionError,
    detect_language,
    extract_source_text,
    inside,
    language,
    language_scripts,
    prepare as prepare_task,
    read_glossary,
    validate as validate_task,
)
from language_catalog import (  # noqa: E402
    AUTO_ID,
    CUSTOM_ID,
    option_value as language_option_value,
    source_options as language_source_options,
    target_options as language_target_options,
)
from reader import ReaderModel  # noqa: E402
from book_identity import canonical_path  # noqa: E402
from bookshelf import Bookshelf  # noqa: E402
from reading_sessions import ReadingSessions, read_list  # noqa: E402
from post_edit_controller import PostEditController  # noqa: E402
from research_controller import ResearchController
from locale_service import LocaleService  # noqa: E402
import ui_messages  # noqa: E402
from storage_context import StorageContext  # noqa: E402
from apple_services import reveal_file, choose_open_file  # noqa: E402
from credential_store import MemoryCredentialStore  # noqa: E402
from worker_context import prepare_worker  # noqa: E402
from file_access import FileAccessManager, application_home, FileAccessError  # noqa: E402
from data_settings import DataSettings  # noqa: E402


PROFILE_MESSAGE_CODES = {
    "local_1_8b": {
        "label": ui_messages.MessageCode.MODEL_PROFILE_LOCAL_1_8_LABEL,
        "short": ui_messages.MessageCode.MODEL_PROFILE_LOCAL_1_8_SHORT,
        "detail": ui_messages.MessageCode.MODEL_PROFILE_LOCAL_1_8_DETAIL,
    },
    "local_7b": {
        "label": ui_messages.MessageCode.MODEL_PROFILE_LOCAL_7_LABEL,
        "short": ui_messages.MessageCode.MODEL_PROFILE_LOCAL_7_SHORT,
        "detail": ui_messages.MessageCode.MODEL_PROFILE_LOCAL_7_DETAIL,
    },
}

FONT_MESSAGE_CODES = (
    (ui_messages.MessageCode.FONT_SONGTI, "Songti SC"),
    (ui_messages.MessageCode.FONT_KAITI, "Kaiti SC"),
    (ui_messages.MessageCode.FONT_FANGSONG, "FangSong_GB2312"),
    (ui_messages.MessageCode.FONT_PINGFANG, "PingFang SC"),
    (ui_messages.MessageCode.FONT_HIRAGINO, "Hiragino Sans GB"),
    (ui_messages.MessageCode.FONT_GEORGIA, "Georgia"),
)


def auth_path() -> Path:
    return Path.home() / ".local/share/opencode/auth.json"


def auth_ready(path: Path | None = None) -> bool:
    try:
        payload = json.loads((path or auth_path()).read_text(encoding="utf-8"))
        provider = payload.get("dgx-spark-translate") or {}
        return provider.get("type") == "api" and bool(provider.get("key"))
    except (OSError, json.JSONDecodeError, AttributeError):
        return False


def model_route(profile: str) -> dict[str, object]:
    """Return a fresh, allow-listed route; never accept endpoints from task files."""
    selected = profile if profile in MODEL_PROFILES else DEFAULT_MODEL_PROFILE
    return dict(MODEL_PROFILES[selected])


def ollama_models(timeout: float = 1.5) -> set[str]:
    """Read only the local Ollama catalog; a stopped service is simply unavailable."""
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {
            str(item.get("name") or item.get("model"))
            for item in payload.get("models") or []
            if isinstance(item, dict) and (item.get("name") or item.get("model"))
        }
    except (OSError, ValueError, AttributeError, urllib.error.URLError):
        return set()


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_stem(value: str) -> str:
    cleaned = re.sub(r"[\x00-\x1f/:\\]", "-", value).strip(" .-")
    return (cleaned or "未命名小说")[:60]


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def clock_text(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes:02d}:{secs:02d}"


class TranslatorController(ReadingSessions):
    # Qt requires notify signals for these properties in this meta-object. Relay
    # the base signals rather than shadowing them (which would freeze tab bindings).
    stateChanged = Signal()
    libraryChanged = Signal()
    readingFileOpened = Signal()
    readingAboutToChange = Signal()
    shelfBookOpened = Signal()

    def __init__(self, *, workspace: Path | None = None, settings=None,
                 storage_context: StorageContext | None = None,
                 locale_service: LocaleService | None = None,
                 credential_store=None,
                 restore: bool = True) -> None:
        super().__init__()
        self.changed.connect(self.stateChanged)
        self.shelfChanged.connect(self.libraryChanged)
        self.root = ROOT
        supplied_settings = settings is not None
        self.settings = settings if supplied_settings else application_settings()
        # Explicit production settings keep production restore behaviour; only a
        # settings object without a storage context is an isolated QA run.
        self._isolated_settings = supplied_settings and storage_context is None
        if storage_context is not None:
            self.storage_context = storage_context
        elif supplied_settings:
            settings_path = Path(self.settings.fileName()).expanduser().resolve()
            self.storage_context = StorageContext.isolated(
                settings_path.parent / '.whaleread-storage', purpose='qa',
                settings_scope=str(settings_path))
        else:
            self.storage_context = StorageContext.production()
        self.storage_context.assert_test_safe()
        data_root = (application_home() / 'Library/Application Support/WhaleRead'
                     if self.storage_context.purpose == 'production'
                     else Path(self.settings.fileName()).resolve().parent / '.whaleread-data')
        self.data_root = data_root
        self.file_access = FileAccessManager(self.settings, internal_roots=(
            data_root, self.storage_context.cache_root, self.storage_context.thumbnail_root,
            *((workspace,) if workspace is not None and self.storage_context.purpose != 'production' else ())),
            strict=False if self.storage_context.purpose != 'production' else None)
        default_workspace = data_root / 'Tasks'
        requested_workspace = workspace or Path(self.settings.value('storage/workspace', str(default_workspace), type=str))
        self._storage_notice = ''
        try:
            self.workspace = self.file_access.ensure(requested_workspace)
        except FileAccessError as exc:
            self.workspace = default_workspace
            self._storage_notice = str(exc)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.providers = ProviderProfiles(
            self.settings, credential_store=(credential_store if credential_store is not None else
                MemoryCredentialStore() if self.storage_context.purpose != 'production' else None),
            locked=self._provider_locked, parent=self)
        self.providers.changed.connect(self._providers_changed)
        self._bound_engine = None
        # A supplied service is already started by ``main()``; a self-made one
        # provides the QML properties until someone calls ``start()``.
        self.locale = locale_service if locale_service is not None else LocaleService(self.settings)
        # A locale switch re-projects already-visible state through the existing
        # signals only: no controller, engine, worker or cache is rebuilt.
        self.locale.changed.connect(self._on_locale_changed)
        self.research = ResearchController(self, restore=restore)
        self.library = Bookshelf(self.settings, self.storage_context)
        using_columns_v4 = self.settings.contains('reader/columns_v4')
        had_columns = using_columns_v4 or self.settings.contains('reader/columns_v2')
        saved_columns = read_list(
            self.settings, 'reader/columns_v4' if using_columns_v4 else 'reader/columns_v2')
        saved_active_column = self.settings.value(
            'reader/active_column_v4' if using_columns_v4 else 'reader/active_column_v2', '', type=str)
        using_tabs_v2 = self.settings.contains('reader/open_tabs_v2')
        had_tabs = using_tabs_v2 or self.settings.contains('reader/open_tabs_v1')
        saved_tabs = read_list(
            self.settings, 'reader/open_tabs_v2' if using_tabs_v2 else 'reader/open_tabs_v1')
        saved_active_tab = self.library.resolve_id(self.settings.value(
            'reader/active_tab_v2' if using_tabs_v2 else 'reader/active_tab_v1', '', type=str))
        self._reading_columns = []
        self._active_column_id = ''
        self._reading_manual_empty = False
        self._alignment_signature = None
        self._alignment_data = ([], [])
        if restore:
            for root in self._task_roots():
                self.library.bootstrap(root)
        self._restoring = restore
        self._active_book = ''
        self._bilingual_signature = None
        self._bilingual_message = ''
        available_fonts = set(QFontDatabase.families())
        self._reader_fonts = [
            {'label_code': code, 'family': family}
            for code, family in FONT_MESSAGE_CODES if family in available_fonts
        ]
        self.reader = ReaderModel(self)
        self._read_file = ""
        self._reader_identity = ""
        self._auto_glossary = False
        self._task_locked = False
        self._new_job_path = ""
        self._scan_progress = 0.0
        self._scanning = False
        self._glossary_summary = ""
        self._glossary_pending = 0
        self._source_path = ""
        self._source_name = ""
        self._source_info = ui_messages.Message(ui_messages.MessageCode.SOURCE_FORMATS)
        self._source_language = ""
        self._source_language_auto = True
        self._target_language = "简体中文"
        self._language_detection: dict = {}
        self._language_notice = ""
        self._language_label = ui_messages.Message(
            ui_messages.MessageCode.LANGUAGE_DIRECTION_AUTO, ("简体中文",))
        self._glossary_path = ""
        self._instructions = ""
        self._speed_mode = bool(self.settings.value("speed_mode", False, type=bool))
        saved_profile = self.settings.value("translation/profile", DEFAULT_MODEL_PROFILE, type=str)
        self._translation_profile = saved_profile
        saved_theme = self.settings.value("appearance/theme", "jade", type=str)
        self._theme_id = saved_theme if saved_theme in THEME_IDS else "jade"
        saved_background = self.settings.value("appearance/background", "paper", type=str)
        # Migrate the former independent night switch into the one background
        # state without silently changing a reader who last used night mode.
        if self.settings.value("reader/night", False, type=bool):
            saved_background = "night"
        self._background_id = saved_background if saved_background in BACKGROUND_IDS else "paper"
        self.settings.setValue("appearance/background", self._background_id)
        self.settings.setValue("reader/night", self._background_id == "night")
        self._local_models: set[str] = set()
        self._local_models_checked = False
        self._probe_process: QProcess | None = None
        self._probe_buffer = ""
        self._probe_busy = False
        self._probe_result = ""
        self._probe_status = ui_messages.Message(ui_messages.MessageCode.PROBE_HELP)
        self._job_path = ""
        self._output_path = ""
        self._status_title = ui_messages.Message(ui_messages.MessageCode.READY)
        self._status_detail = ui_messages.Message(
            ui_messages.MessageCode.READY_SELECT_SOURCE, (self._model_display_message(),))
        self._error = ui_messages.Message("")
        self._progress = 0.0
        self._elapsed = 0.0
        self._speed = 0.0
        self._running = False
        self._finished = False
        self._pausing = False
        self._result_seen = False
        self._process: QProcess | None = None
        self._buffer = ""
        self._started_at = 0.0
        self._translation_started_at = 0.0
        self._base_completed_chars = 0
        self._current_completed_chars = 0
        self._total_chars = 0
        self.post_editor = PostEditController(self)
        from ask_ai import AskAI
        self.ask_ai = AskAI(self)
        self.data_settings = DataSettings(self)
        self.changed.connect(self.post_editor.changed)

        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)
        self.reader_timer = QTimer(self)
        self.reader_timer.setInterval(2000)
        self.reader_timer.timeout.connect(self._refresh_readers)
        self.reader_timer.start()

        last_source = self.settings.value("last_source", "", type=str)
        if restore and last_source and (not self._isolated_settings or Path(last_source).is_file()):
            last_job = Path(self.settings.value('last_job', '', type=str)).resolve()
            project = last_job if last_job.parent in self._task_roots() else None
            if self._isolated_settings:
                self._select_source(Path(last_source), inspect=False, project=project)
            else:
                # The Downloads folder can require a macOS consent check that
                # never returns for an automatic open. Restore the management
                # summary from the content-addressed index only; actual book
                # reads stay on the reader worker or follow an explicit click.
                self._source_path = str(Path(last_source).expanduser())
                self._source_name = Path(last_source).name
                self._job_path = str(project) if project is not None else ''
                self._new_job_path = self._job_path
                indexed = next((row for row in self.library.rows
                                if row.get('job') == self._job_path), None)
                self._progress = float((indexed or {}).get('progress') or 0)
                self._finished = self._progress >= 1
                self._status_title = ui_messages.Message(
                    ui_messages.MessageCode.FINISHED_TASK if self._finished
                    else ui_messages.MessageCode.RESUMABLE_TASK)
                self._status_detail = ui_messages.Message(
                    ui_messages.MessageCode.FINISHED_READY if self._finished
                    else ui_messages.MessageCode.RESUMABLE_PROGRESS,
                    () if self._finished else (f"{self._progress * 100:.1f}",))
        pending_read = ''
        last_read = self.settings.value('last_read_file', '', type=str)
        if restore and last_read and (not self._isolated_settings or Path(last_read).is_file()):
            candidate = Path(last_read).resolve()
            if self._can_restore_reading(candidate):
                pending_read = str(candidate)
            else:
                # 1.4.1 could leave a corrected edition from the previous source
                # pinned here. Do not revive another managed book on launch.
                self.settings.remove('last_read_file')
        self._restoring = False
        if restore and had_columns:
            self._restore_tabs(saved_columns, saved_active_column)
        elif restore and had_tabs:
            self._restore_tabs(saved_tabs, saved_active_tab)
        else:
            self._ensure_column(reader=self.reader)
            self._sync_owner_from_column()
            if pending_read:
                self.openPathInColumnAsync(self._active_column_id, pending_read)
            elif self._job_path:
                self._register_task_tab()
        self._refresh_shelf_metadata()

    @Property(QObject, constant=True)
    def askAI(self):
        return self.ask_ai

    @Property(QObject, constant=True)
    def modelSettings(self):  # noqa: N802
        return self.providers

    @Property(QObject, constant=True)
    def dataSettings(self):  # noqa: N802
        return self.data_settings

    def _provider_locked(self, feature):
        if feature == 'translation':
            research = getattr(self, 'research', None)
            return bool(getattr(self, '_running', False) or getattr(self, '_probe_busy', False)
                        or (research and research.process is not None and research._action in {'translate', 'batch'}))
        if feature == 'ocr':
            research = getattr(self, 'research', None)
            return bool(research and research.process is not None and research._action in {'analyze', 'batch'})
        service = getattr(self, 'ask_ai' if feature == 'ask' else 'post_editor', None)
        return bool(service and (service.process is not None))

    def _providers_changed(self):
        # Revoking consent also prevents retries or automatic continuation in
        # an in-flight worker. Data already sent cannot be recalled.
        pid = getattr(self, '_translation_profile', '')
        if not self.providers.consented('translation', pid):
            if getattr(self, '_probe_process', None) is not None:
                self._probe_process.kill()
            if getattr(self, '_running', False):
                self.pauseTranslation()
        research = getattr(self, 'research', None)
        if research and research.process is not None:
            if any(not self.providers.consented(feature, route['id'])
                   for feature, route in research.active_routes.items()):
                research.stop()
        if hasattr(self, 'ask_ai'):
            if self.ask_ai.process is not None and not self.providers.consented('ask', self.providers.selected('ask')):
                self.ask_ai.cancel()
            self.ask_ai.changed.emit()
        if hasattr(self, 'post_editor'):
            if (self.post_editor.process is not None and self.post_editor._action in self.post_editor.AUTOMATED_ACTIONS
                    and not self.providers.consented('review', self.providers.selected('review'))):
                self.post_editor.stop()
            self.post_editor.changed.emit()
        self.changed.emit()

    @Property(bool, notify=stateChanged)
    def onboardingPending(self):  # noqa: N802
        return not self.settings.value('onboarding/completed_v1', False, type=bool)

    @Slot()
    def finishOnboarding(self):  # noqa: N802
        self.settings.setValue('onboarding/completed_v1', True)
        self.settings.sync()
        self.changed.emit()

    @Property(str, constant=True)
    def privacyText(self):  # noqa: N802
        return (self.root / 'docs/PRIVACY.md').read_text(encoding='utf-8')

    @Property(str, constant=True)
    def licenseText(self):  # noqa: N802
        path = self.root / ('licenses/THIRD_PARTY_NOTICES.md' if getattr(sys, 'frozen', False)
                            else 'packaging/licenses/THIRD_PARTY_NOTICES.md')
        return path.read_text(encoding='utf-8')

    def _task_roots(self):
        roots = [self.workspace.resolve()]
        for raw in read_list(self.settings, 'storage/task_roots_v1'):
            if isinstance(raw, str):
                try:
                    root = self.file_access.ensure(raw)
                    if root not in roots:
                        roots.append(root)
                except OSError:
                    pass
        return roots

    @Property(QObject, constant=True)
    def researchModel(self):
        return self.research

    @Property(QObject, constant=True)
    def postEditor(self):
        return self.post_editor

    @Property(QObject, notify=stateChanged)
    def readerModel(self):  # noqa: N802
        return self.reader

    @Property('QVariantList', notify=libraryChanged)
    def bookshelf(self):
        result = []
        open_ids = {column.bookId for column in self._reading_columns if not column.empty}
        # Editions are one logical book on the shelf. Keep every task/source
        # record on disk, but display the active or newest managed edition.
        task_winners = {}
        for entry in self.library.rows:
            if entry.get('kind') != 'task':
                continue
            identity = str(entry.get('reader_identity') or entry['id'])
            preferred = self.library.preferred_task_for_hash(
                entry.get('book_hash'), preferred_job=self._job_path)
            if preferred is not None:
                task_winners[identity] = preferred
                continue
            current = task_winners.get(identity)
            if current is None or entry.get('job') == self._job_path:
                task_winners[identity] = entry
                continue
            # Rows are stored newest-last. Keep QML's first shelf evaluation
            # metadata-only so a protected folder cannot block first paint.
            task_winners[identity] = entry
        originals = {
            str(value) for entry in self.library.rows if entry.get('kind') == 'task'
            for value in (entry.get('source'), entry.get('retained_source'))
            if value and Path(str(value)).suffix.lower() == '.epub'
        }
        for row in self.library.rows:
            logical = str(row.get('reader_identity') or row['id'])
            if row.get('kind') == 'task' and task_winners.get(logical) is not row:
                continue
            if row.get('kind') == 'text' and logical in task_winners:
                continue
            if row.get('kind') == 'text' and str(Path(row['source']).resolve()) in originals:
                continue
            progress = self._progress if row.get('job') == self._job_path else float(row.get('progress') or 0)
            # The shelf detail is a stable code plus parameters, rendered here so
            # a locale switch re-renders an already-built list.  Publisher
            # titles and other user content stay untouched.
            if row['kind'] == 'text':
                detail = ui_messages.Message(
                    ui_messages.MessageCode.EPUB_ORIGINAL_READING
                    if Path(row['source']).suffix.lower() == '.epub'
                    else ui_messages.MessageCode.TXT_READING)
            elif progress >= 1:
                detail = ui_messages.Message(ui_messages.MessageCode.FINISHED_TASK)
            else:
                detail = ui_messages.Message(ui_messages.MessageCode.TRANSLATED_PERCENT,
                                             (f"{progress * 100:.1f}",))
            result.append({**row, 'current': row['id'] == self._active_book,
                           'opened': row['id'] in open_ids,
                           'available': bool(row.get('retained_source') or row.get('source')),
                           'detail_code': detail.code, 'detail_args': list(detail.args),
                           'detail': ui_messages.render(detail)})
        return result

    def _shelf_source(self, row):
        source = Path(row['source'])
        if row['kind'] == 'task':
            # A task's retained source is immutable and authoritative. The
            # import location is only an alias and may later move or change.
            retained = str(row.get('retained_source') or '')
            if retained:
                try:
                    candidate = self.file_access.ensure(retained)
                    if candidate.is_file():
                        return candidate
                except OSError:
                    pass
        for raw in [row.get('source'), *(row.get('locations') or [])]:
            if raw:
                try:
                    candidate = self.file_access.ensure(raw)
                    if candidate.is_file():
                        return candidate
                except OSError:
                    pass
        return source

    def _save_book_session(self):
        column = self._active_column()
        if column:
            column._read_file = self._read_file
            column._reader_identity = self._reader_identity
            if self.reader is not column.reader:
                self.reader = column.reader
        tab = self._current_tab()
        if tab:
            tab['reading_file'] = self._read_file
        for row in self.library.rows:
            if row.get('job') == self._job_path:
                row['progress'] = self._progress
            if row['id'] == self._active_book:
                if (row['kind'] == 'task' and (not self._read_file or not
                        Path(self._read_file).resolve().is_relative_to(Path(row['job']).resolve() / '原文'))):
                    row['reading_file'] = self._read_file
        self.library.save()
        self._persist_tabs()

    @Slot(str)
    def openShelfBook(self, identity):  # noqa: N802
        ReadingSessions.openShelfBook(self, identity)

    @Slot(str)
    def openShelfBookAsync(self, identity):  # noqa: N802
        ReadingSessions.openShelfBookAsync(self, identity)

    @Slot(str)
    def removeShelfBook(self, identity):  # noqa: N802
        identity = self.library.resolve_id(identity)
        entry = next((r for r in self.library.rows if r['id'] == identity), None)
        cache_identity = str((entry or {}).get('reader_identity')
                             or (entry or {}).get('job') or (entry or {}).get('source') or '')
        related = ([row['id'] for row in self.library.rows
                    if entry and row.get('reader_identity') == entry.get('reader_identity')]
                   or [identity])
        for related_id in related:
            self.library.remove(related_id)
        if cache_identity:
            from epub_reader import prune_book_cache, schedule_prune_book_cache
            if self.storage_context.purpose == 'production':
                schedule_prune_book_cache(cache_identity, self.storage_context.cache_root)
            else:
                prune_book_cache(cache_identity, self.storage_context.cache_root)
        self.shelfChanged.emit()
        self.changed.emit()

    @Property('QVariantList', notify=stateChanged)
    def readerFonts(self):  # noqa: N802
        return [
            {**row, 'label': ui_messages.render(ui_messages.Message(row['label_code']))}
            for row in self._reader_fonts
        ]

    @Property(str, notify=stateChanged)
    def readerFontFamily(self):  # noqa: N802
        return self.settings.value('reader/font_family', 'Songti SC', type=str)

    @readerFontFamily.setter
    def readerFontFamily(self, family):  # noqa: N802
        if any(row['family'] == family for row in self._reader_fonts):
            self.settings.setValue('reader/font_family', family)
            self.changed.emit()

    @Property(bool, notify=stateChanged)
    def readerBilingual(self):  # noqa: N802
        column = self._active_column()
        return column.readerBilingual if column else self.settings.value('reader/bilingual', False, type=bool)

    @readerBilingual.setter
    def readerBilingual(self, value):  # noqa: N802
        column = self._active_column()
        if column:
            column.readerBilingual = value
        else:
            self.settings.setValue('reader/bilingual', bool(value))
        self.changed.emit()

    @Property(str, notify=stateChanged)
    def bilingualMessage(self):  # noqa: N802
        column = self._active_column()
        return column.bilingualMessage if column else ''

    def _refresh_bilingual(self):
        column = self._active_column()
        if column:
            column._refresh_bilingual()

    @Property(int, notify=stateChanged)
    def readerFontSize(self):  # noqa: N802
        return max(15, min(30, int(self.settings.value("reader/font_size", 19, type=int))))

    @readerFontSize.setter
    def readerFontSize(self, value):  # noqa: N802
        self.settings.setValue("reader/font_size", max(15, min(30, int(value))))
        self.changed.emit()

    @Property(bool, notify=stateChanged)
    def readerNight(self):  # noqa: N802
        return self._background_id == "night"

    @readerNight.setter
    def readerNight(self, value):  # noqa: N802
        target = "night" if bool(value) else ("paper" if self._background_id == "night"
                                               else self._background_id)
        self.backgroundId = target

    @Property(float, notify=stateChanged)
    def readingBrightness(self):  # noqa: N802
        """Reader-canvas luminance shared by flow and fixed-layout adapters."""
        try:
            value = float(self.settings.value("reader/brightness", 1.0))
        except (TypeError, ValueError):
            value = 1.0
        return max(0.6, min(1.1, value))

    @readingBrightness.setter
    def readingBrightness(self, value):  # noqa: N802
        try:
            normalized = round(max(0.6, min(1.1, float(value))), 2)
        except (TypeError, ValueError):
            return
        if abs(normalized - self.readingBrightness) < 0.001:
            return
        self.settings.setValue("reader/brightness", normalized)
        self.changed.emit()

    @Property(float, notify=stateChanged)
    def readingLineHeight(self):  # noqa: N802
        try:
            legacy = self.settings.value("reader/epub_line_height", 1.85)
            value = float(self.settings.value("reader/line_height", legacy))
        except (TypeError, ValueError):
            value = 1.85
        return max(1.3, min(2.6, value))

    @readingLineHeight.setter
    def readingLineHeight(self, value):  # noqa: N802
        try:
            normalized = round(max(1.3, min(2.6, float(value))), 2)
        except (TypeError, ValueError):
            return
        if abs(normalized - self.readingLineHeight) < 0.001:
            return
        self.settings.setValue("reader/line_height", normalized)
        # Keep the compatibility key for older installed builds and rollback.
        self.settings.setValue("reader/epub_line_height", normalized)
        self.changed.emit()

    @Property(float, notify=stateChanged)
    def epubLineHeight(self):  # noqa: N802
        return self.readingLineHeight

    @epubLineHeight.setter
    def epubLineHeight(self, value):  # noqa: N802
        self.readingLineHeight = value

    @Property("QVariantList", notify=stateChanged)
    def translationProfiles(self):  # noqa: N802
        rows = self.providers.profiles
        for row in rows:
            if row['id'] in PROFILE_MESSAGE_CODES:
                row['label'] = ui_messages.render(ui_messages.Message(PROFILE_MESSAGE_CODES[row['id']]['label']))
        return rows

    @Property(str, notify=stateChanged)
    def translationProfile(self):  # noqa: N802
        return self._translation_profile

    @translationProfile.setter
    def translationProfile(self, value):  # noqa: N802
        if self.running or self._probe_busy or self._task_locked or not self.providers.contains(value):
            return
        if value != self._translation_profile:
            self._translation_profile = value
            self.providers.select('translation', value)
            self._bound_engine = None
            self._probe_result = ""
            self._probe_status = ui_messages.Message(ui_messages.MessageCode.PROBE_MODEL_CHANGED)
            if self.providers.is_builtin(value):
                self._refresh_local_models()
            self._status_detail = ui_messages.Message(
                ui_messages.MessageCode.READY_WITH_SOURCE, (self._model_display_message(),))
            self.changed.emit()

    @Property(str, notify=stateChanged)
    def modelDisplayName(self):  # noqa: N802
        return ui_messages.render_value(self._model_display_message())

    def _model_display_message(self):
        """Stable active-model label, safe to nest inside another message."""
        if self._translation_profile in PROFILE_MESSAGE_CODES:
            return ui_messages.Message(PROFILE_MESSAGE_CODES[self._translation_profile]['short'])
        if self.providers.contains(self._translation_profile):
            return self.providers.route(self._translation_profile)['label']
        return self.providers.missing_label()

    @Property(bool, notify=stateChanged)
    def modelReady(self):  # noqa: N802
        return self._route_ready(refresh=not self._local_models_checked)

    def _model_status_code(self) -> str:
        """Stable availability code; rendered in the current locale."""
        try:
            route = self.translation_route()
        except ValueError:
            return ui_messages.MessageCode.MODEL_REMOTE_CREDENTIALS_MISSING
        if route['builtin']:
            if self._route_ready(refresh=not self._local_models_checked):
                return ui_messages.MessageCode.MODEL_LOCAL_READY
            return ui_messages.MessageCode.MODEL_LOCAL_NOT_LOADED
        return (ui_messages.MessageCode.MODEL_REMOTE_READY if self.providers.ready(route['id'])
                else ui_messages.MessageCode.MODEL_REMOTE_CREDENTIALS_MISSING)

    @Property(str, notify=stateChanged)
    def modelStatusCode(self):  # noqa: N802
        return self._model_status_code()

    @Property(str, notify=stateChanged)
    def modelStatus(self):  # noqa: N802
        return ui_messages.render(ui_messages.Message(self._model_status_code()))

    @Property(str, notify=stateChanged)
    def themeId(self):  # noqa: N802
        return self._theme_id

    @themeId.setter
    def themeId(self, value):  # noqa: N802
        if value in THEME_IDS and value != self._theme_id:
            self._theme_id = value
            self.settings.setValue("appearance/theme", value)
            self.changed.emit()

    @Property(str, notify=stateChanged)
    def backgroundId(self):  # noqa: N802
        return self._background_id

    @backgroundId.setter
    def backgroundId(self, value):  # noqa: N802
        if value in BACKGROUND_IDS and value != self._background_id:
            self._background_id = value
            self.settings.setValue("appearance/background", value)
            # Keep the rollback key synchronized; it is no longer a second
            # source of truth in this version.
            self.settings.setValue("reader/night", value == "night")
            self.changed.emit()

    @Property(bool, notify=stateChanged)
    def probeBusy(self):  # noqa: N802
        return self._probe_busy

    @Property(str, notify=stateChanged)
    def probeResult(self):  # noqa: N802
        return self._probe_result

    @Property(str, notify=stateChanged)
    def probeStatus(self):  # noqa: N802
        return ui_messages.render_value(self._probe_status)

    @Property(str, notify=stateChanged)
    def probeStatusDetail(self):  # noqa: N802
        """Untranslated probe diagnostic, kept separate from its summary."""
        return (ui_messages.detail_text(self._probe_status)
                if isinstance(self._probe_status, ui_messages.Message) else '')

    def _refresh_local_models(self) -> None:
        self._local_models = ollama_models()
        self._local_models_checked = True

    def _route_ready(self, *, refresh: bool = False) -> bool:
        try:
            route = self.translation_route()
        except ValueError:
            return False
        if not route['builtin']:
            return self.providers.ready(route['id'])
        if refresh:
            self._refresh_local_models()
        return str(route["model"]) in self._local_models

    def translation_route(self) -> dict[str, object]:
        """Return the route bound to the loaded task or current new-task choice."""
        route = self.providers.route(self._translation_profile)
        if self._bound_engine:
            for name in ('api_base', 'model'):
                saved = self._bound_engine.get(name)
                if saved and str(saved) != str(route[name]):
                    raise ValueError(self.providers.mismatched_route())
        return route

    def review_route(self):
        return self.providers.feature_route('review')

    @Slot()
    def refreshModelStatus(self):  # noqa: N802
        self._refresh_local_models()
        self.changed.emit()

    @Slot(str)
    def copyReadingText(self, text):
        QApplication.clipboard().setText(text)

    @Slot(str)
    def probeTranslation(self, source):  # noqa: N802
        if self.running or self._probe_busy:
            return
        source = str(source).strip()
        if not source:
            self._probe_status = ui_messages.Message(ui_messages.MessageCode.PROBE_ENTER_SOURCE)
            self.changed.emit()
            return
        if len(source) > 3000:
            self._probe_status = ui_messages.Message(ui_messages.MessageCode.PROBE_TOO_LONG)
            self.changed.emit()
            return
        if not self._route_ready(refresh=True):
            self._probe_status = ui_messages.Message(self._model_status_code())
            self.changed.emit()
            return
        route = self.translation_route()
        process = QProcess(self)
        arguments = [
                "--api-base",
                str(route["api_base"]),
                "--model",
                str(route["model"]),
                "--auth-provider",
                str(route["auth_provider"]),
                "--auth-path",
                "",
                "--request-timeout",
                "180",
            ]
        try:
            prepare_worker(process, self, 'probe_model.py', arguments,
                           route=route, input_text=source, feature='translation')
        except Exception as exc:
            self._probe_status = str(exc)
            process.deleteLater()
            self.changed.emit()
            return
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONDONTWRITEBYTECODE", "1")
        process.setProcessEnvironment(environment)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_probe)
        process.finished.connect(self._probe_finished)
        process.errorOccurred.connect(self._probe_error)
        self._probe_process = process
        self._probe_buffer = ""
        self._probe_busy = True
        self._probe_result = ""
        self._probe_status = ui_messages.Message(
            ui_messages.MessageCode.PROBE_RUNNING, (self._model_display_message(),))
        process.start()
        if not process.waitForStarted(3000):
            self._probe_busy = False
            self._probe_process = None
            self._probe_status = ui_messages.Message(
                ui_messages.MessageCode.PROCESS_NOT_STARTED, (), process.errorString())
            process.deleteLater()
            self.changed.emit()
            return
        self.changed.emit()

    def _read_probe(self) -> None:
        if self._probe_process:
            self._probe_buffer += bytes(
                self._probe_process.readAllStandardOutput()
            ).decode("utf-8", errors="replace")

    def _probe_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        process = self._probe_process
        self._read_probe()
        result: dict[str, object] = {}
        for line in reversed(self._probe_buffer.splitlines()):
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                result = candidate
                break
        if exit_code == 0 and result.get("ok") is True:
            self._probe_result = str(result.get("translation") or "")
            self._probe_status = ui_messages.Message(
                ui_messages.MessageCode.PROBE_COMPLETE,
                (f"{float(result.get('seconds') or 0):.1f}",))
        else:
            self._probe_status = ui_messages.Message(
                ui_messages.MessageCode.PROBE_FAILED, (), str(result.get("error") or "")[:500])
        self._probe_busy = False
        self._probe_process = None
        self._probe_buffer = ""
        if process:
            process.deleteLater()
        self.changed.emit()

    def _probe_error(self, error: QProcess.ProcessError) -> None:
        if not self._probe_process or error != QProcess.ProcessError.FailedToStart:
            return
        process = self._probe_process
        self._probe_status = ui_messages.Message(
            ui_messages.MessageCode.PROCESS_NOT_STARTED, (), process.errorString()[:500])
        self._probe_busy = False
        self._probe_process = None
        process.deleteLater()
        self.changed.emit()

    @Property(bool, notify=stateChanged)
    def autoGlossary(self):  # noqa: N802
        return self._auto_glossary

    @autoGlossary.setter
    def autoGlossary(self, value):  # noqa: N802
        if self._running or self._task_locked:
            return
        self._auto_glossary = bool(value)
        if value:
            self._glossary_path = ""
        self.changed.emit()

    @Property(bool, notify=stateChanged)
    def taskLocked(self):  # noqa: N802
        return self._task_locked

    @Property(bool, notify=stateChanged)
    def scanning(self):
        return self._scanning

    @Property(float, notify=stateChanged)
    def scanProgress(self):  # noqa: N802
        return self._scan_progress

    @Property(str, notify=stateChanged)
    def glossarySummary(self):  # noqa: N802
        return ui_messages.render_value(self._glossary_summary)

    @Property(int, notify=stateChanged)
    def glossaryPending(self):  # noqa: N802
        return self._glossary_pending

    @Property(str, notify=stateChanged)
    def readerTitle(self):  # noqa: N802
        column = self._active_column()
        return (column.readerTitle if column else ui_messages.render(
            ui_messages.Message(ui_messages.MessageCode.READER_EMPTY_TITLE)))

    @Property(bool, notify=stateChanged)
    def readingExternal(self):  # noqa: N802
        column = self._active_column()
        return column.readingExternal if column else False

    @Property(int, notify=stateChanged)
    def savedReadingPosition(self):  # noqa: N802
        column = self._active_column()
        if column and self._reader_identity and column._reader_identity != self._reader_identity:
            column._reader_identity = self._reader_identity
        return column.savedReadingPosition if column else 0

    @staticmethod
    def _reading_key_for(identity):
        return "reading/" + hashlib.sha256(str(identity).encode()).hexdigest()[:24]

    def _reading_key(self):
        return self._reading_key_for(self._reader_identity)

    def _reading_identity_for_path(self, path):
        """All paths and editions of one hashed source share reading state."""
        path = Path(path).resolve()
        managed = self._managed_reading_job(path)
        if managed:
            row = next((item for item in self.library.rows
                        if item.get('job') and Path(item['job']).resolve() == Path(managed).resolve()), None)
            return str((row or {}).get('reader_identity')
                       or Bookshelf.logical_key('task', managed, self._source_path or str(path)))
        if self._job_path:
            root = Path(self._job_path).resolve()
            if path.is_relative_to(root):
                row = next((item for item in self.library.rows
                            if item.get('job') and Path(item['job']).resolve() == root), None)
                return str((row or {}).get('reader_identity')
                           or Bookshelf.logical_key('task', root, self._source_path or str(path)))
        return Bookshelf.logical_key('text', path)

    def _can_restore_reading(self, path):
        """Reject a managed edition when the selected source points at another task."""
        path = Path(path).resolve()
        if not any(path.is_relative_to(root) for root in self._task_roots()):
            return True
        return bool(self._job_path) and path.is_relative_to(Path(self._job_path).resolve())

    @Slot(int)
    def saveReadingPosition(self, row):  # noqa: N802
        column = self._active_column()
        if column:
            if self._reader_identity and column._reader_identity != self._reader_identity:
                column._reader_identity = self._reader_identity
            column.saveReadingPosition(row)

    @Slot()
    def pickReadingFile(self):  # noqa: N802
        column = self._ensure_column(reader=self.reader)
        column.pickReadingFile()

    @Slot(str, result=bool)
    def openReadingFile(self, raw_path):  # noqa: N802
        path = Path(raw_path).expanduser().resolve()
        column = self._ensure_column(reader=self.reader)
        managed = self._managed_reading_job(path) if path.is_file() else None
        if (managed and path.suffix.lower() != '.epub'
                and (Path(managed) / '.epub-source.json').is_file()):
            # Rebuilding a reviewed EPUB can touch every chapter. Keep that work
            # off the GUI thread while retaining the verified TXT as the stable
            # edition/ledger identity persisted by the reading column.
            self.openPathInColumnAsync(column.columnId, str(path))
            return True
        self.openPathInColumn(column.columnId, str(path))
        return bool(column.readingPath and Path(column.readingPath).resolve() == path)

    @Slot()
    def followTranslation(self):  # noqa: N802
        column = self._ensure_column(reader=self.reader)
        self.followTranslationInColumn(column.columnId)

    @Slot()
    def followTranslationAsync(self):  # noqa: N802
        column = self._ensure_column(reader=self.reader)
        self.followTranslationInColumnAsync(column.columnId)

    def _refresh_reader(self):
        column = self._active_column()
        if column and column.empty and self._output_path and not column._manual_empty:
            job = canonical_path(self._job_path) if self._job_path else ''
            output = Path(self._output_path)
            kind = 'task' if job else 'text'
            identity_path = job or output
            identity_source = self._source_path or str(output)
            column._book = {
                'id': Bookshelf.key(kind, identity_path, identity_source),
                'kind': kind,
                'title': Path(self._source_name).stem or output.stem,
                'source': identity_source,
                'job': job,
                'reading_file': '',
            }
            column._book['reader_identity'] = Bookshelf.logical_key(
                kind, identity_path, identity_source)
            column._reader_identity = column._book['reader_identity']
            column._read_file = ''
            self._sync_owner_from_column(column)
        self._refresh_readers()

    @Slot()
    def openGlossary(self):  # noqa: N802
        if not self._job_path:
            return
        root = Path(self._job_path)
        target = root / ("术语表.auto.json" if self._auto_glossary else "术语表.json")
        if target.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    @Slot()
    def openGlossaryIssues(self):  # noqa: N802
        if self._job_path:
            target = Path(self._job_path) / '译名待核对.json'
            if target.is_file():
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    @Slot()
    def newEdition(self):  # noqa: N802
        if self.running or not self._source_path:
            return
        self.readingAboutToChange.emit()
        self._save_book_session()
        self._read_file = self._reader_identity = ''
        self._bilingual_signature = None
        self.settings.remove('last_read_file')
        source = Path(self._source_path)
        self._new_job_path = str(self.workspace / f"{safe_stem(source.stem)}-{file_digest(source)[:10]}-新译本-{uuid.uuid4().hex[:6]}")
        self._job_path = self._new_job_path
        self.settings.setValue('last_job', self._job_path)
        self._output_path = ""
        self._progress = self._elapsed = self._speed = 0.0
        self._auto_glossary = self._task_locked = self._finished = self._scanning = False
        self._bound_engine = None
        self._glossary_path = self._glossary_summary = ""
        self._error = ui_messages.Message("")
        self._glossary_pending = 0
        # A separate edition starts from the default direction; the old task's
        # saved direction stays with the old task.
        self._reset_language_state()
        self._source_info = self._source_info_text()
        self._status_title = ui_messages.Message(ui_messages.MessageCode.NEW_EDITION_READY)
        self._status_detail = ui_messages.Message(
            ui_messages.MessageCode.READY_WITH_SOURCE, (self._model_display_message(),))
        self._active_book = self.library.remember(
            source, job=self._job_path, enrich=False)
        self._register_task_tab()
        entry = next((row for row in self.library.rows
                      if row['id'] == self._active_book), None)
        self._submit_shelf_metadata(entry)
        if not self._read_file:
            self.reader.clear()
        self.shelfChanged.emit()
        self.changed.emit()

    @Property(str, notify=stateChanged)
    def sourcePath(self) -> str:  # noqa: N802
        return self._source_path

    @Property(str, notify=stateChanged)
    def sourceName(self) -> str:  # noqa: N802
        return self._source_name

    @Property(str, notify=stateChanged)
    def sourceInfo(self) -> str:  # noqa: N802
        return ui_messages.render_value(self._source_info)

    @Property(str, notify=stateChanged)
    def modelProfileLabel(self) -> str:  # noqa: N802
        """Localized display name of the active route."""
        return self.modelDisplayName

    @Property(str, notify=stateChanged)
    def languageLabel(self) -> str:  # noqa: N802
        return ui_messages.render_value(self._language_label)

    @Property(str, notify=stateChanged)
    def languageNotice(self) -> str:  # noqa: N802
        return ui_messages.render_value(self._language_notice)

    @Property(str, notify=stateChanged)
    def sourceLanguage(self) -> str:  # noqa: N802
        """The manual source choice; empty while automatic detection is on."""
        return "" if self._source_language_auto else self._source_language

    @Property(str, notify=stateChanged)
    def detectedLanguage(self) -> str:  # noqa: N802
        return str((self._language_detection or {}).get("language") or "")

    @Property(str, notify=stateChanged)
    def targetLanguage(self) -> str:  # noqa: N802
        return self._target_language

    @Property(bool, notify=stateChanged)
    def languageAuto(self) -> bool:  # noqa: N802
        return self._source_language_auto

    @Property(bool, notify=stateChanged)
    def languageLocked(self) -> bool:  # noqa: N802
        return bool(self._task_locked)

    @Property('QVariantList', constant=True)
    def languageCommon(self):  # noqa: N802
        """Legacy Chinese name list; new UI binds to the structured options."""
        return list(COMMON_LANGUAGES)

    @Property('QVariantList', notify=stateChanged)
    def languageSourceOptions(self):  # noqa: N802
        """Stable IDs, localized actions, and unchanged native language names."""
        return self.locale.localize_language_options(language_source_options())

    @Property('QVariantList', notify=stateChanged)
    def languageTargetOptions(self):  # noqa: N802
        """Structured target picker; ``auto`` is never a target."""
        return self.locale.localize_language_options(language_target_options())

    @Property(str, notify=stateChanged)
    def sourceLanguageId(self) -> str:  # noqa: N802
        """Stable picker value for the current source selection."""
        return language_option_value(
            self._source_language, automatic=self._source_language_auto)

    @Property(str, notify=stateChanged)
    def targetLanguageId(self) -> str:  # noqa: N802
        """Stable picker value for the current target selection."""
        return language_option_value(self._target_language)

    def _on_locale_changed(self) -> None:
        """Refresh projected state after an in-place locale switch.

        Only signals are emitted; the running worker, the QML engine, the
        reader columns and every cache generation stay exactly as they were.
        """
        self.changed.emit()
        self.shelfChanged.emit()
        self.post_editor.changed.emit()
        for column in self._reading_columns:
            column.changed.emit()
            column.reader.metaChanged.emit()
            column.epub.changed.emit()
            if column._epub_peer is not None:
                column._epub_peer.changed.emit()
                column._epub_peer.reader.metaChanged.emit()
                column._epub_peer.epub.changed.emit()
        if hasattr(self, 'research'):
            self.research.changed.emit()
        self.providers.changed.emit()
        if hasattr(self, 'data_settings'):
            self.data_settings.changed.emit()

    # ----------------------------------------------------------------- locale

    @Property(str, notify=stateChanged)
    def uiLocale(self) -> str:  # noqa: N802
        """Stored interface preference: ``system``, ``zh-CN`` or ``en``."""
        return self.locale.preference

    @Property(str, notify=stateChanged)
    def effectiveUiLocale(self) -> str:  # noqa: N802
        """Interface locale actually in force: ``zh-CN`` or ``en``."""
        return self.locale.effective

    @Property('QVariantList', notify=stateChanged)
    def localeOptions(self):  # noqa: N802
        """Structured interface-language picker, refreshed after a switch."""
        return self.locale.options()

    @Property(str, notify=stateChanged)
    def localeLabel(self) -> str:  # noqa: N802
        """Localized label naming the interface language in force."""
        return self.locale.display_label()

    @Property(str, notify=stateChanged)
    def localeNotice(self) -> str:  # noqa: N802
        """Explanation when a requested locale resource was unusable."""
        return self.locale.notice

    @Slot(str, result=bool)
    def setUiLocale(self, value) -> bool:  # noqa: N802
        """Switch the interface language without rebuilding product state."""
        return self.locale.set_locale(value)

    @staticmethod
    def _requested_language(raw) -> str:
        """Adapter: accept a stable ID, a native name or a legacy value.

        ``auto``/``custom`` are picker actions, not languages, so they can
        never be stored as a translation value.
        """
        value = str(raw or "").strip()
        if value in (AUTO_ID, CUSTOM_ID):
            return ""
        return language(value)

    @Slot(str, str, bool, result=bool)
    def setLanguageDirection(self, source, target, automatic) -> bool:  # noqa: N802
        """Apply the direction shown on the card; never rewrite a checkpointed task."""
        if self._running or self.post_editor.busy:
            return False
        target_language = self._requested_language(target)
        if not target_language or target_language == AUTO_LANGUAGE:
            self._language_notice = ui_messages.Message(
                ui_messages.MessageCode.TARGET_LANGUAGE_INVALID)
            self.changed.emit()
            return False
        source_language = "" if automatic else self._requested_language(source)
        if not automatic and not source_language:
            self._language_notice = ui_messages.Message(
                ui_messages.MessageCode.SOURCE_LANGUAGE_INVALID)
            self.changed.emit()
            return False
        if self._task_locked:
            self._language_notice = ui_messages.Message(
                ui_messages.MessageCode.DIRECTION_CHECKPOINT_LOCKED)
            self.changed.emit()
            return False
        self._source_language_auto = bool(automatic)
        self._source_language = source_language
        self._target_language = target_language
        self._language_notice = self._language_status_notice()
        self._language_label = self._language_state_label()
        self._source_info = self._source_info_text()
        self._error = ui_messages.Message("")
        self._save_language_selection()
        self.changed.emit()
        return True

    @Slot(str, str, bool, result=bool)
    def newEditionWithLanguage(self, source, target, automatic) -> bool:  # noqa: N802
        """Create a separate translation task that starts from this direction."""
        if self._running or self.post_editor.busy or not self._source_path:
            return False
        target_language = self._requested_language(target)
        if not target_language or target_language == AUTO_LANGUAGE:
            self._language_notice = ui_messages.Message(
                ui_messages.MessageCode.TARGET_LANGUAGE_INVALID)
            self.changed.emit()
            return False
        source_language = "" if automatic else self._requested_language(source)
        if not automatic and not source_language:
            self._language_notice = ui_messages.Message(
                ui_messages.MessageCode.SOURCE_LANGUAGE_INVALID)
            self.changed.emit()
            return False
        self.newEdition()
        self._source_language_auto = bool(automatic)
        self._source_language = source_language
        self._target_language = target_language
        self._language_notice = self._language_status_notice()
        self._language_label = self._language_state_label()
        self._source_info = self._source_info_text()
        self._error = ui_messages.Message("")
        self._save_language_selection()
        self.changed.emit()
        return True

    @Property(str, notify=stateChanged)
    def glossaryPath(self) -> str:  # noqa: N802
        return self._glossary_path

    @Property(str, notify=stateChanged)
    def glossaryName(self) -> str:  # noqa: N802
        return (Path(self._glossary_path).name if self._glossary_path else
                ui_messages.render(ui_messages.Message(ui_messages.MessageCode.GLOSSARY_NONE)))

    @Property(str, notify=stateChanged)
    def instructions(self) -> str:
        return self._instructions

    @instructions.setter
    def instructions(self, value: str) -> None:
        if self._running or self._task_locked:
            return
        value = value[:1000]
        if value != self._instructions:
            self._instructions = value
            self.changed.emit()

    @Property(bool, notify=stateChanged)
    def speedMode(self) -> bool:  # noqa: N802
        return self._speed_mode

    @speedMode.setter
    def speedMode(self, value: bool) -> None:  # noqa: N802
        if self._running or self._task_locked:
            return
        value = bool(value)
        if value != self._speed_mode:
            self._speed_mode = value
            self.settings.setValue("speed_mode", value)
            self.changed.emit()

    @Property(str, notify=stateChanged)
    def jobPath(self) -> str:  # noqa: N802
        return self._job_path

    @Property(str, notify=stateChanged)
    def outputPath(self) -> str:  # noqa: N802
        return self._output_path

    @Property(str, notify=stateChanged)
    def statusTitle(self) -> str:  # noqa: N802
        """Rendered in the current locale from the stored status message."""
        # Plain strings remain a compatibility path for state restored from an
        # older controller. ``render_value`` preserves those strings verbatim.
        return ui_messages.render_value(self._status_title)

    @Property(str, notify=stateChanged)
    def statusDetail(self) -> str:  # noqa: N802
        return ui_messages.render_value(self._status_detail)

    @Property(str, notify=stateChanged)
    def errorMessage(self) -> str:  # noqa: N802
        """Localized error summary; the diagnostic is exposed separately."""
        return ui_messages.render(self._error)

    @Property(str, notify=stateChanged)
    def errorDetail(self) -> str:  # noqa: N802
        """The untranslated diagnostic exactly as the worker produced it."""
        return ui_messages.detail_text(self._error)

    @Property(str, notify=stateChanged)
    def errorCode(self) -> str:  # noqa: N802
        """Stable failure code for the current error, ``""`` when unclassified."""
        return self._error.code

    @property
    def _error_message(self):
        """Compatibility bridge for older mixins; new code stores ``_error``."""
        return ui_messages.summary_with_detail(self._error)

    @_error_message.setter
    def _error_message(self, value):
        self._error = ui_messages.legacy_message(value)

    @Property(float, notify=stateChanged)
    def progress(self) -> float:
        return self._progress

    @Property(str, notify=stateChanged)
    def progressText(self) -> str:  # noqa: N802
        return f"{self._progress * 100:.1f}%"

    @Property(str, notify=stateChanged)
    def elapsedText(self) -> str:  # noqa: N802
        return clock_text(self._elapsed)

    @Property(str, notify=stateChanged)
    def speedText(self) -> str:  # noqa: N802
        if self._speed <= 0:
            return ui_messages.render(ui_messages.Message(ui_messages.MessageCode.SPEED_CALCULATING))
        return ui_messages.render(ui_messages.Message(
            ui_messages.MessageCode.SPEED_RATE, (f"{self._speed:,.0f}",)))

    @Property(bool, notify=stateChanged)
    def hasSource(self) -> bool:  # noqa: N802
        return bool(self._source_path)

    @Property(bool, notify=stateChanged)
    def hasGlossary(self) -> bool:  # noqa: N802
        return bool(self._glossary_path)

    @Property(bool, notify=stateChanged)
    def running(self) -> bool:
        return self._running or self.post_editor.busy

    @Property(bool, notify=stateChanged)
    def finished(self) -> bool:
        return self._finished

    @Property(bool, notify=stateChanged)
    def authReady(self) -> bool:  # noqa: N802
        # Kept for QML/tests from earlier releases; now means selected route ready.
        return self.modelReady

    @Property(bool, notify=stateChanged)
    def canStart(self) -> bool:  # noqa: N802
        return bool(self._source_path) and self.modelReady and not self.running

    @Slot()
    def pickSource(self) -> None:  # noqa: N802
        if self.running:
            return
        path, _ = choose_open_file(
            None,
            ui_messages.render(ui_messages.Message(ui_messages.MessageCode.DIALOG_PICK_SOURCE)),
            str(Path(self._source_path).parent if self._source_path else Path.home() / "Downloads"),
            ui_messages.render(ui_messages.Message(ui_messages.MessageCode.DIALOG_SOURCE_FILTER)),
        )
        if path:
            # This action belongs to the translation workspace.  EPUB files
            # can also be opened in the reader, but choosing one from the
            # source card must still replace the translation source.
            try:
                selected = self.file_access.selected(path, read_only=True)
                self._select_source(selected, reader_async=True)
            except OSError as exc:
                self._error = ui_messages.message_from_worker_error(exc)
                self.changed.emit()

    @Slot()
    def pickShelfBook(self) -> None:  # noqa: N802
        """Add a directly readable book without changing the translation task."""
        if self.running:
            return
        column = self._ensure_column()
        directory = str(Path(column.readingPath).parent) if column.readingPath else str(Path.home() / "Downloads")
        path, _ = choose_open_file(
            None,
            ui_messages.render(ui_messages.Message(ui_messages.MessageCode.DIALOG_ADD_BOOK)),
            directory,
            ui_messages.render(ui_messages.Message(ui_messages.MessageCode.DIALOG_READING_FILTER)),
        )
        if path:
            try:
                selected = self.file_access.selected(path, read_only=True)
                self.importShelfBookInColumnAsync(column.columnId, str(selected))
            except OSError as exc:
                self._error = ui_messages.message_from_worker_error(exc)
                self.changed.emit()

    @Slot(str)
    def acceptDrop(self, raw_url: str) -> None:  # noqa: N802
        if self.running:
            return
        path = Path(QUrl(raw_url).toLocalFile()).expanduser()
        if path.is_file():
            # This drop target is the source card, so all supported formats
            # have the same replace-source semantics as the button above.
            try:
                self._select_source(self.file_access.selected(path, read_only=True), reader_async=True)
            except OSError as exc:
                self._error = ui_messages.message_from_worker_error(exc)
                self.changed.emit()

    @Slot(str)
    def prepareEpubTranslation(self, path):
        if not self.running and Path(path).suffix.lower() == '.epub':
            self._select_source(Path(path), reader_async=True)

    @Slot(str)
    def openEpubOriginal(self, job):
        column = self._active_column()
        if column and column.readingJobPath == job:
            column.setEpubMode('bilingual')

    @Slot()
    def clearSource(self) -> None:  # noqa: N802
        if self.running:
            return
        self._save_book_session()
        self._source_path = ""
        self._source_name = ""
        self._source_info = ui_messages.Message(ui_messages.MessageCode.SOURCE_FORMATS)
        self._reset_language_state()
        self._job_path = ""
        self._output_path = ""
        self._progress = 0.0
        self._finished = False
        self._error = ui_messages.Message("")
        self._auto_glossary = self._task_locked = self._scanning = False
        self._bound_engine = None
        self._new_job_path = self._glossary_path = self._glossary_summary = ""
        self._glossary_pending = 0
        self._instructions = ""
        if not self._read_file and not self._current_tab():
            self.reader.clear()
        self.settings.remove("last_source")
        self.changed.emit()

    @Slot()
    def pickGlossary(self) -> None:  # noqa: N802
        if self._running or self._task_locked:
            return
        path, _ = choose_open_file(
            None,
            ui_messages.render(ui_messages.Message(ui_messages.MessageCode.DIALOG_PICK_GLOSSARY)),
            str(Path(self._source_path).parent if self._source_path else Path.home() / "Downloads"),
            ui_messages.render(ui_messages.Message(ui_messages.MessageCode.DIALOG_GLOSSARY_FILTER)),
        )
        if not path:
            return
        try:
            path = self.file_access.selected(path, read_only=True)
            entries = read_glossary(Path(path))
            if not entries:
                raise ValueError("术语表不能为空")
            self._glossary_path = str(Path(path).resolve())
            self._auto_glossary = False
            self._error = ui_messages.Message("")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            self._error = ui_messages.message_from_worker_error(exc)
        self.changed.emit()

    @Slot()
    def clearGlossary(self) -> None:  # noqa: N802
        if not self._running and not self._task_locked:
            self._glossary_path = ""
            self.changed.emit()

    @Slot()
    def startTranslation(self) -> None:  # noqa: N802
        if self.running:
            return
        try:
            project = self._prepare_job()
            # Preparing the directory persists its task-edition id. Replace
            # the provisional pre-start shelf id before the worker launches.
            self._active_book = self.library.remember(
                self._source_path, job=str(project), progress=self._progress,
                enrich=False)
            self._register_task_tab()
            self._launch_engine(project)
        except Exception as exc:  # keep the GUI alive on malformed books/config
            if isinstance(exc, LanguageDetectionError):
                # Surface the uncertainty on the card itself so the manual
                # source picker is the obvious next step.
                self._language_detection = dict(exc.detection or self._language_detection)
                self._language_notice = ui_messages.Message(
                    ui_messages.MessageCode.LANGUAGE_UNCERTAIN)
            self._error = ui_messages.message_from_worker_error(exc)
            self._status_title = ui_messages.Message(ui_messages.MessageCode.CANNOT_START)
            self._status_detail = ui_messages.Message(
                ui_messages.MessageCode.PREPARE_FAILED)
            self.changed.emit()

    @Slot()
    def pauseTranslation(self) -> None:  # noqa: N802
        if self.post_editor.busy:
            self.post_editor.stop()
            return
        if not self._process or self._process.state() == QProcess.ProcessState.NotRunning:
            return
        process = self._process
        self._pausing = True
        self._status_title = ui_messages.Message(ui_messages.MessageCode.PAUSING)
        self._status_detail = ui_messages.Message(ui_messages.MessageCode.PAUSE_REQUESTED)
        self.changed.emit()
        process.terminate()
        QTimer.singleShot(5000, lambda target=process: self._kill_if_needed(target))

    @Slot()
    def shutdown(self) -> None:
        """Stop the engine before the window/controller disappears."""
        self.research.shutdown()
        self.ask_ai.shutdown()
        self._save_book_session()
        self.post_editor.shutdown()
        self.timer.stop()
        self.reader_timer.stop()
        self._shutdown_reader_loader()
        probe = self._probe_process
        if probe and probe.state() != QProcess.ProcessState.NotRunning:
            probe.terminate()
            if not probe.waitForFinished(1500):
                probe.kill()
                probe.waitForFinished(1000)
        self._probe_process = None
        self._probe_busy = False
        process = self._process
        if not process or process.state() == QProcess.ProcessState.NotRunning:
            self.file_access.close()
            return
        self._pausing = True
        process.terminate()
        if not process.waitForFinished(3000):
            process.kill()
            process.waitForFinished(2000)
        self._running = False
        self.file_access.close()

    @Slot()
    def openOutput(self) -> None:  # noqa: N802
        path = self._output_target()
        if path is not None and path.is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        elif path is not None and path.parent.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent)))

    @Slot()
    def revealOutput(self) -> None:  # noqa: N802
        path = self._output_target()
        job = Path(self._job_path) if self._job_path else None
        target = path if path is not None and path.exists() else job
        if target is not None and target.exists():
            reveal_file(target)

    def _output_target(self) -> Path | None:
        """Return the managed output, recovering it after summary-only startup."""
        if not self._output_path and self._job_path:
            project = Path(self._job_path)
            task = project / "翻译任务.yaml"
            if task.is_file():
                try:
                    config = yaml.safe_load(task.read_text(encoding="utf-8"))
                    if isinstance(config, dict):
                        self._output_path = self._managed_output(project, config)
                        self.changed.emit()
                except (OSError, ValueError, yaml.YAMLError):
                    pass
        if not self._output_path:
            return None
        path = Path(self._output_path)
        epub = path.with_name(path.name.removesuffix('.partial')).with_suffix('.epub')
        return epub if epub.is_file() else path

    @Slot()
    def dismissError(self) -> None:  # noqa: N802
        self._error = ui_messages.Message("")
        self.changed.emit()

    def _select_source(self, path: Path, *, inspect: bool = True,
                       project: Path | None = None, reader_async: bool = False) -> None:
        if self._running or self.post_editor.busy:
            return
        try:
            path = self.file_access.ensure(path)
        except OSError as exc:
            self._error = ui_messages.message_from_worker_error(exc)
            self.changed.emit()
            return
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            self._error = ui_messages.Message(ui_messages.MessageCode.FILE_MISSING,
                                              (), "请选择 TXT、Markdown、RTF、Word、EPUB 或带文字层的 PDF。")
            self.changed.emit()
            return
        previous_source = Path(self._source_path).resolve() if self._source_path else None
        self.readingAboutToChange.emit()
        self._save_book_session()
        switching_book = inspect and (previous_source != path or bool(self._read_file)
                                     or (project is not None and str(project) != self._job_path))
        if switching_book:
            # Translation-task selection must not clear any visible reading
            # column. The selected task gets its own column below.
            self._read_file = ""
            self._reader_identity = ""
            self.settings.remove('last_read_file')
        self._bilingual_signature = None
        self._source_path = str(path)
        try:
            source_digest = file_digest(path)
        except OSError:
            source_digest = ''
        self._new_job_path = self._glossary_path = self._glossary_summary = ""
        self._glossary_pending = 0
        self._auto_glossary = self._task_locked = self._scanning = False
        self._bound_engine = None
        self._instructions = ""
        preferred_job = self._job_path
        self._job_path = self._output_path = ""
        self._speed = self._elapsed = 0.0
        self._source_name = path.name
        self._load_language_selection(source_digest)
        self._source_info = self._source_info_text()
        self._error = ui_messages.Message("")
        self._finished = False
        self._progress = 0.0
        self._status_title = ui_messages.Message(ui_messages.MessageCode.READY)
        self._status_detail = ui_messages.Message(
            ui_messages.MessageCode.READY_WITH_SOURCE, (self._model_display_message(),))
        self.settings.setValue("last_source", str(path))
        inspect_in_background = bool(
            reader_async and inspect and path.stat().st_size <= 30 * 1024 * 1024)
        if inspect and not inspect_in_background and path.stat().st_size <= 30 * 1024 * 1024:
            try:
                text = extract_source_text(path)
                self._apply_detected_language(detect_language(text[:200_000]))
                self._source_info = ui_messages.Message(
                    ui_messages.MessageCode.SOURCE_INSPECTED,
                    (human_size(path.stat().st_size), f"{len(text):,}"))
            except Exception as exc:
                self._error = ui_messages.message_from_worker_error(str(exc)[:500])
        if project is not None and project.parent in self._task_roots():
            self._new_job_path = str(project)
        self._load_existing_job_hint(path, preferred_job=preferred_job, digest=source_digest)
        self.settings.setValue('last_job', self._job_path)
        self._active_book = self.library.remember(
            path, job=self._job_path, progress=self._progress,
            restore=self._restoring, enrich=False)
        entry = next((row for row in self.library.rows if row['id'] == self._active_book), {})
        saved_read = self._reading_file_for_row(entry) if entry else ''
        if saved_read and Path(saved_read).is_file() and Path(saved_read).is_relative_to(Path(self._job_path)):
            self._read_file = saved_read
        elif self._job_path:
            current_editions = sorted((Path(self._job_path) / '译后校对').glob('*.当前阅读版.txt'))
            if current_editions:
                self._read_file = str(current_editions[-1])
        if self._read_file:
            self.settings.setValue('last_read_file', self._read_file)
        if not self._restoring:
            self._register_task_tab(async_load=reader_async)
            self._submit_shelf_metadata(entry)
            if inspect_in_background:
                self._submit_source_inspection(path)
        self.shelfChanged.emit()
        self.changed.emit()

    def _job_for_source(self, source: Path, digest='') -> Path:
        if self._new_job_path:
            return Path(self._new_job_path)
        digest_prefix = str(digest or file_digest(source))[:10]
        saved = self.settings.value("editions/" + digest_prefix, "", type=str)
        if saved:
            path = Path(saved).resolve()
            if path.parent in self._task_roots() and path.is_dir():
                return path
        return self.workspace / f"{safe_stem(source.stem)}-{digest_prefix}"

    def _load_existing_job_hint(self, source: Path, *, preferred_job='', digest='') -> None:
        digest = digest or file_digest(source)
        project = Path(self._new_job_path) if self._new_job_path else None
        if project is None:
            for row in self.library.task_candidates_for_hash(
                    digest, preferred_job=preferred_job):
                candidate = Path(row.get('job') or '').resolve()
                if (candidate.parent in self._task_roots()
                        and (candidate / '翻译任务.yaml').is_file()):
                    project = candidate
                    break
        if project is None:
            project = self._job_for_source(source, digest)
        self._job_path = str(project)
        progress_path = project / "翻译进度.json"
        if not (project / "翻译任务.yaml").is_file():
            return
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8")) if progress_path.is_file() else {}
            total_chars = int(progress.get("total_source_chars") or 0)
            completed_chars = int(progress.get("completed_source_chars") or 0)
            if total_chars:
                self._progress = min(1.0, completed_chars / total_chars)
            self._status_title = ui_messages.Message(ui_messages.MessageCode.RESUMABLE_TASK)
            self._status_detail = ui_messages.Message(
                ui_messages.MessageCode.RESUMABLE_PROGRESS,
                (f"{self._progress * 100:.1f}",))
            config = yaml.safe_load((project / "翻译任务.yaml").read_text(encoding="utf-8"))
            if isinstance(config, dict):
                self._restore_task_options(project, config)
                self._output_path = self._managed_output(project, config)
                self._finished = progress.get("status") == "completed" and self._progress >= 1.0
                if self._finished:
                    self._status_title = ui_messages.Message(ui_messages.MessageCode.FINISHED_TASK)
                    self._status_detail = ui_messages.Message(ui_messages.MessageCode.FINISHED_READY)
        except (OSError, ValueError, json.JSONDecodeError, yaml.YAMLError):
            return

    def _restore_task_options(self, project: Path, config: dict):
        self._task_locked = self._has_checkpoint(project)
        engine = config.get("engine") or {}
        profile = str(engine.get("profile") or DEFAULT_MODEL_PROFILE)
        self._translation_profile = profile
        self._bound_engine = dict(engine) if self._task_locked else None
        mode = (config.get("glossary") or {}).get("mode", "none")
        self._auto_glossary = mode == "auto"
        self._glossary_path = str(inside(project, config["glossary"].get("file") or "术语表.json")) if mode == "fixed" else ""
        self._speed_mode = int((config.get("chunking") or {}).get("parallel_requests") or 1) == 2
        saved_languages = config.get("languages") or {}
        self._source_language = language(str(saved_languages.get("source") or ""))
        self._target_language = language(str(saved_languages.get("target") or "")) or "简体中文"
        # A task file stores the direction handed to the translator as a real
        # language, so the card shows that saved direction rather than "auto".
        self._source_language_auto = False
        self._language_label = ui_messages.Message(
            ui_messages.MessageCode.LANGUAGE_DIRECTION,
            (self._source_language, self._target_language))
        self._language_notice = ""
        # sourceInfo stays a size/character line; the background inspection owns
        # its text so a slow file never blocks the card on the GUI thread.
        self._instructions = "；".join((config.get("translation") or {}).get("special_requirements") or [])
        artifact = project / "术语表.auto.json"
        if mode == "auto" and artifact.is_file():
            data = json.loads(artifact.read_text(encoding="utf-8"))
            self._glossary_summary = ui_messages.Message(
                ui_messages.MessageCode.GLOSSARY_READY,
                (len(data.get('entries') or []),))
            self._glossary_pending = int(data.get('pending_review') or 0)
        elif mode == "auto":
            self._glossary_summary = ui_messages.Message(
                ui_messages.MessageCode.GLOSSARY_PREPARE)
            index_file, scan_file = project / '译名候选.json', project / '.hy-name-review.json'
            if index_file.exists() and scan_file.exists():
                total = (len(json.loads(index_file.read_text()).get('candidates', [])) + 7) // 8
                completed = len(json.loads(scan_file.read_text()).get('batches', {}))
                self._scan_progress = completed / total if total else 0.0
                self._scanning = True
                self._status_title = ui_messages.Message(ui_messages.MessageCode.GLOSSARY_PAUSED)
                self._status_detail = ui_messages.Message(
                    ui_messages.MessageCode.GLOSSARY_PROGRESS, (completed, total))

    @staticmethod
    def _has_checkpoint(project: Path) -> bool:
        return any((project / name).is_file() for name in
                   ("译文/.hy-direct-state.json", ".hy-name-review.json", ".hy-glossary-scan.json"))

    def _reset_language_state(self) -> None:
        self._source_language = ""
        self._source_language_auto = True
        self._target_language = "简体中文"
        self._language_detection = {}
        self._language_notice = ""
        self._language_label = self._language_state_label()

    def _language_state_label(self):
        target = self._target_language or "简体中文"
        if not self._source_language_auto and self._source_language:
            return ui_messages.Message(
                ui_messages.MessageCode.LANGUAGE_DIRECTION,
                (self._source_language, target))
        detected = self.detectedLanguage
        if detected:
            return ui_messages.Message(
                ui_messages.MessageCode.LANGUAGE_DIRECTION_DETECTED,
                (detected, target))
        return ui_messages.Message(
            ui_messages.MessageCode.LANGUAGE_DIRECTION_AUTO, (target,))

    def _language_status_notice(self) -> str:
        """Explain uncertainty or an obvious script clash without guessing."""
        if not self._source_path:
            return ""
        detection = self._language_detection or {}
        if not self._source_language_auto and self._source_language:
            scripts = language_scripts(self._source_language)
            script = str(detection.get("script") or "")
            if scripts and script not in ("", "unknown") and script not in scripts:
                shown = detection.get("language") or "另一种文字"
                return ui_messages.Message(
                    ui_messages.MessageCode.LANGUAGE_SCRIPT_WARNING,
                    (shown, self._source_language))
            return ""
        if detection.get("uncertain"):
            # Detection details are domain diagnostics generated in Chinese.
            # The card owns a localized summary; the semantic detection dict
            # remains unchanged for inspection and task validation.
            return ui_messages.Message(ui_messages.MessageCode.LANGUAGE_UNCERTAIN)
        return ""

    def _source_info_text(self) -> str:
        if not self._source_path:
            return ui_messages.Message(ui_messages.MessageCode.SOURCE_FORMATS)
        try:
            size = human_size(Path(self._source_path).stat().st_size)
        except OSError:
            size = ""
        if self._source_language_auto:
            return ui_messages.Message(
                ui_messages.MessageCode.SOURCE_ROUTE_AUTO,
                (size, self._target_language or '简体中文'))
        return ui_messages.Message(
            ui_messages.MessageCode.SOURCE_ROUTE,
            (size, self._source_language, self._target_language or '简体中文'))

    def _apply_detected_language(self, detection) -> None:
        """Record a background detection result; a manual choice always wins."""
        if not isinstance(detection, dict) or not detection:
            return
        self._language_detection = dict(detection)
        self._language_label = self._language_state_label()
        self._language_notice = self._language_status_notice()

    def _language_setting_key(self, digest: str = "") -> str:
        value = digest[:10] if digest else ""
        if not value:
            if not self._source_path:
                return ""
            try:
                value = file_digest(Path(self._source_path))[:10]
            except OSError:
                return ""
        return "language/" + value

    def _save_language_selection(self) -> None:
        key = self._language_setting_key()
        if not key:
            return
        self.settings.setValue(key, json.dumps({
            "source": self._source_language,
            "target": self._target_language,
            "auto": self._source_language_auto,
        }, ensure_ascii=False))

    def _load_language_selection(self, digest: str = "") -> None:
        """Restore this book's own choice so switching books never leaks one."""
        self._reset_language_state()
        key = self._language_setting_key(digest)
        raw = self.settings.value(key, "", type=str) if key else ""
        if not raw:
            return
        try:
            saved = json.loads(raw)
        except (TypeError, ValueError):
            return
        if not isinstance(saved, dict):
            return
        self._target_language = language(str(saved.get("target") or "")) or "简体中文"
        if self._target_language == AUTO_LANGUAGE:
            self._target_language = "简体中文"
        chosen = language(str(saved.get("source") or ""))
        self._source_language_auto = bool(saved.get("auto", not chosen))
        self._source_language = "" if self._source_language_auto else chosen
        self._language_label = self._language_state_label()

    def _prepare_job(self) -> Path:
        route = self.translation_route()
        self.providers.require_consent('translation', route['id'])
        source = Path(self._source_path)
        if not source.is_file():
            raise ValueError("请先选择可用的原文文件")
        project = self._job_for_source(source)
        project.mkdir(parents=True, exist_ok=True)
        if self._has_checkpoint(project):
            config = yaml.safe_load((project / "翻译任务.yaml").read_text(encoding="utf-8"))
            validate_task(project, config)
            self._restore_task_options(project, config)
            self._status_detail = ui_messages.Message(ui_messages.MessageCode.RESUMING)
        else:
            route = self.translation_route()
            spec: dict[str, object] = {
                "source_files": [str(source)],
                "source_language": self._source_language,
                "source_language_explicit": bool(
                    not self._source_language_auto and self._source_language),
                "target_language": self._target_language,
                "target_language_explicit": True,
                "glossary_mode": "auto" if self._auto_glossary else "fixed" if self._glossary_path else "none",
                "native_defaults": True,
                "requirements": [self._instructions.strip()] if self._instructions.strip() else [],
            }
            if self._auto_glossary:
                spec["glossary_strategy"] = "candidates-v1"
            if self._instructions.strip():
                spec["requirements"] = [self._instructions.strip()]
            if self._glossary_path:
                spec["glossary_file"] = self._glossary_path
            prepare_task(project, spec)
            config = yaml.safe_load((project / "翻译任务.yaml").read_text(encoding="utf-8"))
            chunking = config.setdefault("chunking", {})
            if route["local"]:
                # Smaller local models are steadier on shorter literary chunks;
                # a single Ollama runner also gains little from two queued calls.
                chunking["parallel_requests"] = 1
                small_model = self._translation_profile == 'local_1_8b'
                chunking["max_source_chars"] = min(
                    int(chunking.get("max_source_chars") or 4000), 1400 if small_model else 2400
                )
                chunking["max_segments"] = min(
                    int(chunking.get("max_segments") or 20), 8 if small_model else 12
                )
            else:
                chunking["parallel_requests"] = 2 if self._speed_mode else 1
            config["engine"] = {
                "profile": self._translation_profile,
                "model": str(route["model"]),
                "api_base": str(route["api_base"]),
                "local": bool(route["local"]),
            }
            from task_config import atomic

            atomic(
                project / "翻译任务.yaml",
                yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
            )
            validate_task(project, config)
        if not self._route_ready(refresh=True):
            raise RuntimeError(self.modelStatus)
        self._job_path = str(project)
        self.settings.setValue("editions/" + file_digest(source)[:10], str(project))
        self._restore_task_options(project, config)
        self._output_path = self._managed_output(project, config)
        return project

    @staticmethod
    def _managed_output(project: Path, config: dict) -> str:
        source_files = (config.get("source") or {}).get("files") or []
        source = inside(project, str(source_files[0]))
        output = config.get("output") or {}
        directory = inside(project, str(output.get("directory") or "译文"))
        name = str(output.get("filename_rule") or "原文件名.zh-CN.txt").replace(
            "原文件名", source.stem
        )
        final = directory / Path(name).name
        partial = directory / f"{Path(name).name}.partial"
        return str(final if final.exists() else partial if partial.exists() else final)

    def _launch_engine(self, project: Path) -> None:
        route = self.translation_route()
        progress_path = project / "翻译进度.json"
        self._base_completed_chars = 0
        if progress_path.is_file():
            try:
                progress = json.loads(progress_path.read_text(encoding="utf-8"))
                self._base_completed_chars = int(progress.get("completed_source_chars") or 0)
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        self._current_completed_chars = self._base_completed_chars
        self._total_chars = 0
        process = QProcess(self)
        arguments = [
                "--task-root",
                str(project),
                "--start-segment",
                "0",
                "--end-segment",
                "0",
                "--context-segments",
                "3",
                "--api-base",
                str(route["api_base"]),
                "--model",
                str(route["model"]),
                "--auth-path",
                "",
                "--auth-provider",
                str(route["auth_provider"]),
                "--request-timeout",
                "180",
                "--prompt-mode",
                "native",
                "--parent-pid",
                str(os.getpid()),
            ]
        prepare_worker(process, self, 'translate_range.py', arguments, route=route, feature='translation')
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONDONTWRITEBYTECODE", "1")
        process.setProcessEnvironment(environment)
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read_process)
        process.finished.connect(self._process_finished)
        process.errorOccurred.connect(self._process_error)
        self._process = process
        self._buffer = ""
        self._running = True
        self._task_locked = True
        self._scan_progress = 0.0
        self._scanning = self._auto_glossary
        self._finished = False
        self._pausing = False
        self._result_seen = False
        self._error = ui_messages.Message("")
        self._status_title = ui_messages.Message(
            ui_messages.MessageCode.STARTING_MODEL, (self._model_display_message(),))
        self._status_detail = ui_messages.Message(
            ui_messages.MessageCode.RESUMING if self._has_checkpoint(project)
            else ui_messages.MessageCode.MODEL_WARMUP)
        self._started_at = time.monotonic()
        self._translation_started_at = 0.0
        self._elapsed = 0.0
        self._speed = 0.0
        self.timer.start()
        process.start()
        if not process.waitForStarted(5000):
            message = process.errorString() or "翻译进程未能启动"
            self.timer.stop()
            self._running = False
            self._process = None
            process.deleteLater()
            raise RuntimeError(message)
        self.changed.emit()

    def _read_process(self) -> None:
        if not self._process:
            return
        self._buffer += bytes(self._process.readAllStandardOutput()).decode("utf-8", errors="replace")
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                self._handle_event(json.loads(line))
            except json.JSONDecodeError:
                # Never expose credentials or raw request payloads; the engine
                # normally emits JSON only.  A legacy worker may still print a
                # plain status line, so retain its short text verbatim instead
                # of turning it into an uninspectable "unknown" summary.
                self._status_detail = line[:240]
                self.changed.emit()

    def _handle_event(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "started":
            self._scanning = False
            self._translation_started_at = time.monotonic()
            self._total_chars = int(event.get("total_source_chars") or 0)
            self._status_title = ui_messages.Message(ui_messages.MessageCode.TRANSLATING)
            self._status_detail = ui_messages.Message(ui_messages.MessageCode.TRANSLATION_STARTED)
        elif kind == "working":
            start, end = event.get("range") or (0, 0)
            phase = event.get("phase")
            self._status_title = ui_messages.Message(ui_messages.MessageCode.TRANSLATING)
            if phase == "structure_retry":
                self._status_detail = ui_messages.Message(
                    ui_messages.MessageCode.STRUCTURE_RETRY, (start, end))
            elif phase == "split":
                self._status_detail = ui_messages.Message(
                    ui_messages.MessageCode.SPLIT_RANGE, (start, end))
            else:
                self._status_detail = ui_messages.Message(
                    ui_messages.MessageCode.TRANSLATING_RANGE, (start, end))
        elif kind == "glossary_indexed":
            self._scanning = True
            self._status_title = ui_messages.Message(ui_messages.MessageCode.GLOSSARY_SCANNING)
            if event.get("strategy") == "fulltext":
                self._glossary_summary = ui_messages.Message(
                    ui_messages.MessageCode.GLOSSARY_INDEXED_FULLTEXT)
            else:
                self._glossary_summary = ui_messages.Message(
                    ui_messages.MessageCode.GLOSSARY_INDEXED_LIMITED
                    if event.get("omitted_by_limit") else
                    ui_messages.MessageCode.GLOSSARY_INDEXED,
                    (event.get('selected_candidates', 0),
                     event.get("omitted_by_limit", 0)))
            self._status_detail = self._glossary_summary
        elif kind == "glossary_progress":
            self._scanning = True
            self._status_title = ui_messages.Message(ui_messages.MessageCode.GLOSSARY_SCANNING)
            total = int(event.get("total_blocks") or 0)
            self._scan_progress = min(1.0, int(event.get("completed_blocks") or 0) / total) if total else 1.0
            self._status_detail = ui_messages.Message(
                ui_messages.MessageCode.GLOSSARY_PROGRESS,
                (event.get('completed_blocks', 0), total))
        elif kind == 'glossary_retry':
            self._status_detail = ui_messages.Message(
                ui_messages.MessageCode.GLOSSARY_RETRY, (event.get('source', ''),))
        elif kind == 'glossary_skipped':
            self._glossary_pending = int(event.get('pending_review') or 0)
            self._status_detail = ui_messages.Message(
                ui_messages.MessageCode.GLOSSARY_SKIPPED, (event.get('source', ''),))
        elif kind == "glossary_ready":
            self._scanning = False
            self._glossary_pending = int(event.get('pending_review') or 0)
            entries = int(event.get('entries') or 0)
            self._glossary_summary = ui_messages.Message(
                ui_messages.MessageCode.GLOSSARY_READY_PENDING
                if self._glossary_pending else ui_messages.MessageCode.GLOSSARY_READY,
                (entries, self._glossary_pending))
            self._status_title = ui_messages.Message(
                ui_messages.MessageCode.GLOSSARY_READY, (entries,))
            self._status_detail = self._glossary_summary
        elif kind == "progress":
            self._current_completed_chars = int(event.get("completed_source_chars") or 0)
            self._total_chars = int(event.get("total_source_chars") or self._total_chars)
            self._progress = min(1.0, max(0.0, float(event.get("percent") or 0.0) / 100.0))
            if event.get("output_file"):
                self._output_path = str(event["output_file"])
            start, end = event.get("range") or (0, 0)
            self._status_title = ui_messages.Message(ui_messages.MessageCode.TRANSLATING)
            self._status_detail = ui_messages.Message(
                ui_messages.MessageCode.CHUNK_SAVED, (start, end))
            self._update_speed()
        elif kind == "result":
            self._result_seen = True
            if event.get("output_file"):
                self._output_path = str(event["output_file"])
            status = event.get("status")
            if status in ("completed", "already_complete"):
                self._progress = 1.0
                self._finished = True
                self._status_title = ui_messages.Message(ui_messages.MessageCode.COMPLETED)
                self._status_detail = ui_messages.Message(
                    ui_messages.MessageCode.COMPLETED_DETAIL)
            elif status == "range_completed":
                self._status_title = ui_messages.Message(ui_messages.MessageCode.PARTIAL)
                self._status_detail = ui_messages.Message(
                    ui_messages.MessageCode.RANGE_COMPLETED)
            else:
                failures = event.get("failures") or []
                self._status_title = ui_messages.Message(ui_messages.MessageCode.PARTIAL)
                self._status_detail = ui_messages.Message(
                    ui_messages.MessageCode.PARTIAL_FAILURES, (len(failures),))
                if failures:
                    first = failures[0]
                    self._error = ui_messages.message_from_worker_error(
                        first.get("error") or "", str(first.get("code") or ""))
        elif kind == "error":
            self._result_seen = True
            self._status_title = ui_messages.Message(ui_messages.MessageCode.TRANSLATION_STOPPED)
            self._status_detail = ui_messages.Message(ui_messages.MessageCode.PAUSE_SAVED)
            # A worker that reports a structured code is trusted; an older
            # worker's raw error is classified only when it matches a message
            # this application itself produced.
            self._error = ui_messages.message_from_worker_error(
                event.get("detail") or event.get("error") or "",
                ui_messages.code_from_worker(event))
        if kind in ("progress", "result"):
            self._refresh_reader()
            self.shelfChanged.emit()
        self.changed.emit()

    def _process_finished(self, exit_code: int, _status: QProcess.ExitStatus) -> None:
        process = self._process
        self._read_process()
        self.timer.stop()
        self._running = False
        self._scanning = self._auto_glossary and not (Path(self._job_path) / '术语表.auto.json').is_file()
        self._task_locked = self._has_checkpoint(Path(self._job_path))
        publishing_after_pause = bool(
            self._pausing and self._submit_epub_publication(self._job_path))
        if not publishing_after_pause:
            self._refresh_reader()
        self._elapsed = time.monotonic() - self._started_at if self._started_at else self._elapsed
        self._update_speed()
        if self._pausing:
            self._status_title = ui_messages.Message(ui_messages.MessageCode.PAUSED)
            self._status_detail = ui_messages.Message(
                ui_messages.MessageCode.PAUSE_EPUB_PUBLISHED if publishing_after_pause
                else ui_messages.MessageCode.PAUSE_SAVED)
        elif exit_code != 0 and not self._result_seen:
            # A hard process stop has no model/API diagnostic to show.  The
            # engine's per-chunk atomic state makes this a resumable stop, not
            # a destructive failure and not a modal "crash" condition.
            self._reload_progress_snapshot()
            self._status_title = ui_messages.Message(ui_messages.MessageCode.TRANSLATION_STOPPED)
            self._status_detail = ui_messages.Message(ui_messages.MessageCode.RESUMING)
        self._process = None
        self._save_book_session()
        if process and not self._pausing:
            process.deleteLater()
        self.changed.emit()
        self.post_editor.changed.emit()
        # The automatic review path asks the same shared capability question as
        # the UI and the worker: an unsupported target must not reach a provider.
        if (self._finished and not self._pausing and self.post_editor.reviewAfter
                and self.post_editor.capabilitySupported):
            QTimer.singleShot(0, self.post_editor.prepare)

    def _process_error(self, error: QProcess.ProcessError) -> None:
        if not self._process or self._pausing:
            return
        if error == QProcess.ProcessError.FailedToStart:
            self._error = ui_messages.message_from_worker_error(
                (self._process.errorString() or ""), ui_messages.MessageCode.PROCESS_NOT_STARTED)
            self._status_title = ui_messages.Message(ui_messages.MessageCode.PROCESS_NOT_STARTED)
            self._status_detail = ui_messages.Message(ui_messages.MessageCode.PREPARE_FAILED)
        elif error == QProcess.ProcessError.Crashed:
            self._status_title = ui_messages.Message(ui_messages.MessageCode.TRANSLATION_STOPPED)
            self._status_detail = ui_messages.Message(ui_messages.MessageCode.RESUMING)
        self.changed.emit()

    @staticmethod
    def _kill_if_needed(process: QProcess) -> None:
        try:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
            process.deleteLater()
        except RuntimeError:
            # The application may already have destroyed this QObject while
            # the delayed pause safeguard was waiting.
            pass

    def _reload_progress_snapshot(self) -> None:
        progress_path = Path(self._job_path) / "翻译进度.json"
        if not progress_path.is_file():
            return
        try:
            progress = json.loads(progress_path.read_text(encoding="utf-8"))
            completed = int(progress.get("completed_source_chars") or 0)
            total = int(progress.get("total_source_chars") or 0)
            self._current_completed_chars = completed
            self._total_chars = total
            if total:
                self._progress = min(1.0, max(0.0, completed / total))
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    def _tick(self) -> None:
        if self._running and self._started_at:
            self._elapsed = time.monotonic() - self._started_at
            self._update_speed()
            self.changed.emit()

    def _update_speed(self) -> None:
        translated = max(0, self._current_completed_chars - self._base_completed_chars)
        elapsed = time.monotonic() - self._translation_started_at if self._translation_started_at else 0.0
        self._speed = translated * 60.0 / elapsed if elapsed > 0 else 0.0


def main() -> int:
    os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
    from epub_reader import initialize_webengine, secure_profile
    initialize_webengine()
    app = QApplication(sys.argv)
    web_guard = secure_profile(app)
    app.setQuitOnLastWindowClosed(True)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("Sindy")
    app.setWindowIcon(QIcon(str(ROOT / "assets/HyTranslator.png")))
    instance_guard = SingleInstanceGuard(parent=app)
    try:
        if not instance_guard.claim():
            return 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    # Install interface translators before product state and QML exist, and
    # share this one QSettings instance with the controller.
    settings = application_settings()
    locale = LocaleService(settings)
    locale.start(app)
    controller = TranslatorController(settings=settings, storage_context=StorageContext.production(),
                                      locale_service=locale)
    app.aboutToQuit.connect(controller.shutdown)
    app.lastWindowClosed.connect(app.quit)
    engine = QQmlApplicationEngine()
    locale.attach_engine(engine)
    engine.rootContext().setContextProperty("backend", controller)
    qml = ROOT / "ui/Main.qml"
    engine.load(QUrl.fromLocalFile(str(qml)))
    if not engine.rootObjects():
        instance_guard.close()
        return 1
    root_window = engine.rootObjects()[0]
    instance_guard.activationRequested.connect(lambda: activate_window(root_window))
    code = app.exec()
    # Destroy QML while its context controller is still alive.
    import shiboken6 as sip
    sip.delete(engine)
    instance_guard.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
