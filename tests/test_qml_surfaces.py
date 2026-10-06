"""Task 03: complete QML English coverage for the supported macOS app.

Everything runs offscreen on temporary settings, a temporary workspace and
synthetic prose.  No translation or AI API is called, no network access is
allowed, and no real book, task, cache or installed app is touched.

The native Cocoa shell, WKWebView overlays and IME behaviour are **not**
verified here: this file only exercises QML loaded through the offscreen
platform plugin, where the reader columns have no real ``reading`` object.
"""
from __future__ import annotations

import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "runtime/lib"), str(ROOT / "scripts")):
    sys.path.insert(0, entry)

from PySide6.QtCore import (Q_ARG, QCoreApplication, QMetaObject, QObject, QSettings,
                            QUrl, qInstallMessageHandler)  # noqa: E402
from PySide6.QtQml import QQmlApplicationEngine, QQmlComponent  # noqa: E402
from PySide6.QtQuick import QQuickItem  # noqa: E402,F401  (registers the type)
from PySide6.QtWidgets import QApplication  # noqa: E402

import i18n_coverage  # noqa: E402
import locale_service as LS  # noqa: E402
from i18n_common import messages, parse_catalogue, sources  # noqa: E402

UI = ROOT / "ui"
FIXTURES = ROOT / "tests/fixtures"
COMPILED = ROOT / "i18n/whaleread_en.qm"

SOURCE = ("Mara returned to North Harbor before dawn. She carried the sealed letter in her coat "
          "and waited quietly by the old lighthouse.\n\n") * 5

QML_WARNINGS = ("ReferenceError", "TypeError", "Unable to assign", "is not defined", "Binding loop")
HAS_HAN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")

# Autonyms that must never be translated.
AUTONYMS = ("简体中文", "繁體中文", "English", "日本語", "한국어", "Français", "Deutsch",
            "Español", "Português", "Italiano", "Русский", "العربية", "Nederlands", "Polski",
            "Türkçe", "Tiếng Việt", "ไทย", "हिन्दी")

# Every user-accessible popup of the supported app, by stable objectName.
# Popups owned by the main window.  The reader's own popups are opened through
# a directly loaded ReaderPane, because the offscreen Repeater never builds a
# delegate for a synthetic column.
POPUPS = ("askDialog", "settingsPopup", "clearReaderCacheDialog", "editionDialog",
          "languageDialog", "errorDialog", "reviewPanel", "researchTermDialog")
READER_POPUPS = ("annotationPopup", "chaptersPopup", "bookmarksPopup", "openBooksPopup",
                 "readingAppearancePopup")


def application():
    return QApplication.instance() or QApplication([])


def find(scope, name):
    found = scope.findChild(QObject, name)
    if found is None:
        raise AssertionError(f"QML 对象不存在：{name}")
    return found


def han_only(texts, keep=()):
    return [value for value in texts if value and HAS_HAN.search(value)
            and not any(token in value for token in keep)]


