"""Single application interface locale: ``system``, ``zh-CN`` or ``en``.

Source strings stay Chinese.  Consequences:

* ``zh-CN`` installs no application translator (the source text already *is*
  Chinese) and only adds Qt's own Chinese catalogue for built-in dialogs, so a
  missing or damaged Chinese catalogue degrades to the source text.
* ``en`` is loaded from ``i18n/whaleread_en.qm``.  A missing, unreadable or
  empty catalogue never silently becomes English: the service keeps the
  Chinese interface, reports a notice, and never persists an ``en`` preference
  that is not actually in effect.

Only the locale preference key is written.  Application/organization names,
bundle identity, the single-instance key, workspace, books, tasks, cache
identities, stable object names and the AI answer locale are untouched.
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import (QLibraryInfo, QLocale, QObject, QSettings,
                            QT_TRANSLATE_NOOP, QTranslator, Signal,
                            QCoreApplication)

SYSTEM = "system"
CHINESE = "zh-CN"
ENGLISH = "en"
SUPPORTED = (SYSTEM, CHINESE, ENGLISH)

SETTINGS_KEY = "interface/locale"
TRANSLATION_PREFIX = "whaleread"


def _translations_dir() -> Path:
    """Locate the catalogue directory in a source tree and in a frozen bundle.

    In a source checkout the catalogue sits at ``<root>/i18n``.  Inside a macOS
    bundle PyInstaller collects data files under ``Contents/Resources`` while
    ``sys._MEIPASS`` points at ``Contents/Frameworks``, so both the module
    relative path and the bundle resource root are considered.  The first
    directory that actually contains a catalogue wins; the source-tree default
    is returned when neither exists, so a missing catalogue is reported by the
    locale service rather than silently resolving somewhere unrelated.
    """
    candidates = [Path(__file__).resolve().parents[2] / "i18n"]
    meipass = getattr(sys, "meipass", None) or getattr(sys, "_MEIPASS", None)
    if meipass:
        root = Path(meipass)
        candidates += [root / "i18n", root.parent / "Resources" / "i18n"]
    for candidate in candidates:
        if (candidate / f"{TRANSLATION_PREFIX}_en.qm").is_file():
            return candidate
    return candidates[0]


DEFAULT_TRANSLATIONS_DIR = _translations_dir()

# Native names of the interfaces themselves: an interface language is shown in
# its own script in both locales and is never translated.
CHINESE_NAME = "简体中文"
ENGLISH_NAME = "English"

# Every Python-side string is translated through one stable context.
# ``QT_TRANSLATE_NOOP`` keeps the source literal visible to lupdate while
# deferring the lookup to call time, so a runtime switch still applies.
CONTEXT = "LocaleService"
FOLLOW_SYSTEM = QT_TRANSLATE_NOOP("LocaleService", "跟随系统")
CURRENT_TEMPLATE = QT_TRANSLATE_NOOP("LocaleService", "当前界面语言：%1")
MISSING_ENGLISH = QT_TRANSLATE_NOOP(
    "LocaleService", "英文界面资源缺失或损坏，已继续使用中文界面。")
AUTO_LANGUAGE_ACTION = QT_TRANSLATE_NOOP("LocaleService", "自动识别")
CUSTOM_LANGUAGE_ACTION = QT_TRANSLATE_NOOP("LocaleService", "其他语言（手动输入）")


def _text(message: str) -> str:
    return QCoreApplication.translate(CONTEXT, message)


def normalize_preference(value) -> str:
    """Map any stored or requested value to ``system``/``zh-CN``/``en``.

    Unknown values fall back to ``system`` — the caller then decides what the
    system resolves to, so a damaged preference can never pin a wrong language.
    """
    text = str(value or "").strip().casefold().replace("_", "-")
    if not text or text in {"system", "auto", "default"}:
        return SYSTEM
    if text == CHINESE.casefold() or text.startswith("zh"):
        return CHINESE
    if text == ENGLISH.casefold() or text.startswith("en"):
        return ENGLISH
    return SYSTEM


def _system_name(system_locale) -> str:
    if system_locale is None:
        return QLocale.system().name()
    if isinstance(system_locale, QLocale):
        return system_locale.name()
    return str(system_locale)


class LocaleService(QObject):
    """Own the interface translator; notify the UI instead of rebuilding it."""

    changed = Signal()

    def __init__(self, settings: QSettings, *, translations_dir=None,
                 system_locale=None, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.translations_dir = (Path(translations_dir) if translations_dir is not None
                                 else DEFAULT_TRANSLATIONS_DIR)
        self._system_name = _system_name(system_locale)
        self._app = None
        self._app_translator = None
        self._qt_translator = None
        self._engine = None
        self._notice = ""
        self._started = False
        self._preference = self._stored_preference()
        self._effective = self.resolve(self._preference)

    # ------------------------------------------------------------------ state

    @property
    def started(self) -> bool:
        return self._started

    @property
    def preference(self) -> str:
        """The stored request: ``system``, ``zh-CN`` or ``en``."""
        return self._preference

    @property
    def effective(self) -> str:
        """The locale actually in force: ``zh-CN`` or ``en``."""
        return self._effective

    @property
    def notice(self) -> str:
        """Localized explanation when the requested locale could not be used."""
        return self._notice

    @property
    def system_name(self) -> str:
        return self._system_name

    def effective_name(self) -> str:
        return CHINESE_NAME if self._effective == CHINESE else ENGLISH_NAME

    def resolve(self, preference) -> str:
        """Effective locale for a preference, without touching resources."""
        value = normalize_preference(preference)
        if value == CHINESE:
            return CHINESE
        if value == ENGLISH:
            return ENGLISH
        return CHINESE if self._system_name.casefold().startswith("zh") else ENGLISH

    def options(self) -> list[dict]:
        """Structured picker rows; only the ``system`` action is translated."""
        return [
            {"value": SYSTEM, "text": _text(FOLLOW_SYSTEM), "kind": "action"},
            {"value": CHINESE, "text": CHINESE_NAME, "kind": "language"},
            {"value": ENGLISH, "text": ENGLISH_NAME, "kind": "language"},
        ]

    def display_label(self) -> str:
        """Localized label naming the interface language in force."""
        return _text(CURRENT_TEMPLATE).replace("%1", self.effective_name())

    def localize_labels(self, rows: list[dict], context: str = CONTEXT) -> list[dict]:
        """Translate display labels of structured option rows, keeping values.

        Used for the annotation category picker: the stable ``value`` and the
        stored Chinese label both stay unchanged, only ``text`` is localized.
        """
        return [({**row, "text": QCoreApplication.translate(context, row["text"])}
                 if row.get("text") and row.get("value") else dict(row))
                for row in rows]

    def localize_language_options(self, rows: list[dict]) -> list[dict]:
        """Translate picker actions while preserving every native language name."""
        actions = {
            "auto": _text(AUTO_LANGUAGE_ACTION),
            "custom": _text(CUSTOM_LANGUAGE_ACTION),
        }
        return [({**row, "text": actions[row["value"]]}
                 if row.get("kind") == "action" and row.get("value") in actions
                 else dict(row))
                for row in rows]

    # ------------------------------------------------------------- life cycle

    def attach_engine(self, engine) -> None:
        """Remember the live QML engine so a switch can call ``retranslate()``."""
        self._engine = engine

    def start(self, app=None) -> str:
        """Install translators for the resolved preference and persist it.

        Call this before constructing product state or loading QML.
        """
        self._app = app if app is not None else QCoreApplication.instance()
        stored = str(self.settings.value(SETTINGS_KEY, "", type=str) or "").strip()
        preference = normalize_preference(stored) if stored else self._default_preference()
        self.apply(preference, persist=True)
        self._started = True
        return self._effective

    def set_locale(self, value) -> bool:
        """Switch the interface at runtime; ``False`` when English was refused."""
        if self._app is None:
            self._app = QCoreApplication.instance()
        self.apply(value, persist=True)
        return self._effective == self.resolve(value)

    def apply(self, preference, *, persist: bool = True) -> str:
        requested = normalize_preference(preference)
        target = self.resolve(requested)
        failed_english = False
        if target == ENGLISH:
            translator = self._english_translator()
            if translator is None:
                failed_english = True
            elif not self._install_application(translator):
                # Loading a catalogue is not enough: Qt can still reject it.
                failed_english = True
        else:
            self._install_application(None)
        if failed_english:
            # Safe failure: remove any old English translator before reporting
            # Chinese as effective, and never persist a false English state.
            self._install_application(None)
            target = CHINESE
            requested = CHINESE
        self._install_qt(target)
        # The fallback interface is Chinese.  Use its source literal after the
        # old translator is gone so the notice cannot remain in English.
        self._notice = MISSING_ENGLISH if failed_english else ""
        self._preference = requested
        self._effective = target
        if persist:
            self.settings.setValue(SETTINGS_KEY, requested)
            self.settings.sync()
        self._retranslate()
        self.changed.emit()
        return self._effective

    def shutdown(self) -> None:
        """Remove both translators; the caller keeps owning the QML engine."""
        self._install_application(None)
        self._remove(self._qt_translator)
        self._qt_translator = None
        self._engine = None

    # ---------------------------------------------------------------- helpers

    def _default_preference(self) -> str:
        """Legacy users stay Chinese; a clean public-preview user starts in English."""
        return CHINESE if self.settings.allKeys() else ENGLISH

    def _stored_preference(self) -> str:
        stored = str(self.settings.value(SETTINGS_KEY, "", type=str) or "").strip()
        if stored:
            return normalize_preference(stored)
        return self._default_preference()

    def _english_translator(self):
        path = self.translations_dir / f"{TRANSLATION_PREFIX}_{ENGLISH}.qm"
        if not path.is_file():
            return None
        translator = QTranslator()
        if not translator.load(str(path)) or translator.isEmpty():
            return None
        return translator

    def _install_application(self, translator) -> bool:
        self._remove(self._app_translator)
        self._app_translator = None
        if translator is None:
            return True
        if self._app is None:
            return False
        if self._app.installTranslator(translator):
            self._app_translator = translator
            return True
        return False

    def _install_qt(self, locale: str) -> None:
        self._remove(self._qt_translator)
        self._qt_translator = None
        if self._app is None:
            return
        name = "qtbase_zh_CN" if locale == CHINESE else "qtbase_en"
        directory = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        translator = QTranslator()
        if translator.load(name, directory) and not translator.isEmpty():
            if self._app.installTranslator(translator):
                self._qt_translator = translator

    def _remove(self, translator) -> None:
        if translator is None or self._app is None:
            return
        self._app.removeTranslator(translator)
        translator.setParent(None)

    def _retranslate(self) -> None:
        if self._engine is not None:
            self._engine.retranslate()
