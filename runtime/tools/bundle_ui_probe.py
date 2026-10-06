"""Isolated packaged-UI probe used by ``scripts/qa_bundle_only.py``.

This worker is allowlisted by the frozen entry point.  It loads the real
``Main.qml`` with the real controller, temporary settings/workspace/cache roots,
and no provider calls.  It exists so release QA can exercise the signed bundle
without replacing a bundled worker or touching the installed application.
"""
from __future__ import annotations

import json
import sys
import tempfile
from file_access import application_home, is_sandboxed
from application_identity import bundle_identifier, application_settings
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, QUrl, QMetaObject, qInstallMessageHandler
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication

from epub_reader import initialize_webengine
from locale_service import DEFAULT_TRANSLATIONS_DIR, LocaleService
from native_launcher import ROOT, TranslatorController
from storage_context import StorageContext


BAD_QML = ("ReferenceError", "TypeError", "Unable to assign", "is not defined", "Binding loop")


def main() -> int:
    initialize_webengine()
    warnings: list[str] = []
    qInstallMessageHandler(lambda _kind, _context, message: warnings.append(message))
    app = QApplication(["bundle-ui-probe"])
    temporary_root = application_home() / 'tmp'
    temporary_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="whaleread-bundle-ui-", dir=temporary_root) as raw:
        isolated = Path(raw).resolve()
        settings = QSettings(
            str(isolated / "settings.ini"), QSettings.Format.IniFormat)
        locale = LocaleService(settings, system_locale="zh_CN")
        locale.start(app)
        storage = StorageContext.isolated(
            isolated / "storage", purpose="probe", settings_scope=str(settings.fileName()))
        controller = TranslatorController(
            workspace=isolated / "jobs",
            settings=settings,
            storage_context=storage,
            locale_service=locale,
            restore=False,
        )
        engine = QQmlApplicationEngine()
        locale.attach_engine(engine)
        engine.rootContext().setContextProperty("backend", controller)
        qml = ROOT / "ui/Main.qml"
        engine.load(QUrl.fromLocalFile(str(qml)))
        app.processEvents()
        roots = engine.rootObjects()
        window = roots[0] if roots else None
        welcome = window.findChild(QObject, 'welcomePopup') if window is not None else None
        welcome_visible = bool(welcome and welcome.property('visible'))
        settings_button = (
            window.findChild(QObject, "settingsButton") if window is not None else None)
        en_text = settings_button.property("text") if settings_button is not None else ""
        switched = controller.setUiLocale("zh-CN") if window is not None else False
        app.processEvents()
        zh_text = settings_button.property("text") if settings_button is not None else ""
        switched_back = controller.setUiLocale("en") if window is not None else False
        app.processEvents()
        en_text_again = settings_button.property("text") if settings_button is not None else ""
        welcome_read = window.findChild(QObject, 'welcomeRead') if window is not None else None
        if welcome_read is not None:
            QMetaObject.invokeMethod(welcome_read, 'clicked')
            app.processEvents()
        onboarding_saved = bool(not controller.onboardingPending and QSettings(
            settings.fileName(), QSettings.IniFormat).value('onboarding/completed_v1', False, type=bool))
        qml_errors = [message for message in warnings if any(bad in message for bad in BAD_QML)]
        report = {
            "frozen": bool(getattr(sys, "frozen", False)),
            "sandbox_entitlement_active": is_sandboxed(),
            "application_home": str(application_home()),
            "bundle_identifier": bundle_identifier(),
            "settings_domain": application_settings().organizationName(),
            "welcome_visible": welcome_visible,
            "onboarding_saved": onboarding_saved,
            "meipass": str(getattr(sys, "_MEIPASS", "")),
            "root": str(ROOT),
            "qml": str(qml),
            "root_loaded": bool(window),
            "settings_button_found": settings_button is not None,
            "translations_dir": str(DEFAULT_TRANSLATIONS_DIR),
            "catalogue_exists": (
                DEFAULT_TRANSLATIONS_DIR / "whaleread_en.qm").is_file(),
            "en_text": en_text,
            "switched": bool(switched),
            "zh_text": zh_text,
            "switched_back": bool(switched_back),
            "en_text_again": en_text_again,
            "qml_errors": qml_errors,
            "source_tree_on_path": any(
                not entry or not Path(entry).resolve().is_relative_to(Path(sys.executable).resolve().parent.parent)
                for entry in sys.path),
            "workspace_isolated": controller.workspace.is_relative_to(isolated),
            "storage_isolated": storage.cache_root.is_relative_to(isolated),
        }
        print("BUNDLE_UI_PROBE " + json.dumps(report, ensure_ascii=False), flush=True)
        controller.shutdown()
        engine.deleteLater()
        app.processEvents()
    failures = []
    if not report["root_loaded"]:
        failures.append("Main.qml did not create a root object")
    if report["en_text"] != "Settings":
        failures.append("a clean installation did not start in English")
    if report["zh_text"] != "设置":
        failures.append("the real settings control did not retranslate en -> zh-CN")
    if report["en_text_again"] != "Settings":
        failures.append("the real settings control did not return to English")
    for key in (
        "frozen", "settings_button_found", "catalogue_exists", "switched",
        "switched_back", "workspace_isolated", "storage_isolated",
    ):
        if not report[key]:
            failures.append(f"{key} was not satisfied")
    if report["source_tree_on_path"]:
        failures.append("a source checkout appeared on sys.path")
    if report["qml_errors"]:
        failures.append("QML emitted binding/reference errors")
    if failures:
        for failure in failures:
            print(f"bundle UI probe failed: {failure}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