class QmlHarness(unittest.TestCase):
    """Shared offscreen app with one synthetic column and no network."""

    warnings: list[str] = []

    # Python-side dynamic state rendered into QML.  Localizing it is Task 04.
    DYNAMIC = ("导入论文后按页解析。公式和图表保留原图；扫描文字核对后再翻译。",
               "翻译完成后，对照原文检查译名；建议由你决定是否采用。",
               "尚未统计")

    @classmethod
    def setUpClass(cls):
        application()
        qInstallMessageHandler(lambda kind, context, message: cls.warnings.append(message))
        cls._tmp = tempfile.TemporaryDirectory(prefix="whaleread-qml-")
        root = Path(cls._tmp.name)
        from native_launcher import TranslatorController
        cls.root = root
        cls.settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        # Surface assertions compare an explicit Chinese baseline with English.
        # Do not make their shared QML engine depend on the product's clean-user
        # startup default.
        cls.settings.setValue("interface/locale", "zh-CN")
        cls.settings.sync()
        cls.service = LS.LocaleService(cls.settings, system_locale="zh_CN")
        cls.service.start(application())
        cls.controller = TranslatorController(workspace=root / "jobs", settings=cls.settings,
                                              locale_service=cls.service, restore=False)
        source = root / "shell.txt"
        source.write_text(SOURCE, encoding="utf-8")
        cls.controller._select_source(source)
        preview = root / "preview.txt"
        preview.write_text("北港的钟声越过水面。\n\n玛拉把信放进口袋。", encoding="utf-8")
        cls.controller._output_path = str(preview)
        cls.controller._refresh_reader()
        cls.engine = QQmlApplicationEngine()
        cls.service.attach_engine(cls.engine)
        cls.engine.rootContext().setContextProperty("backend", cls.controller)
        cls.engine.load(QUrl.fromLocalFile(str(UI / "Main.qml")))
        assert cls.engine.rootObjects(), cls.warnings
        cls.window = cls.engine.rootObjects()[0]

    @classmethod
    def tearDownClass(cls):
        qInstallMessageHandler(None)
        cls.controller.reader_timer.stop()
        cls.controller.shutdown()
        cls.service.shutdown()
        # Destroy the QML tree while its context controller is still alive; a
        # lingering engine window would otherwise steal focus from later tests.
        import shiboken6 as sip
        sip.delete(cls.engine)
        cls.window = None
        application().processEvents()
        cls._tmp.cleanup()

    def setUp(self):
        type(self).warnings.clear()
        self._loaded = []
        self.assertTrue(self.controller.setUiLocale("zh-CN"))
        application().processEvents()

    def english(self):
        self.assertTrue(self.controller.setUiLocale("en"))
        application().processEvents()

    def open_popup(self, name):
        popup = find(self.window, name)
        QMetaObject.invokeMethod(popup, "open")
        application().processEvents()
        self.assertTrue(popup.property("opened"), f"{name} 没有打开")
        return popup

    def close_popup(self, popup):
        QMetaObject.invokeMethod(popup, "close")
        application().processEvents()

    def popup_texts(self, popup):
        """Every string property of the opened popup and its descendants."""
        values: list[str] = []
        for item in [popup] + popup.findChildren(QObject):
            for prop in ("text", "title", "placeholderText"):
                if item.metaObject().indexOfProperty(prop) < 0:
                    continue
                value = item.property(prop)
                if isinstance(value, str) and value.strip():
                    values.append(value)
        return values

    def option_rows(self, combo):
        """(label, value) of every row of a QML ComboBox.

        Qt does not let a model of plain maps be introspected from Python, so
        the rows are read through a tiny QML helper that uses the ComboBox's own
        ``textAt``/``valueAt`` accessors.
        """
        helper = self.load_pane("OptionProbe.qml", combo=combo)
        QMetaObject.invokeMethod(helper, "collect")
        application().processEvents()
        labels = helper.property("labels").toVariant()
        values = helper.property("values").toVariant()
        return [str(label) for label in labels], [str(value) for value in values]

    def load_pane(self, name="ReaderPane.qml", **properties):
        """Load one pane directly with a real reading object.

        Main.qml's Repeater does not instantiate its delegate for a synthetic
        single column, so the reader surface is exercised on its own.
        """
        path = (UI / name) if (UI / name).is_file() else (FIXTURES / name)
        component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(path)))
        self.assertEqual(QQmlComponent.Status.Ready, component.status(), component.errorString())
        obj = component.createWithInitialProperties(properties)
        self.assertIsNotNone(obj, component.errorString())
        # Keep the component and object alive: the engine owns them, and the
        # object must outlive the assertions.
        self._loaded.append((component, obj))
        return obj

    def reader(self):
        return self.controller.readingColumns[0]

    def check_popup(self, scope, name):
        for locale in ("zh-CN", "en"):
            self.assertTrue(self.controller.setUiLocale(locale))
            application().processEvents()
            popup = find(scope, name)
            QMetaObject.invokeMethod(popup, "open")
            application().processEvents()
            self.assertTrue(popup.property("opened"), (name, locale))
            QMetaObject.invokeMethod(popup, "close")
            application().processEvents()

    def qml_errors(self):
        return [message for message in type(self).warnings
                if any(token in message for token in QML_WARNINGS)]


