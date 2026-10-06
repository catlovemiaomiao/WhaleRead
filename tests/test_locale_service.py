"""Task 02: interface locale service, Qt translation resources and the small
shell proof (navigation/header/direction dialog).

Everything runs on temporary settings, temporary workspaces and synthetic
text.  No translation or AI API is called, and no network access is allowed.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import uuid
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "runtime/lib"))
sys.path.insert(0, str(ROOT / "scripts"))

from PySide6.QtCore import (QCoreApplication, QMetaObject, QObject, QSettings,
                            QTranslator, QUrl, qInstallMessageHandler)  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402,F401  (registers the type for property reads)
from PySide6.QtWidgets import QApplication  # noqa: E402

import i18n_check  # noqa: E402
import i18n_common  # noqa: E402
import locale_service as LS  # noqa: E402
from i18n_common import TranslationError, messages, parse_catalogue  # noqa: E402

TRANSLATIONS = ROOT / "i18n"
CATALOGUE = TRANSLATIONS / "whaleread_en.ts"
COMPILED = TRANSLATIONS / "whaleread_en.qm"
QML_WARNINGS = ("ReferenceError", "TypeError", "Unable to assign", "is not defined",
                "Binding loop")


def application():
    return QApplication.instance() or QApplication([])


def find(window, name):
    found = window.findChild(QObject, name)
    if found is None:
        raise AssertionError(f"QML 对象不存在：{name}")
    return found


def settings_at(root, values=None):
    settings = QSettings(str(Path(root) / "settings.ini"), QSettings.Format.IniFormat)
    for key, value in (values or {}).items():
        settings.setValue(key, value)
    if values:
        settings.sync()
    return settings


def make_service(test, settings, system="zh_CN", translations=None):
    service = LS.LocaleService(settings, system_locale=system,
                               translations_dir=translations or TRANSLATIONS)
    test.addCleanup(service.shutdown)
    return service


class LocaleResolutionTests(unittest.TestCase):
    def setUp(self):
        application()

    def test_explicit_choice_overrides_the_system(self):
        with tempfile.TemporaryDirectory() as raw:
            for system in ("zh_CN", "en_US", "ja_JP"):
                for preference, expected in (("zh-CN", "zh-CN"), ("en", "en")):
                    service = make_service(self, settings_at(raw), system=system)
                    self.assertEqual(expected, service.resolve(preference), system)

    def test_system_chinese_resolves_to_simplified_chinese(self):
        with tempfile.TemporaryDirectory() as raw:
            service = make_service(self, settings_at(raw))
            for name in ("zh_CN", "zh-Hans", "zh_TW", "zh-Hant", "zh_HK"):
                service._system_name = name
                self.assertEqual("zh-CN", service.resolve(LS.SYSTEM), name)

    def test_system_english_resolves_to_english(self):
        with tempfile.TemporaryDirectory() as raw:
            service = make_service(self, settings_at(raw))
            for name in ("en_US", "en_GB", "en"):
                service._system_name = name
                self.assertEqual("en", service.resolve(LS.SYSTEM), name)

    def test_other_and_unknown_system_locales_fall_back_to_english(self):
        with tempfile.TemporaryDirectory() as raw:
            service = make_service(self, settings_at(raw))
            for name in ("ja_JP", "fr_FR", "de_DE", "C", "", "POSIX"):
                service._system_name = name
                self.assertEqual("en", service.resolve(LS.SYSTEM), name)

    def test_unknown_preference_is_treated_as_system(self):
        for value, expected in ((None, LS.SYSTEM), ("", LS.SYSTEM), ("  ", LS.SYSTEM),
                                ("klingon", LS.SYSTEM), ("system", LS.SYSTEM),
                                ("zh", "zh-CN"), ("ZH_cn", "zh-CN"),
                                ("en-US", "en"), ("EN", "en")):
            self.assertEqual(expected, LS.normalize_preference(value), repr(value))


class LocaleDefaultAndPersistenceTests(unittest.TestCase):
    def setUp(self):
        application()

    def test_legacy_user_stays_chinese_even_on_an_english_system(self):
        with tempfile.TemporaryDirectory() as raw:
            settings = settings_at(raw, {"reader/font_size": 19})
            service = make_service(self, settings, system="en_US")
            self.assertEqual("zh-CN", service.effective)
            self.assertEqual("zh-CN", service.start(application()))
            self.assertEqual("zh-CN", service.preference)
            self.assertEqual("zh-CN", settings.value(LS.SETTINGS_KEY, "", type=str))
            self.assertEqual("设置", QCoreApplication.translate("Main", "设置"))

    def test_clean_user_starts_in_english(self):
        for system in ("zh_CN", "en_US", "ja_JP"):
            with tempfile.TemporaryDirectory() as raw:
                settings = settings_at(raw)
                service = make_service(self, settings, system=system)
                self.assertEqual("en", service.start(application()), system)
                self.assertEqual("en", settings.value(LS.SETTINGS_KEY, "", type=str))

    @unittest.skipUnless(sys.platform == 'darwin', 'macOS native preference inheritance')
    def test_native_global_defaults_do_not_turn_a_new_reader_into_a_legacy_user(self):
        import application_identity
        identity = 'com.example.whaleread.localetest' + uuid.uuid4().hex
        with patch.object(application_identity, 'bundle_identifier', return_value=identity):
            settings = application_identity.application_settings()
            self.addCleanup(lambda: (settings.clear(), settings.sync()))
            self.assertEqual([], settings.allKeys())
            service = make_service(self, settings, system='zh_CN')
            self.assertEqual('en', service.start(application()))
            settings.setValue(LS.SETTINGS_KEY, 'zh-CN')
            settings.sync()
            reopened = application_identity.application_settings()
            restored = make_service(self, reopened, system='en_US')
            self.assertEqual('zh-CN', restored.start(application()))
            self.assertEqual('zh-CN', reopened.value(LS.SETTINGS_KEY))

    def test_only_the_locale_preference_is_written(self):
        with tempfile.TemporaryDirectory() as raw:
            settings = settings_at(raw, {"reader/font_size": 19, "appearance/theme": "jade"})
            before = set(settings.allKeys())
            service = make_service(self, settings, system="en_US")
            service.start(application())
            service.set_locale("en")
            added = set(settings.allKeys()) - before
            self.assertEqual({LS.SETTINGS_KEY}, added)
            self.assertEqual(19, settings.value("reader/font_size", type=int))
            self.assertEqual("jade", settings.value("appearance/theme", type=str))

    def test_preference_survives_reopen(self):
        with tempfile.TemporaryDirectory() as raw:
            first = make_service(self, settings_at(raw), system="zh_CN")
            first.start(application())
            self.assertTrue(first.set_locale("en"))
            self.assertEqual("en", settings_at(raw).value(LS.SETTINGS_KEY, "", type=str))
            reopened = settings_at(raw)
            second = make_service(self, reopened, system="zh_CN")
            self.assertEqual("en", second.preference)
            self.assertEqual("en", second.start(application()))
            self.assertEqual("Settings", second._app_translator.translate("Main", "设置"))

    def test_invalid_stored_value_never_pins_a_language(self):
        with tempfile.TemporaryDirectory() as raw:
            settings = settings_at(raw, {LS.SETTINGS_KEY: "klingon"})
            service = make_service(self, settings, system="en_US")
            self.assertEqual(LS.SYSTEM, service.preference)
            self.assertEqual("en", service.start(application()))


class LocaleResourceTests(unittest.TestCase):
    def setUp(self):
        application()

    def test_english_catalogue_loads_and_translates(self):
        with tempfile.TemporaryDirectory() as raw:
            service = make_service(self, settings_at(raw), system="en_US")
            self.assertEqual("en", service.start(application()))
            self.assertEqual("Settings", QCoreApplication.translate("Main", "设置"))
            self.assertEqual("Follow system", QCoreApplication.translate("LocaleService", "跟随系统"))
            self.assertEqual("", service.notice)

    def test_switching_back_to_chinese_restores_the_source_text(self):
        with tempfile.TemporaryDirectory() as raw:
            service = make_service(self, settings_at(raw), system="en_US")
            service.start(application())
            self.assertEqual("Settings", QCoreApplication.translate("Main", "设置"))
            self.assertTrue(service.set_locale("zh-CN"))
            self.assertEqual("设置", QCoreApplication.translate("Main", "设置"))
            self.assertEqual("跟随系统", QCoreApplication.translate("LocaleService", "跟随系统"))

    def test_missing_english_resource_refuses_and_keeps_chinese(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as empty:
            settings = settings_at(raw, {LS.SETTINGS_KEY: "zh-CN"})
            service = make_service(self, settings, system="zh_CN", translations=Path(empty))
            service.start(application())
            self.assertFalse(service.set_locale("en"))
            self.assertEqual("zh-CN", service.effective)
            self.assertEqual("zh-CN", settings.value(LS.SETTINGS_KEY, "", type=str))
            self.assertTrue(service.notice)
            self.assertEqual("设置", QCoreApplication.translate("Main", "设置"))

    def test_corrupt_english_resource_refuses(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as broken:
            (Path(broken) / "whaleread_en.qm").write_bytes(b"this is not a catalogue")
            settings = settings_at(raw, {LS.SETTINGS_KEY: "zh-CN"})
            service = make_service(self, settings, system="zh_CN", translations=Path(broken))
            self.assertFalse(service.set_locale("en"))
            self.assertEqual("zh-CN", service.effective)
            self.assertEqual("zh-CN", settings.value(LS.SETTINGS_KEY, "", type=str))
            self.assertTrue(service.notice)

    def test_empty_but_loadable_english_catalogue_refuses(self):
        lrelease = Path(sys.executable).resolve().parent / "pyside6-lrelease"
        if not lrelease.is_file():
            self.skipTest("pyside6-lrelease 不可用")
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as blank:
            blank_dir = Path(blank)
            (blank_dir / "empty.ts").write_text(
                '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE TS>\n'
                '<TS version="2.1" language="en_US"></TS>\n', encoding="utf-8")
            subprocess.run([str(lrelease), str(blank_dir / "empty.ts"), "-qm",
                            str(blank_dir / "whaleread_en.qm")],
                           check=True, capture_output=True)
            settings = settings_at(raw, {LS.SETTINGS_KEY: "zh-CN"})
            service = make_service(self, settings, system="zh_CN", translations=blank_dir)
            self.assertFalse(service.set_locale("en"))
            self.assertEqual("zh-CN", service.effective)
            self.assertNotEqual("en", settings.value(LS.SETTINGS_KEY, "", type=str))

    def test_startup_with_missing_english_never_persists_english(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as empty:
            settings = settings_at(raw)
            service = make_service(self, settings, system="en_US", translations=Path(empty))
            self.assertEqual("zh-CN", service.start(application()))
            self.assertEqual("zh-CN", settings.value(LS.SETTINGS_KEY, "", type=str))
            self.assertTrue(service.notice)
            self.assertEqual("设置", QCoreApplication.translate("Main", "设置"))

    def test_a_failed_switch_never_leaves_the_old_english_translator_active(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as empty:
            settings = settings_at(raw)
            service = make_service(self, settings, system="zh_CN")
            service.start(application())
            self.assertTrue(service.set_locale("en"))
            self.assertEqual("Settings", QCoreApplication.translate("Main", "设置"))
            service.translations_dir = Path(empty)
            self.assertFalse(service.set_locale("en"))
            self.assertEqual("zh-CN", service.effective)
            self.assertEqual("设置", QCoreApplication.translate("Main", "设置"))
            self.assertNotEqual("en", settings.value(LS.SETTINGS_KEY, "", type=str))
            self.assertEqual("英文界面资源缺失或损坏，已继续使用中文界面。", service.notice)

    def test_qt_rejecting_a_loaded_catalogue_never_claims_english(self):
        class RejectingApplication:
            def installTranslator(self, translator):  # noqa: N802
                return False

            def removeTranslator(self, translator):  # noqa: N802
                return True

        with tempfile.TemporaryDirectory() as raw:
            settings = settings_at(raw)
            service = make_service(self, settings, system="en_US")
            service._app = RejectingApplication()
            self.assertEqual("zh-CN", service.apply("en"))
            self.assertEqual("zh-CN", service.preference)
            self.assertEqual("zh-CN", settings.value(LS.SETTINGS_KEY, "", type=str))
            self.assertIsNone(service._app_translator)
            self.assertEqual("英文界面资源缺失或损坏，已继续使用中文界面。", service.notice)


class LocaleDisplayTests(unittest.TestCase):
    def setUp(self):
        application()

    def test_option_model_and_label_refresh_but_native_names_do_not(self):
        with tempfile.TemporaryDirectory() as raw:
            service = make_service(self, settings_at(raw), system="en_US")
            service.start(application())
            self.assertEqual(["Follow system", "简体中文", "English"],
                             [row["text"] for row in service.options()])
            self.assertEqual("Interface language: English", service.display_label())
            self.assertTrue(service.set_locale("zh-CN"))
            self.assertEqual(["跟随系统", "简体中文", "English"],
                             [row["text"] for row in service.options()])
            self.assertEqual("当前界面语言：简体中文", service.display_label())
            self.assertEqual([LS.SYSTEM, "zh-CN", "en"],
                             [row["value"] for row in service.options()])

    def test_switching_is_offline(self):
        with tempfile.TemporaryDirectory() as raw:
            service = make_service(self, settings_at(raw), system="zh_CN")
            service.start(application())
            with patch("urllib.request.urlopen", side_effect=AssertionError("network")), \
                    patch("socket.create_connection", side_effect=AssertionError("network")):
                self.assertTrue(service.set_locale("en"))
                self.assertTrue(service.set_locale("zh-CN"))


class TranslationCatalogueTests(unittest.TestCase):
    def setUp(self):
        application()

    def translator(self):
        """The compiled catalogue, installed so ``%n`` is substituted like Qt does."""
        app = application()
        translator = QTranslator()
        self.assertTrue(translator.load(str(COMPILED)), "缺少编译产物 i18n/whaleread_en.qm")
        self.assertFalse(translator.isEmpty())
        app.installTranslator(translator)
        self.addCleanup(lambda: app.removeTranslator(translator))
        return translator

    def test_english_plural_has_zero_one_and_many_forms(self):
        self.translator()
        forms = {number: QCoreApplication.translate("Main", "%n 本", "bookshelf count", number)
                 for number in (0, 1, 2, 5)}
        self.assertEqual("0 books", forms[0])
        self.assertEqual("1 book", forms[1])
        self.assertEqual("2 books", forms[2])
        self.assertEqual("5 books", forms[5])

    def test_placeholder_parity_in_the_compiled_catalogue(self):
        self.translator()
        self.assertTrue(QCoreApplication.translate("Main", "%1 · 已就绪").startswith("%1"))
        self.assertIn("%1", QCoreApplication.translate("LocaleService", "当前界面语言：%1"))
        self.assertNotIn("%1", QCoreApplication.translate("Main", "设置"))

    def test_reviewed_catalogue_passes_the_check_script(self):
        rows = i18n_check.check_catalogue(CATALOGUE)
        # Task 03 grew the catalogue to every QML surface.
        self.assertGreaterEqual(len(set((context, source) for context, source, _, _ in rows)), 400)
        i18n_check.check_compiled(rows, COMPILED)

    def mutate(self, root, mutate):
        tree = ET.parse(CATALOGUE)
        mutate(tree.getroot())
        path = Path(root) / "mutated.ts"
        tree.write(path, encoding="utf-8", xml_declaration=True)
        return path

    def find(self, root, source):
        for _, message in messages(root):
            if (message.findtext("source") or "") == source:
                return message
        raise AssertionError(source)

    def test_unfinished_or_empty_messages_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            def unfinished(root):
                self.find(root, "设置").find("translation").set("type", "unfinished")
            with self.assertRaises(TranslationError):
                i18n_check.check_catalogue(self.mutate(raw, unfinished))
            def emptied(root):
                self.find(root, "设置").find("translation").text = "   "
            with self.assertRaises(TranslationError):
                i18n_check.check_catalogue(self.mutate(raw, emptied))

    def test_vanished_messages_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            def vanished(root):
                self.find(root, "设置").find("translation").set("type", "vanished")
            with self.assertRaises(TranslationError):
                i18n_check.check_catalogue(self.mutate(raw, vanished))

    def test_placeholder_change_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            def dropped(root):
                self.find(root, "%1 · 已就绪").find("translation").text = "ready"
            with self.assertRaises(TranslationError):
                i18n_check.check_catalogue(self.mutate(raw, dropped))

    def test_missing_or_extra_numerus_form_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            def single(root):
                translation = self.find(root, "%n 本").find("translation")
                forms = translation.findall("numerusform")
                translation.remove(forms[1])
            with self.assertRaises(TranslationError):
                i18n_check.check_catalogue(self.mutate(raw, single))

    def test_wrong_catalogue_language_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            def language(root):
                root.set("language", "fr_FR")
            with self.assertRaises(TranslationError):
                i18n_check.check_catalogue(self.mutate(raw, language))

    def test_missing_source_string_fails_the_coverage_check(self):
        with tempfile.TemporaryDirectory() as raw:
            def dropped(root):
                for context in root.findall("context"):
                    for message in list(context.findall("message")):
                        if (message.findtext("source") or "") == "设置":
                            context.remove(message)
            with self.assertRaises(TranslationError):
                i18n_check.check_coverage(parse_catalogue(self.mutate(raw, dropped)))


class TranslationPipelineTests(unittest.TestCase):
    """Run the real scripts end to end, without touching the committed files."""

    def run_script(self, name, *arguments):
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        return subprocess.run([sys.executable, str(ROOT / "scripts" / name), *arguments],
                              cwd=str(ROOT), capture_output=True, text=True, env=env)

    def entries(self, path):
        """Every message with its source locations, so stale line numbers fail."""
        result = set()
        for context, message in messages(parse_catalogue(path)):
            locations = frozenset((node.get("filename") or "", node.get("line") or "")
                                  for node in message.findall("location"))
            result.add((context, message.findtext("source") or "",
                        message.findtext("comment") or "",
                        message.get("numerus") == "yes", locations))
        return result

    def test_extraction_is_deterministic_and_matches_the_committed_catalogue(self):
        probe = TRANSLATIONS / ".extract-probe.ts"
        second = TRANSLATIONS / ".extract-probe-2.ts"
        try:
            first_run = self.run_script("i18n_extract.py", "--output", str(probe))
            self.assertEqual(0, first_run.returncode, first_run.stderr)
            again = self.run_script("i18n_extract.py", "--output", str(second))
            self.assertEqual(0, again.returncode, again.stderr)
            self.assertEqual(probe.read_bytes(), second.read_bytes(),
                             "lupdate 提取结果不确定")
            self.assertEqual(self.entries(CATALOGUE), self.entries(probe),
                             "提交的 catalogue 与当前源文件不一致，请重新提取并复审")
        finally:
            for path in (probe, second):
                path.unlink(missing_ok=True)

    def test_compile_and_check_scripts_succeed_on_a_temporary_output(self):
        compiled = TRANSLATIONS / ".pipeline-probe.qm"
        second = TRANSLATIONS / ".pipeline-probe-2.qm"
        try:
            compile_run = self.run_script("i18n_compile.py", "--output", str(compiled))
            self.assertEqual(0, compile_run.returncode, compile_run.stderr)
            self.assertTrue(compiled.is_file() and compiled.stat().st_size > 0)
            again = self.run_script("i18n_compile.py", "--output", str(second))
            self.assertEqual(0, again.returncode, again.stderr)
            self.assertEqual(compiled.read_bytes(), second.read_bytes(),
                             "lrelease 编译结果不确定")
            self.assertEqual(COMPILED.read_bytes(), compiled.read_bytes(),
                             "提交的 .qm 不是当前 catalogue 的可复现产物")
            check_run = self.run_script("i18n_check.py", "--qm", str(compiled))
            self.assertEqual(0, check_run.returncode, check_run.stderr)
        finally:
            compiled.unlink(missing_ok=True)
            second.unlink(missing_ok=True)

    def test_compile_refuses_an_unfinished_catalogue(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            tree = ET.parse(CATALOGUE)
            for _, message in messages(tree.getroot()):
                if (message.findtext("source") or "") == "关闭":
                    message.find("translation").set("type", "unfinished")
                    break
            catalogue = root / "unfinished.ts"
            output = root / "unfinished.qm"
            tree.write(catalogue, encoding="utf-8", xml_declaration=True)
            run = self.run_script("i18n_compile.py", "--catalogue", str(catalogue),
                                  "--output", str(output))
            self.assertEqual(1, run.returncode)
            self.assertIn("未翻译", run.stderr)
            self.assertFalse(output.exists())

    def test_tool_resolution_never_falls_back_to_path(self):
        with tempfile.TemporaryDirectory() as raw:
            fake_python = Path(raw) / "python"
            fake_python.write_text("", encoding="utf-8")
            tools_dir = str(Path(sys.executable).resolve().parent)
            with patch.object(i18n_common.sys, "executable", str(fake_python)), \
                    patch.dict(os.environ, {"PATH": tools_dir}):
                with self.assertRaises(TranslationError):
                    i18n_common.tool("pyside6-lupdate")

    def test_check_script_fails_on_a_missing_compiled_resource(self):
        run = self.run_script("i18n_check.py", "--qm", str(TRANSLATIONS / "missing.qm"))
        self.assertEqual(1, run.returncode)
        self.assertIn("缺少编译产物", run.stderr)


class BootstrapWiringTests(unittest.TestCase):
    """The controller exposes the locale to QML and keeps production restore."""

    def setUp(self):
        application()

    def controller(self, root, *, system="zh_CN", storage=False):
        from native_launcher import TranslatorController
        from storage_context import StorageContext
        settings = settings_at(root)
        service = make_service(self, settings, system=system)
        service.start(application())
        context = StorageContext.isolated(root / "storage") if storage else None
        controller = TranslatorController(workspace=root / "jobs", settings=settings,
                                          storage_context=context, locale_service=service,
                                          restore=False)
        self.addCleanup(lambda: (controller.reader_timer.stop(), controller.shutdown()))
        return controller

    def test_explicit_settings_keep_production_restore_behaviour(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            qa = self.controller(root / "qa")
            self.assertTrue(qa._isolated_settings)
            explicit = self.controller(root / "explicit", storage=True)
            self.assertFalse(explicit._isolated_settings)
            self.assertIsNotNone(explicit.settings)

    def test_controller_locale_properties_refresh_and_notify(self):
        with tempfile.TemporaryDirectory() as raw:
            controller = self.controller(Path(raw))
            changes: list[int] = []
            controller.changed.connect(lambda: changes.append(1))
            self.assertEqual("en", controller.uiLocale)
            self.assertEqual("en", controller.effectiveUiLocale)
            self.assertEqual(["Follow system", "简体中文", "English"],
                             [row["text"] for row in controller.localeOptions])
            self.assertEqual("Detect automatically", controller.languageSourceOptions[0]["text"])
            self.assertEqual("Other language (enter manually)",
                             controller.languageSourceOptions[-1]["text"])
            self.assertEqual("简体中文", controller.languageTargetOptions[0]["text"])
            self.assertEqual("Interface language: English", controller.localeLabel)
            self.assertEqual("", controller.localeNotice)

            before = len(changes)
            self.assertTrue(controller.setUiLocale("zh-CN"))
            self.assertGreater(len(changes), before, "切换后应通知 QML 刷新属性")
            self.assertEqual("zh-CN", controller.uiLocale)
            self.assertEqual("zh-CN", controller.effectiveUiLocale)

            self.assertTrue(controller.setUiLocale("en"))
            self.assertEqual("en", controller.uiLocale)
            self.assertEqual("en", controller.effectiveUiLocale)
            self.assertEqual(["Follow system", "简体中文", "English"],
                             [row["text"] for row in controller.localeOptions])
            self.assertEqual("Detect automatically",
                             controller.languageSourceOptions[0]["text"])
            self.assertEqual("Other language (enter manually)",
                             controller.languageSourceOptions[-1]["text"])
            self.assertEqual("简体中文", controller.languageTargetOptions[0]["text"])
            self.assertEqual("English", controller.languageTargetOptions[2]["text"])
            self.assertEqual("Interface language: English", controller.localeLabel)

    def test_controller_reports_a_refused_english_switch(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as empty:
            from native_launcher import TranslatorController
            settings = settings_at(raw)
            service = make_service(self, settings, system="zh_CN", translations=Path(empty))
            service.start(application())
            controller = TranslatorController(workspace=Path(raw) / "jobs", settings=settings,
                                              locale_service=service, restore=False)
            self.addCleanup(lambda: (controller.reader_timer.stop(), controller.shutdown()))
            self.assertFalse(controller.setUiLocale("en"))
            self.assertEqual("zh-CN", controller.effectiveUiLocale)
            self.assertTrue(controller.localeNotice)


class ShellSwitchTests(unittest.TestCase):
    """The small shell proof: switch locale without rebuilding product state."""

    ENGLISH_SOURCE = ("Mara returned to North Harbor before dawn. She carried the sealed "
                      "letter in her coat and waited quietly by the old lighthouse.\n\n") * 4

    def setUp(self):
        application()
        self.warnings: list[str] = []
        qInstallMessageHandler(lambda kind, context, message: self.warnings.append(message))

    def tearDown(self):
        qInstallMessageHandler(None)

    def build(self, root, system="zh_CN"):
        from native_launcher import TranslatorController
        settings = settings_at(root)
        service = LS.LocaleService(settings, system_locale=system)
        service.start(application())
        controller = TranslatorController(workspace=root / "jobs", settings=settings,
                                          locale_service=service, restore=False)
        source = root / "shell.txt"
        source.write_text(self.ENGLISH_SOURCE, encoding="utf-8")
        controller._select_source(source)
        preview = root / "preview.txt"
        preview.write_text("北港的钟声越过水面。\n\n玛拉把信放进口袋。", encoding="utf-8")
        controller._output_path = str(preview)
        controller._refresh_reader()
        engine = QQmlApplicationEngine()
        service.attach_engine(engine)
        engine.rootContext().setContextProperty("backend", controller)
        engine.load(QUrl.fromLocalFile(str(ROOT / "ui/Main.qml")))
        self.addCleanup(lambda: (controller.reader_timer.stop(), controller.shutdown(),
                                 service.shutdown()))
        self.assertTrue(engine.rootObjects(), self.warnings)
        return service, controller, engine, engine.rootObjects()[0]

    def state(self, controller):
        from epub_reader import cache_stats
        return {
            "controller": controller,
            "columns": controller._reading_columns,
            "column_ids": [column.columnId for column in controller._reading_columns],
            "output": controller._output_path,
            "source": controller._source_path,
            "target": controller.targetLanguage,
            "locked": controller.languageLocked,
            "cache": controller.readerCacheSummary,
            "generations": cache_stats(controller.storage_context.cache_root)["generations"],
        }

    def test_switch_preserves_controller_columns_path_and_cache(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller, engine, window = self.build(Path(raw))
            header = find(window, "settingsButton")
            shelf = find(window, "addShelfBook")
            count = find(window, "shelfCount")
            self.assertEqual("Settings", header.property("text"))
            self.assertEqual("+ Add book", shelf.property("text"))
            books = len(controller.bookshelf)
            self.assertEqual(QCoreApplication.translate("Main", "%n 本", "bookshelf count", books),
                             count.property("text"))
            before = self.state(controller)
            root_before = window

            with patch("urllib.request.urlopen", side_effect=AssertionError("network")), \
                    patch("socket.create_connection", side_effect=AssertionError("network")):
                self.assertTrue(controller.setUiLocale("zh-CN"))

            after = self.state(controller)
            self.assertEqual("设置", header.property("text"))
            self.assertEqual("+ 添加图书", shelf.property("text"))
            self.assertEqual(QCoreApplication.translate("Main", "%n 本", "bookshelf count", books),
                             count.property("text"))
            self.assertEqual("当前界面语言：简体中文", controller.localeLabel)
            self.assertIs(window, root_before)
            self.assertIs(engine.rootObjects()[0], root_before)
            self.assertIs(engine.rootContext().contextProperty("backend"), before["controller"])
            self.assertIs(after["columns"], before["columns"])
            self.assertEqual(before["column_ids"], after["column_ids"])
            self.assertEqual(before["output"], after["output"])
            self.assertEqual(before["source"], after["source"])
            self.assertEqual(before["target"], after["target"])
            self.assertEqual(before["locked"], after["locked"])
            # The cache *text* is locale-projected (Task 04); what must not
            # change is the underlying state, asserted via generations above.
            self.assertEqual(before["generations"], after["generations"])

    def test_switching_back_is_also_in_place(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller, engine, window = self.build(Path(raw), system="en_US")
            self.assertEqual("Settings", find(window, "settingsButton").property("text"))
            before = self.state(controller)
            self.assertTrue(controller.setUiLocale("zh-CN"))
            self.assertEqual("设置", find(window, "settingsButton").property("text"))
            after = self.state(controller)
            self.assertEqual(before["column_ids"], after["column_ids"])
            # The cache *text* is locale-projected (Task 04); what must not
            # change is the underlying state, asserted via generations above.
            self.assertEqual(before["generations"], after["generations"])

    def test_no_qml_binding_or_reference_errors_during_the_switch(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller, engine, window = self.build(Path(raw))
            self.assertTrue(controller.setUiLocale("en"))
            self.assertTrue(controller.setUiLocale("zh-CN"))
            self.assertTrue(controller.setUiLocale("system"))
            bad = [message for message in self.warnings
                   if any(token in message for token in QML_WARNINGS)]
            self.assertEqual([], bad)

    def test_locale_switch_does_not_touch_the_language_direction(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller, engine, window = self.build(Path(raw))
            direction = (controller.sourceLanguageId, controller.targetLanguageId,
                         controller.sourceLanguage, controller.targetLanguage,
                         controller.languageAuto)
            self.assertTrue(controller.setUiLocale("en"))
            self.assertEqual(direction, (controller.sourceLanguageId, controller.targetLanguageId,
                                         controller.sourceLanguage, controller.targetLanguage,
                                         controller.languageAuto))
            self.assertEqual("简体中文", controller.targetLanguage)
            source_picker = find(window, "languageSourceSelector")
            target_picker = find(window, "languageTargetSelector")
            self.assertEqual("Detect automatically", source_picker.property("currentText"))
            self.assertEqual("简体中文", target_picker.property("currentText"))

    def test_settings_language_selector_has_accessible_names_in_both_locales(self):
        source = (ROOT / "ui/Main.qml").read_text(encoding="utf-8")
        self.assertIn('Accessible.name: qsTr("界面语言")', source)
        with tempfile.TemporaryDirectory() as raw:
            service, controller, engine, window = self.build(Path(raw))
            popup = find(window, "settingsPopup")
            QMetaObject.invokeMethod(popup, "open")
            application().processEvents()
            contents = popup.property("contentItem")
            picker = find(contents, "interfaceLanguageChoice")
            heading = find(contents, "interfaceLanguageHeading")
            self.assertEqual(3, picker.property("count"))
            self.assertEqual("en", picker.property("currentValue"))
            self.assertEqual("English", picker.property("currentText"))
            self.assertEqual("Interface language", heading.property("text"))
            self.assertEqual("Interface language",
                             QCoreApplication.translate("Main", "界面语言"))

            self.assertTrue(controller.setUiLocale("zh-CN"))
            self.assertEqual("简体中文", picker.property("currentText"))
            self.assertEqual("界面语言", heading.property("text"))
            self.assertEqual("界面语言", QCoreApplication.translate("Main", "界面语言"))
            self.assertTrue(controller.setUiLocale("system"))
            # This fixture injects a Chinese system, so following it returns to Chinese.
            self.assertEqual("跟随系统", picker.property("currentText"))
            self.assertEqual("界面语言", heading.property("text"))


if __name__ == "__main__":
    unittest.main()