class CoverageCheckTests(QmlHarness):
    def test_every_qml_literal_is_wrapped_and_extracted(self):
        wrapped, missing, allowed = i18n_coverage.check_qml(sorted(UI.glob("*.qml")))
        self.assertEqual([], missing)
        self.assertGreater(wrapped, 400)
        self.assertEqual(2, len(allowed), allowed)  # product name and wordmark only
        self.assertEqual([], i18n_coverage.check_js(sorted(UI.glob("*.js"))))

    def test_coverage_check_reports_an_unwrapped_literal(self):
        with tempfile.TemporaryDirectory() as raw:
            probes = []
            for index, literal in enumerate(('"双引号漏译"', "'单引号漏译'", "`模板漏译`")):
                probe = Path(raw) / f"Probe{index}.qml"
                probe.write_text(f"import QtQuick\nItem {{ property string s: {literal} }}\n",
                                 encoding="utf-8")
                probes.append(probe)
            wrapped, missing, _ = i18n_coverage.check_qml(probes)
            self.assertEqual(0, wrapped)
            self.assertEqual(3, len(missing))
            self.assertTrue(any("单引号漏译" in finding for finding in missing))
            self.assertTrue(any("模板漏译" in finding for finding in missing))

    def test_coverage_check_reports_a_wrapped_but_unextracted_literal(self):
        with tempfile.TemporaryDirectory() as raw:
            probe = Path(raw) / "Probe.qml"
            probe.write_text('import QtQuick\nItem { property string s: qsTr("尚未提取的文字") }\n',
                             encoding="utf-8")
            wrapped, missing, _ = i18n_coverage.check_qml([probe])
            self.assertEqual(1, wrapped)
            self.assertEqual(1, len(missing))
            self.assertIn("qsTr 未按 Probe 上下文提取", missing[0])

    def test_coverage_check_requires_the_correct_qml_context(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            probe = root / "WrongContext.qml"
            probe.write_text('import QtQuick\nItem { property string s: qsTr("关闭") }\n',
                             encoding="utf-8")
            wrapped, missing, _ = i18n_coverage.check_qml([probe])
            self.assertEqual(1, wrapped)
            self.assertEqual(1, len(missing))
            self.assertIn("WrongContext 上下文", missing[0])

    def test_coverage_check_reports_hardcoded_javascript_text(self):
        with tempfile.TemporaryDirectory() as raw:
            probe = Path(raw) / "Probe.js"
            probe.write_text(
                'const a = "双引号文字"; const b = \'单引号文字\'; const c = `模板文字`;\n',
                encoding="utf-8")
            findings = i18n_coverage.check_js([probe])
            self.assertEqual(3, len(findings))
            self.assertTrue(any("单引号文字" in finding for finding in findings))
            self.assertTrue(any("模板文字" in finding for finding in findings))

    def test_allowlist_stays_narrow(self):
        self.assertEqual({("ui/Main.qml", "鲸读"), ("ui/Main.qml", "鲸 读")},
                         set(i18n_coverage.ALLOWED))

    def test_catalogue_covers_every_declared_qsTr_literal(self):
        declared = set()
        for path in sources():
            text = path.read_text(encoding="utf-8")
            for match in re.finditer(r"qsTr\(\s*\"((?:[^\"\\]|\\.)*)\"", text):
                declared.add(match.group(1).replace("\\n", "\n").replace('\\"', '"'))
        known = {message.findtext("source") or ""
                 for _, message in messages(parse_catalogue())}
        self.assertEqual(set(), {value for value in declared if value not in known})

    def test_extraction_refuses_to_drop_reviewed_translations(self):
        from i18n_common import TranslationError, guard_catalogue_loss
        guard_catalogue_loss(497, 490)          # a small change is fine
        guard_catalogue_loss(497, 497)
        guard_catalogue_loss(0, 40)             # first run from scratch
        with self.assertRaises(TranslationError):
            guard_catalogue_loss(497, 35)
        with self.assertRaises(TranslationError):
            guard_catalogue_loss(497, 100)

    def test_failed_extraction_keeps_the_reviewed_catalogue_byte_identical(self):
        import i18n_extract

        def catalogue(count):
            rows = "".join(
                f"<message><source>s{index}</source><translation>t{index}</translation></message>"
                for index in range(count))
            return f'<?xml version="1.0" encoding="utf-8"?><TS><context><name>x</name>{rows}</context></TS>'

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "reviewed.ts"
            source = root / "Probe.qml"
            target.write_text(catalogue(20), encoding="utf-8")
            source.write_text("import QtQuick\nItem {}\n", encoding="utf-8")
            before = target.read_bytes()

            def fake_lupdate(command, **_kwargs):
                Path(command[-1]).write_text(catalogue(1), encoding="utf-8")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            with mock.patch.object(i18n_extract, "sources", return_value=[source]), \
                    mock.patch.object(i18n_extract, "tool", return_value="fake-lupdate"), \
                    mock.patch.object(i18n_extract.subprocess, "run", side_effect=fake_lupdate), \
                    mock.patch.object(sys, "argv", ["i18n_extract.py", "--output", str(target)]):
                with self.assertRaises(i18n_extract.TranslationError):
                    i18n_extract.main()
            self.assertEqual(before, target.read_bytes())
            self.assertEqual([], list(root.glob(".reviewed-*.ts")))

    def test_no_script_uses_a_network_api(self):
        for path in sorted((ROOT / "scripts").glob("i18n_*.py")):
            text = path.read_text(encoding="utf-8")
            for token in ("urllib", "requests", "socket", "http.client", "curl"):
                self.assertNotIn(token, text, f"{path.name} 不应联网")


class SurfaceTranslationTests(QmlHarness):
    HEADER = ("settingsButton", "reviewHeader", "readerToggle", "researchModeToggle", "addShelfBook")
    READER = ("chooseColumnBook", "addParallelColumn", "closeParallelColumn", "openTxtButton",
              "readerAnnotations", "readerFocus", "readerMenu", "readerBookmarks", "readerFontPicker",
              "readerToc")

    def test_header_and_shelf_switch_in_place(self):
        before = {name: find(self.window, name).property("text") for name in self.HEADER}
        self.assertTrue(all(HAS_HAN.search(value) for value in before.values()), before)
        self.english()
        after = {name: find(self.window, name).property("text") for name in self.HEADER}
        self.assertEqual("Settings", after["settingsButton"])
        self.assertEqual("Review", after["reviewHeader"])
        self.assertEqual("+ Add book", after["addShelfBook"])
        self.assertEqual([], han_only(after.values()), after)

    def test_translation_card_shows_legacy_language_values(self):
        card = find(self.window, "languageDirectionButton").property("text")
        self.assertIn("英语", card)
        self.english()
        card = find(self.window, "languageDirectionButton").property("text")
        # The direction carries legacy task values (data, not interface text),
        # so the card keeps them while its action suffix follows the locale.
        self.assertIn("英语 → 简体中文", card)
        self.assertNotIn("调整语言方向", card)
        self.assertIn("adjust direction", card)

    def test_reader_pane_controls_are_english(self):
        pane = self.load_pane(reading=self.reader())
        self.assertEqual("换书", find(pane, "chooseColumnBook").property("text"))
        self.english()
        texts = {name: find(pane, name).property("text") for name in self.READER}
        self.assertEqual([], han_only(texts.values(), keep=("•••",)), texts)
        self.assertEqual(["+ Pane", "× Pane", "Annotate", "Immersive reading"],
                         [texts["addParallelColumn"], texts["closeParallelColumn"],
                          texts["readerAnnotations"], texts["readerFocus"]])
        self.assertEqual("0 bookmarks", texts["readerBookmarks"])

    def test_language_direction_dialog_keeps_autonyms(self):
        dialog = self.open_popup("languageDialog")
        source = find(dialog, "languageSourceSelector")
        target = find(dialog, "languageTargetSelector")
        self.assertEqual("自动识别", source.property("currentText"))
        self.english()
        self.assertEqual("Detect automatically", source.property("currentText"))
        self.assertEqual("简体中文", target.property("currentText"))
        labels, values = self.option_rows(target)
        for autonym in AUTONYMS:
            self.assertIn(autonym, labels, autonym)
        self.assertEqual("Detect automatically", self.option_rows(source)[0][0])
        self.assertNotIn("auto", values)
        self.assertIn("ja", values)
        self.assertEqual("custom", values[-1])
        self.assertIn("Other language", labels[-1])

    def test_settings_language_selector_and_accessible_names(self):
        popup = self.open_popup("settingsPopup")
        self.assertEqual("界面语言", find(popup, "interfaceLanguageHeading").property("text"))
        picker = find(popup, "interfaceLanguageChoice")
        self.assertEqual(["跟随系统", "简体中文", "English"], self.option_rows(picker)[0])
        self.english()
        self.assertEqual("Interface language",
                         find(popup, "interfaceLanguageHeading").property("text"))
        labels, values = self.option_rows(picker)
        self.assertEqual(["Follow system", "简体中文", "English"], labels)
        self.assertEqual(["system", "zh-CN", "en"], values)
        self.assertIn('Accessible.name: qsTr("界面语言")',
                      (UI / "Main.qml").read_text(encoding="utf-8"))

    def test_reader_native_surfaces_remain_hidden_until_every_in_window_overlay_closes(self):
        # This checks coordination, not native WebKit painting. The exact
        # candidate also needs a real EPUB/window replay.
        names = ('welcomePopup', 'legalPopup', 'settingsPopup', 'askDialog', 'reviewPanel')
        for name in names:
            self.close_popup(find(self.window, name))
        self.assertFalse(self.window.property('nativeEpubViewsHidden'))
        settings = self.open_popup('settingsPopup')
        ask = self.open_popup('askDialog')
        self.assertTrue(self.window.property('nativeEpubViewsHidden'))
        self.close_popup(ask)
        self.assertTrue(self.window.property('nativeEpubViewsHidden'))
        self.close_popup(settings)
        self.assertFalse(self.window.property('nativeEpubViewsHidden'))
        for name in ('reviewPanel', 'legalPopup'):
            popup = self.open_popup(name)
            self.assertTrue(self.window.property('nativeEpubViewsHidden'))
            self.close_popup(popup)
            self.assertFalse(self.window.property('nativeEpubViewsHidden'))
        self.assertEqual([], self.qml_errors())

    def test_profile_editor_preserves_selected_profile_after_forgetting_key(self):
        from credential_store import CredentialError, MemoryCredentialStore
        from native_launcher import TranslatorController
        from PySide6.QtGui import QColor
        with tempfile.TemporaryDirectory(prefix='whaleread-profile-editor-') as raw:
            root = Path(raw)
            settings = QSettings(str(root / 'settings.ini'), QSettings.IniFormat)
            owner = TranslatorController(workspace=root / 'Tasks', settings=settings,
                credential_store=MemoryCredentialStore(), restore=False)
            try:
                pid = owner.providers.save('', 'Fictional Model',
                    'https://provider.example/v1', 'fictional-model', 'qa-fictional-token', True)
                panel = self.load_pane('ProviderSettings.qml', backend=owner,
                                      ink=QColor('#202020'), muted=QColor('#707070'))
                picker = find(panel, 'modelEditChoice')
                panel.setProperty('editId', pid)
                labels, values = self.option_rows(picker)
                picker.setProperty('currentIndex', values.index(pid))
                QMetaObject.invokeMethod(picker, 'activated', Q_ARG(int, values.index(pid)))
                application().processEvents()
                self.assertEqual('Fictional Model', find(panel, 'modelName').property('text'))
                QMetaObject.invokeMethod(find(panel, 'forgetModelKey'), 'clicked')
                application().processEvents()
                application().processEvents()
                self.assertEqual(pid, picker.property('currentValue'))
                self.assertEqual(pid, panel.property('editId'))
                self.assertEqual('Fictional Model', find(panel, 'modelName').property('text'))
                with self.assertRaises(CredentialError):
                    owner.providers.key(pid)
                self.assertFalse(owner.providers.profile(pid)['key_saved'])
                self.assertEqual([], self.qml_errors())
            finally:
                owner.shutdown()

    def test_both_local_presets_are_available_and_read_only_in_the_editor(self):
        from PySide6.QtGui import QColor
        panel = self.load_pane('ProviderSettings.qml', backend=self.controller,
                              ink=QColor('#202020'), muted=QColor('#707070'))
        picker = find(panel, 'modelEditChoice')
        labels, values = self.option_rows(picker)
        for pid, label, model in (
                ('local_7b', 'Hy-MT2 7B · Ollama', 'jingdu-hy-mt2:7b-q4'),
                ('local_1_8b', 'Hy-MT2 1.8B · Ollama', 'jingdu-hy-mt2:1.8b-q8')):
            with self.subTest(pid=pid):
                index = values.index(pid)
                self.assertEqual(labels[index], label)
                picker.setProperty('currentIndex', index)
                QMetaObject.invokeMethod(picker, 'activated', Q_ARG(int, index))
                application().processEvents()
                self.assertTrue(panel.property('editingPreset'))
                self.assertEqual(find(panel, 'modelID').property('text'), model)
                for name in ('modelName', 'modelEndpoint', 'modelID'):
                    self.assertTrue(find(panel, name).property('readOnly'))
                self.assertFalse(find(panel, 'saveModelSettings').property('enabled'))
                self.assertFalse(find(panel, 'modelAuthRequired').property('enabled'))
                self.assertFalse(find(panel, 'forgetModelKey').property('visible'))
        picker.setProperty('currentIndex', 0)
        QMetaObject.invokeMethod(picker, 'activated', Q_ARG(int, 0))
        application().processEvents()
        self.assertFalse(panel.property('editingPreset'))
        self.assertFalse(find(panel, 'modelID').property('readOnly'))
        self.assertTrue(find(panel, 'saveModelSettings').property('enabled'))
        self.assertEqual([], self.qml_errors())

    def test_ask_dialog_is_english(self):
        popup = self.open_popup("askDialog")
        self.english()
        texts = self.popup_texts(popup)
        self.assertIn("Back to reading", texts)
        self.assertIn("API settings", texts)
        picker = find(popup, "askAnswerLocale")
        labels, values = self.option_rows(picker)
        self.assertEqual(["Follow interface language", "Chinese", "English"], labels)
        self.assertEqual(["follow_ui", "zh-CN", "en"], values)
        assistant = self.controller.askAI
        try:
            assistant._frozen_answer_locale = "en"
            assistant.process = object()
            assistant.changed.emit()
            application().processEvents()
            effective = find(popup, "askAnswerLocaleEffective")
            self.assertTrue(effective.property("visible"))
            self.assertEqual("This request uses English", effective.property("text"))
        finally:
            assistant.process = None
            assistant._frozen_answer_locale = ""
            assistant.changed.emit()
        self.assertEqual([], han_only(texts, keep=("DGX", "Qwen", "API")), han_only(texts))

    def test_review_panel_is_english(self):
        popup = self.open_popup("reviewPanel")
        self.english()
        texts = self.popup_texts(popup)
        self.assertIn("Name list · 0", texts)
        self.assertEqual([], [value for value in han_only(texts)
                              if value not in self.DYNAMIC], han_only(texts))

    def test_research_dialog_is_english_and_states_its_limits(self):
        popup = self.open_popup("researchTermDialog")
        notice = find(popup, "researchScopeNotice")
        self.assertIn("简体中文", notice.property("text"))
        self.assertIn("所选 OCR 服务", notice.property("text"))
        self.assertIn("结果仍需人工核对", notice.property("text"))
        self.english()
        self.assertIn("Simplified Chinese", notice.property("text"))
        self.assertIn("selected OCR service", notice.property("text"))
        self.assertIn("Batch mode skips page approval", notice.property("text"))
        self.assertIn("results still need human review", notice.property("text"))
        texts = self.popup_texts(popup)
        # The research pane's dynamic status line is Python-side state (Task 04);
        # every interface literal in this dialog must already be English.
        self.assertEqual([], [value for value in han_only(texts, keep=("简体中文",))
                              if value not in self.DYNAMIC], han_only(texts))

    def test_confirmation_dialogs_are_english(self):
        for name, expected in (("clearReaderCacheDialog", ("Cancel", "OK")),
                               ("editionDialog", ("Cancel", "OK"))):
            popup = self.open_popup(name)
            self.english()
            texts = self.popup_texts(popup)
            self.assertTrue(texts, name)
            for label in expected:
                self.assertIn(label, texts, (name, texts))
            self.assertEqual([], han_only(texts, keep=("尚未统计",)),
                             (name, texts))
            self.close_popup(popup)

    def test_error_dialog_is_english_and_wraps_long_text(self):
        long_error = ("This is a deliberately long English failure message that must wrap inside "
                      "the dialog instead of being clipped or breaking the window layout. ") * 3
        import ui_messages as UM
        self.controller._error = UM.Message("", (), long_error)
        popup = self.open_popup("errorDialog")
        self.english()
        self.assertEqual("Something needs attention", popup.property("title"))
        self.assertEqual("OK", find(popup, "errorDialogAcknowledge").property("text"))
        # An unrecognized failure shows a localized summary and keeps the raw
        # diagnostic verbatim beside it (Task 04).
        self.assertTrue(any(value.startswith("The operation did not finish")
                            for value in self.popup_texts(popup)), self.popup_texts(popup))
        self.assertEqual(long_error, self.controller.errorDetail)
        self.assertIn(long_error, find(popup, "errorDialogDetail").property("text"))
        self.assertEqual([], self.qml_errors())
        self.controller.dismissError()

    def test_every_popup_has_a_stable_object_name(self):
        for name in POPUPS:
            popup = find(self.window, name)
            self.assertIsNotNone(popup, name)

    def test_every_popup_opens_without_qml_errors(self):
        """Each main-window popup opens once per locale.

        Offscreen Qt can delay a popup window indefinitely, so the sweep is
        bounded to the popups that belong to the main window.
        """
        for name in POPUPS:
            for locale in ("zh-CN", "en"):
                self.assertTrue(self.controller.setUiLocale(locale))
                application().processEvents()
                popup = self.open_popup(name)
                self.assertTrue(popup.property("opened"), name)
                self.close_popup(popup)
        self.assertTrue(self.controller.setUiLocale("zh-CN"))

    def test_reader_popups_open(self):
        """Reader-owned popups, exercised on a directly loaded ReaderPane.

        ``annotationPopup`` is a ``Popup.Item`` that only shows for a real text
        selection, so it is checked through its window-level sibling popups and
        its own translated controls instead of being force-opened.
        """
        pane = self.load_pane(reading=self.reader())
        # ``readingAppearancePopup`` is loaded on its own: ReaderPane only
        # builds it once it is inside a window, which offscreen never happens.
        appearance = self.load_pane("ReadingAppearancePopup.qml", host=None)
        self.assertEqual("阅读排版", find(appearance, "closeReadingAppearance").property("text")
                         and QCoreApplication.translate("ReadingAppearancePopup", "阅读排版"))
        self.english()
        self.assertEqual("Close", find(appearance, "closeReadingAppearance").property("text"))
        # The pane's own window popups are opened on the directly loaded pane;
        # they are never built inside the main window in this fixture, and a
        # window-less pane cannot show them, so their controls are checked.
        for name, control in (("chaptersPopup", "readerToc"),
                              ("bookmarksPopup", "readerBookmarks"),
                              ("openBooksPopup", "chooseColumnBook")):
            popup = find(pane, name)
            self.assertFalse(popup.property("opened"), name)
            self.assertTrue(popup.property("modal"), name)
            self.assertNotEqual("", find(pane, control).property("text"), control)
        self.english()
        self.assertEqual("Contents", find(pane, "readerToc").property("text"))
        self.assertEqual("Bookmarks", find(pane, "bookmarksPopup").findChild(
            QObject, "bookmarksPopupTitle").property("text"))
        self.assertEqual("Change this pane's content",
                         find(pane, "openBooksPopup").findChild(
                             QObject, "openBooksPopupTitle").property("text"))
        annotation = find(pane, "annotationPopup")
        self.assertTrue(annotation.property("modal"))
        for name in ("saveAnnotationButton", "saveAndReviewAnnotationButton"):
            button = find(annotation, name)
            self.assertNotEqual("", button.property("text"))
        self.english()
        self.assertEqual("Save annotation",
                         find(annotation, "saveAnnotationButton").property("text"))
        self.assertEqual("Save and review",
                         find(annotation, "saveAndReviewAnnotationButton").property("text"))
        self.assertEqual("Annotate this translation",
                         find(annotation, "annotationTitle").property("text"))

    def test_positioned_paper_full_text_dialog_is_translated(self):
        """The dialog belongs to a research page; its texts are checked directly.

        A ``Popup.Window`` without a real parent window cannot be opened
        offscreen, so the dialog is loaded and its strings are read instead.
        """
        component = QQmlComponent(self.engine, QUrl.fromLocalFile(str(UI / "PositionedPaper.qml")))
        self.assertEqual(QQmlComponent.Status.Ready, component.status(), component.errorString())
        paper = component.createWithInitialProperties({
            "position": {"image": "", "pageWidth": 100, "pageHeight": 140, "blocks": []},
            "original": "", "pageWidth": 100, "pageHeight": 140})
        self.assertIsNotNone(paper, component.errorString())
        self._loaded.append((component, paper))
        dialog = find(paper, "positionFullText")
        self.assertEqual("原文与译文", dialog.property("title"))
        self.english()
        self.assertEqual("Source and translation", dialog.property("title"))
        texts = [find(dialog, name).property("text")
                 for name in ("positionFullTextSource", "positionFullTextTranslation")]
        self.assertEqual([], han_only(texts), texts)
        # The expanded-state dialog texts are enough; the in-page affordance is
        # covered by the QML coverage check.
        self.assertEqual("Source", QCoreApplication.translate("PositionedPaper", "原文"))
        self.assertEqual("Translation", QCoreApplication.translate("PositionedPaper", "译文"))

    def test_annotation_category_picker_is_localized_with_stable_values(self):
        pane = self.load_pane(reading=self.reader())
        combo = find(pane, "annotationCategory")
        picker = self.controller.postEditor.categoryOptions
        self.assertEqual(["疑似误译", "人名 / 地名", "漏译 / 多译", "数字 / 时间", "语气 / 表达",
                          "其他"], [row["text"] for row in picker])
        self.assertEqual([row["text"] for row in picker], self.option_rows(combo)[0])
        self.assertEqual(["mistranslation", "names_places", "omission", "numbers_time",
                          "tone", "other"], [row["value"] for row in picker])
        self.english()
        picker = self.controller.postEditor.categoryOptions
        self.assertEqual(["Suspected mistranslation", "Names / places", "Omission / addition",
                          "Numbers / time", "Tone / expression", "Other"],
                         [row["text"] for row in picker])
        self.assertEqual([row["text"] for row in picker], self.option_rows(combo)[0])
        self.assertEqual(["mistranslation", "names_places", "omission", "numbers_time",
                          "tone", "other"], [row["value"] for row in picker])

    def test_switching_locale_twice_changes_no_product_state(self):
        before = (self.controller._output_path, self.controller._source_path,
                  [column.columnId for column in self.controller._reading_columns],
                  self.controller.targetLanguage, self.controller.readerCacheSummary)
        for locale in ("en", "zh-CN", "en", "system"):
            self.assertTrue(self.controller.setUiLocale(locale))
        after = (self.controller._output_path, self.controller._source_path,
                 [column.columnId for column in self.controller._reading_columns],
                 self.controller.targetLanguage, self.controller.readerCacheSummary)
        self.assertEqual(before, after)


class UtilitySurfaceTests(QmlHarness):
    def test_positioned_paper_dialog_literals_are_wrapped(self):
        text = (UI / "PositionedPaper.qml").read_text(encoding="utf-8")
        for literal in ("展开全文", "原文与译文", "关闭", "原文", "译文", "本段尚未翻译"):
            self.assertIn(f'qsTr("{literal}")', text, literal)
        self.english()
        self.assertEqual("Source and translation",
                         QCoreApplication.translate("PositionedPaper", "原文与译文"))
        self.assertEqual("Show full text",
                         QCoreApplication.translate("PositionedPaper", "展开全文"))

    def test_reading_mode_bar_labels_and_tooltip(self):
        self.english()
        for source, expected in (("译文", "Translation"), ("原文", "Source"), ("双语", "Bilingual"),
                                 ("交换左右", "Swap sides")):
            self.assertEqual(expected, QCoreApplication.translate("ReadingModeBar", source))
        self.assertEqual(
            "This book has no verifiable source-to-translation passage mapping",
            QCoreApplication.translate("ReadingModeBar", "这本书没有可验证的原译段落映射"))
        self.assertEqual(
            "Available only when source and translation align reliably by passage",
            QCoreApplication.translate("ReadingModeBar", "只在原文与译文能按段落稳定对应时开放"))

    def test_margin_note_labels_and_placeholder(self):
        self.english()
        for source, expected in (("页边随笔", "Margin notes"), ("收起", "Collapse"),
                                 ("段落标色", "Passage colour"), ("删除标记", "Remove mark"),
                                 ("保存", "Save")):
            self.assertEqual(expected, QCoreApplication.translate("ReadingMarginNote", source))
        self.assertEqual("Jot something down, or just mark the colour without writing.",
                         QCoreApplication.translate("ReadingMarginNote",
                                                    "随手记一点东西，也可以只标色不写字。"))

    def test_appearance_popup_labels(self):
        self.english()
        for source, expected in (("阅读排版", "Reading layout"), ("纸张背景", "Paper background"),
                                 ("宣纸", "Xuan paper"), ("暖米", "Warm cream"),
                                 ("雾灰", "Misty grey"), ("深夜", "Night"),
                                 ("阅读亮度", "Reading brightness"),
                                 ("译文行距", "Translation line spacing"), ("默认", "Default")):
            self.assertEqual(expected, QCoreApplication.translate("ReadingAppearancePopup", source))

    def test_epub_workspace_and_pane_controls(self):
        self.english()
        for source, expected in (("换书", "Change book"), ("+ 栏", "+ Pane"), ("× 栏", "× Pane"),
                                 ("同步滚动", "Linked scrolling"),
                                 ("独立滚动", "Independent scrolling"),
                                 ("‹ 一起上一节", "‹ Previous section together")):
            self.assertEqual(expected, QCoreApplication.translate("EpubWorkspace", source))
        for source, expected in (("原文对照", "Compare source"), ("添加书签", "Add bookmark"),
                                 ("EPUB 译本", "EPUB translation"), ("移除", "Remove"),
                                 ("划线校阅…", "Review selection…")):
            self.assertEqual(expected, QCoreApplication.translate("EpubPane", source))

    def test_injected_epub_script_receives_labels_instead_of_hardcoding(self):
        source = (UI / "EpubAnnotations.js").read_text(encoding="utf-8")
        self.assertNotIn("'当前段'", source)
        self.assertNotIn("'相邻原文段'", source)
        self.assertIn("labels.current", source)
        self.assertIn("labels.open", source)
        self.english()
        self.assertEqual("Current passage", QCoreApplication.translate("EpubPane", "当前段"))
        self.assertEqual("Adjacent source passage",
                         QCoreApplication.translate("EpubPane", "相邻原文段"))
        self.assertEqual("Margin notes", QCoreApplication.translate("EpubPane", "页边随笔"))

    def test_native_book_view_failure_message(self):
        self.english()
        self.assertEqual("Reading interaction failed to start. Reopen this section.",
                         QCoreApplication.translate(
                             "NativeBookView", "阅读交互初始化失败，请重新打开此节。"))

    def test_numerus_messages_use_zero_one_and_many_forms(self):
        self.english()
        forms = {number: QCoreApplication.translate("ReaderPane", "%n 书签", "bookmark count", number)
                 for number in (0, 1, 2, 5)}
        self.assertEqual("0 bookmarks", forms[0])
        self.assertEqual("1 bookmark", forms[1])
        self.assertEqual("2 bookmarks", forms[2])
        self.assertEqual("5 bookmarks", forms[5])
        cases = (
            ("ResearchPane", "/ %n 页", "page count", "/ 1 page", "/ 2 pages"),
            ("ResearchPane", "本页还有 %n 段待翻译。", "remaining passages on page",
             "1 passage on this page is still waiting to be translated.",
             "2 passages on this page are still waiting to be translated."),
            ("ReviewPane", "（%n 段）", "variant passage count", "(1 passage)", "(2 passages)"),
            ("ReviewPane", "已解决标注 · %n", "resolved annotation count",
             "Resolved annotation · 1", "Resolved annotations · 2"),
            ("ReviewPane", "%1 · %n 段", "ready passage count",
             "%1 · 1 passage", "%1 · 2 passages"),
        )
        for context, source, comment, singular, plural in cases:
            self.assertEqual(singular, QCoreApplication.translate(context, source, comment, 1))
            self.assertEqual(plural, QCoreApplication.translate(context, source, comment, 2))


class LayoutTests(QmlHarness):
    HEADER = SurfaceTranslationTests.HEADER

    def resize(self, width, height):
        self.window.setProperty("width", width)
        self.window.setProperty("height", height)
        application().processEvents()

    def test_minimum_window_size_keeps_english_actions_readable(self):
        original = (self.window.property("width"), self.window.property("height"))
        self.resize(self.window.property("minimumWidth"), self.window.property("minimumHeight"))
        self.english()
        for name in self.HEADER:
            item = find(self.window, name)
            if not item.property("visible"):
                continue
            self.assertGreater(item.property("implicitWidth"), 30, name)
            self.assertFalse(HAS_HAN.search(item.property("text")), name)
        self.assertEqual([], self.qml_errors())
        self.resize(*original)

    def test_english_text_is_not_shrunk_globally(self):
        """English must not be accommodated by making every font smaller."""
        self.english()
        sizes = []
        for item in self.window.findChildren(QObject):
            if item.metaObject().indexOfProperty("font") < 0:
                continue
            font = item.property("font")
            if font is not None and hasattr(font, "pixelSize"):
                sizes.append(font.pixelSize())
        # -1 means "use the application default"; only explicit sizes matter.
        explicit = [size for size in sizes if size > 0]
        self.assertGreater(len(explicit), 50)
        self.assertGreaterEqual(min(explicit), 10, "英文界面不得靠全局缩小字号实现")
        self.assertLessEqual(max(explicit), 30)

    def test_reader_actions_exist_at_every_supported_width(self):
        pane = self.load_pane(reading=self.reader())
        self.english()
        for width in (600, 1000, 1500):
            pane.setProperty("width", width)
            pane.setProperty("height", 800)
            application().processEvents()
            for name in ("addParallelColumn", "closeParallelColumn", "readerMenu"):
                self.assertIsNotNone(pane.findChild(QObject, name), (name, width))
        self.assertEqual([], self.qml_errors())


class BoundaryTests(unittest.TestCase):
    def test_user_content_and_protocol_values_are_not_interface_text(self):
        for value in ("Mara", "readerPane_0", "post-edit-v5", "name-plan-v2", "human-review-v4",
                      "zh-CN", "epub-v1", "bookshelfList"):
            self.assertFalse(HAS_HAN.search(value), value)

    def test_catalogue_keeps_protocol_and_object_names_ascii(self):
        for _, message in messages(parse_catalogue()):
            source = message.findtext("source") or ""
            for token in ("post-edit", "name-plan", "human-review", "readerPane"):
                self.assertNotIn(token, source, source)


if __name__ == "__main__":
    unittest.main()
