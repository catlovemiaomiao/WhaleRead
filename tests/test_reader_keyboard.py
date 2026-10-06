"""Isolated QML-level tests for immersive keyboard paging (TXT/Markdown).

Loads the real Main.qml and ReaderPane.qml with a temporary task directory and
temporary settings, so it never touches a real book, job, setting, cache or the
translation API.  Qt is imported lazily inside setUpClass: importing Qt Quick
while pytest collects every module changes global Qt state for unrelated
threaded tests in this suite.  EPUB's native WKWebView cannot exist in this
harness; the matching native-window evidence lives in
scripts/qa_reader_keyboard.py and the page-script contract in
tests/test_reader_keys.mjs.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "runtime/lib")]

PARAGRAPH = "北港的钟声越过水面，抵达旧灯塔时，只剩下低而温柔的回响。玛拉把信放进口袋，沿着潮湿的石阶向上走。"
CHAPTERS = ["潮汐来信", "旧塔灯火", "白鸟归航", "未寄出的信", "海风记得"]


def find_all(scope, name):
    from PySide6.QtQuick import QQuickItem  # noqa: F401  (lazy: see module docstring)
    found, pending = [], [scope]
    while pending:
        item = pending.pop()
        if item.objectName() == name:
            found.append(item)
        pending.extend(item.childItems())
        for child in item.children():
            if child.metaObject().indexOfProperty("popupType") >= 0:
                content = child.property("contentItem")
                if isinstance(content, QQuickItem):
                    pending.append(content)
    return found


def find_in(scope, name):
    from PySide6.QtQuick import QQuickItem  # noqa: F401  (lazy: see module docstring)
    value = scope if scope.objectName() == name else scope.findChild(QQuickItem, name)
    if value:
        return value
    pending = [scope]
    while pending:
        item = pending.pop()
        if item.objectName() == name:
            return item
        pending.extend(item.childItems())
        for child in item.children():
            if child.metaObject().indexOfProperty("popupType") >= 0:
                content = child.property("contentItem")
                if isinstance(content, QQuickItem):
                    pending.append(content)
    return None


class ImmersiveReadingKeys(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        global Q_ARG, QMetaObject, QObject, QSettings, Qt, QUrl, QApplication, QTest
        global QQmlApplicationEngine, TranslatorController
        os.environ.setdefault("QT_QUICK_CONTROLS_STYLE", "Basic")
        from PySide6.QtCore import Q_ARG, QMetaObject, QObject, QSettings, Qt, QUrl
        from PySide6.QtQml import QQmlApplicationEngine
        from PySide6.QtQuick import QQuickItem, QQuickWindow  # noqa: F401  (typed QML wrappers)
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from native_launcher import TranslatorController
        cls.app = QApplication.instance() or QApplication([])
        cls.tmp = tempfile.TemporaryDirectory(prefix="whale-reader-keys-")
        root = Path(cls.tmp.name)
        cls.root = root
        settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)
        cls.controller = TranslatorController(workspace=root / "jobs", settings=settings, restore=False)
        source = root / "长夜航线 · 原创测试.txt"
        source.write_text(PARAGRAPH * 4, encoding="utf-8")
        cls.controller._select_source(source)
        rows = []
        for number, chapter in enumerate(CHAPTERS, 1):
            rows.append(f"第{number}章 {chapter}")
            rows.extend([PARAGRAPH] * 80)
        preview = root / "preview.txt"
        preview.write_text("\n\n".join(rows), encoding="utf-8")
        cls.controller._output_path = str(preview)
        cls.controller._refresh_reader()
        cls.engine = QQmlApplicationEngine()
        cls.engine.rootContext().setContextProperty("backend", cls.controller)
        cls.engine.load(QUrl.fromLocalFile(str(ROOT / "ui/Main.qml")))
        if not cls.engine.rootObjects():
            raise AssertionError("Main.qml did not load")
        cls.window = cls.engine.rootObjects()[0]
        QTest.qWait(300)

    @classmethod
    def tearDownClass(cls):
        cls.window.close()
        cls.controller.reader_timer.stop()
        cls.controller.shutdown()
        cls.engine.deleteLater()
        cls.controller.deleteLater()
        QTest.qWait(120)
        del cls.engine
        del cls.controller
        cls.tmp.cleanup()

    def activate_window(self, attempts=25):
        """macOS can drop key status between tests; re-assert it before
        asserting keyboard behaviour (the app needs an active window to focus)."""
        for _ in range(attempts):
            if self.window.isActive():
                return True
            self.poke_application_active()
            self.window.requestActivate()
            QTest.qWait(100)
        return self.window.isActive()

    @staticmethod
    def poke_application_active():
        # QWindow.requestActivate() alone is not always enough once the test
        # process has been in the background for a while; ask AppKit directly.
        if sys.platform != "darwin":
            return
        try:
            import ctypes
            import ctypes.util
            objc = ctypes.CDLL(ctypes.util.find_library("objc"))
            objc.sel_registerName.restype = ctypes.c_void_p
            objc.sel_registerName.argtypes = [ctypes.c_char_p]
            objc.objc_getClass.restype = ctypes.c_void_p
            objc.objc_getClass.argtypes = [ctypes.c_char_p]
            msg = objc.objc_msgSend
            msg.restype = ctypes.c_void_p
            msg.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool]
            app = msg(objc.objc_getClass(b"NSApplication"), objc.sel_registerName(b"sharedApplication"), ctypes.c_bool(False))
            msg(app, objc.sel_registerName(b"activateIgnoringOtherApps:"), ctypes.c_bool(True))
        except Exception:
            pass

    def setUp(self):
        self.pane = find_in(self.window.contentItem(), "readerPane_0")
        assert self.pane, "reader pane missing"
        self.column_id = self.pane.property("reading").property("columnId")
        self.book = find_in(self.pane, "bookList")
        QMetaObject.invokeMethod(self.pane, "jump", Q_ARG("QVariant", 60))
        QTest.qWait(120)
        QMetaObject.invokeMethod(self.window, "setImmersive", Q_ARG("QVariant", True), Q_ARG("QVariant", ""))
        self.window.requestActivate()
        QTest.qWait(200)
        self.activate_window()

    def tearDown(self):
        QMetaObject.invokeMethod(self.window, "setImmersive", Q_ARG("QVariant", False), Q_ARG("QVariant", ""))
        QTest.qWait(80)

    def click(self, owner, name):
        target = find_in(owner, name)
        assert target and target.isEnabled(), name
        assert QMetaObject.invokeMethod(target, "click"), name
        QTest.qWait(120)

    def page_step(self):
        return max(80.0, self.book.height() * 0.85)

    def line_step(self):
        return max(48.0, self.book.height() * 0.12)

    def key(self, code, modifiers=None):
        QTest.keyClick(self.window, code, modifiers if modifiers is not None else Qt.KeyboardModifier.NoModifier)
        QTest.qWait(80)

    def test_immersive_button_moves_focus_into_body_then_pages(self):
        QMetaObject.invokeMethod(self.window, "setImmersive", Q_ARG("QVariant", False), Q_ARG("QVariant", ""))
        QTest.qWait(100)
        self.click(self.pane, "readerFocus")
        self.assertTrue(self.window.property("readingOnly"))
        self.assertTrue(self.book.hasActiveFocus(), "immersion did not hand focus to the reading surface")
        before = self.book.property("contentY")
        self.key(Qt.Key.Key_Space)
        self.assertAlmostEqual(self.book.property("contentY") - before, self.page_step(), delta=2)
        self.key(Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier)
        self.assertAlmostEqual(self.book.property("contentY"), before, delta=2)

    def test_all_reading_keys_and_bounds(self):
        self.assertTrue(self.book.hasActiveFocus(), "reading surface should own the keyboard in immersion")
        before = self.book.property("contentY")
        self.key(Qt.Key.Key_Down)
        self.assertAlmostEqual(self.book.property("contentY") - before, self.line_step(), delta=2)
        self.key(Qt.Key.Key_Up)
        self.assertAlmostEqual(self.book.property("contentY"), before, delta=2)
        self.key(Qt.Key.Key_PageDown)
        self.assertAlmostEqual(self.book.property("contentY") - before, self.page_step(), delta=2)
        self.key(Qt.Key.Key_PageUp)
        self.assertAlmostEqual(self.book.property("contentY"), before, delta=2)
        # Top boundary: back-paging at the first paragraph stays put.
        QMetaObject.invokeMethod(self.pane, "jump", Q_ARG("QVariant", 0))
        QTest.qWait(250)
        top = self.book.property("contentY")
        self.key(Qt.Key.Key_Up)
        self.key(Qt.Key.Key_PageUp)
        self.key(Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.book.property("contentY"), top)
        # Bottom boundary: forward paging on the last paragraph settles on the
        # final screen and stops instead of running past the content.
        QMetaObject.invokeMethod(self.pane, "jump", Q_ARG("QVariant", self.controller.reader.count - 1))
        QTest.qWait(300)
        for _ in range(6):
            self.key(Qt.Key.Key_Space)
        settled = self.book.property("contentY")
        maximum = self.book.property("originY") + self.book.property("contentHeight") - self.book.height()
        self.assertLessEqual(settled, maximum + 2)
        self.key(Qt.Key.Key_Space)
        self.key(Qt.Key.Key_Down)
        self.assertAlmostEqual(self.book.property("contentY"), settled, delta=2)

    def test_progress_is_saved_after_keyboard_paging(self):
        self.key(Qt.Key.Key_PageDown)
        self.key(Qt.Key.Key_PageDown)
        QTest.qWait(800)
        self.assertEqual(self.controller.savedReadingPosition, self.pane.property("visibleRow"))
        self.assertGreater(self.controller.savedReadingPosition, 60)

    def test_search_field_keeps_its_keys(self):
        if not self.activate_window():
            self.skipTest("the window could not take key status in this environment")
        search = find_in(self.pane, "readerSearch")
        search.forceActiveFocus()
        QTest.qWait(80)
        self.assertFalse(self.book.hasActiveFocus())
        before = self.book.property("contentY")
        self.key(Qt.Key.Key_Space)
        self.assertEqual(search.property("text"), " ")
        self.assertEqual(self.book.property("contentY"), before)
        self.key(Qt.Key.Key_PageDown)
        self.key(Qt.Key.Key_Down)
        self.assertEqual(self.book.property("contentY"), before)

    def test_chapter_popup_swallows_reading_keys_and_returns_focus(self):
        before = self.book.property("contentY")
        self.click(self.pane, "readerToc")
        popup = self.pane.findChild(QObject, "chaptersPopup")
        self.assertTrue(popup.property("opened"))
        self.key(Qt.Key.Key_Space)
        self.assertEqual(self.book.property("contentY"), before)
        QMetaObject.invokeMethod(popup, "close")
        QTest.qWait(200)
        self.assertTrue(self.book.hasActiveFocus(), "closing the chapter menu should restore reading keys")
        self.key(Qt.Key.Key_Space)
        self.assertGreater(self.book.property("contentY"), before)

    def test_note_editor_keeps_its_keys(self):
        if not self.activate_window():
            self.skipTest("the window could not take key status in this environment")
        # Row 0 must be materialised before its margin-note marker can be used.
        QMetaObject.invokeMethod(self.pane, "jump", Q_ARG("QVariant", 0))
        QTest.qWait(250)
        self.click(self.pane, "paragraphNoteMarker_0")
        margin_note = find_in(self.pane, "readingMarginNote")
        self.assertTrue(margin_note.property("opened"))
        editor = find_in(self.pane, "marginNoteText")
        editor.forceActiveFocus()
        QTest.qWait(80)
        before = self.book.property("contentY")
        for code in (Qt.Key.Key_A, Qt.Key.Key_B, Qt.Key.Key_Space, Qt.Key.Key_C, Qt.Key.Key_D):
            QTest.keyClick(self.window, code)
        QTest.qWait(80)
        self.assertIn("ab cd", editor.property("text"))
        self.assertEqual(self.book.property("contentY"), before)
        self.click(self.pane, "closeMarginNote")
        QTest.qWait(200)
        self.assertTrue(self.book.hasActiveFocus())

    def list_for_column(self, column_id):
        """Repeater delegates are rebuilt when columns change, so re-resolve."""
        for item in find_all(self.window.contentItem(), "bookList"):
            parent = item.parentItem()
            while parent is not None and not parent.objectName().startswith("readerPane"):
                parent = parent.parentItem()
            if parent is not None and parent.property("reading").property("columnId") == column_id:
                return item, parent
        return None, None

    def test_hidden_parallel_column_never_moves(self):
        if len(self.controller.readingColumns) < 2:
            self.controller.addReadingColumn()
            QTest.qWait(250)
        other = next(c for c in self.controller.readingColumns if c.columnId != self.column_id)
        second_book = self.root / "第二栏 · 原创测试.txt"
        second_book.write_text("\n\n".join(f"第二栏第 {i} 段。" + PARAGRAPH for i in range(300)), encoding="utf-8")
        self.controller.openPathInColumn(other.columnId, str(second_book))
        QTest.qWait(400)
        self.controller.activateReadingColumn(self.column_id)
        QMetaObject.invokeMethod(self.window, "setImmersive", Q_ARG("QVariant", False), Q_ARG("QVariant", ""))
        QTest.qWait(150)
        QMetaObject.invokeMethod(self.window, "setImmersive", Q_ARG("QVariant", True), Q_ARG("QVariant", ""))
        QTest.qWait(250)
        visible_list, visible_pane = self.list_for_column(self.column_id)
        hidden_list, _ = self.list_for_column(other.columnId)
        self.assertIsNotNone(visible_list, "active reading surface missing")
        self.assertIsNotNone(hidden_list, "second column reader missing")
        self.assertTrue(visible_list.isVisible())
        self.assertFalse(hidden_list.isVisible(), "single-column immersion must hide the other column")
        hidden_before = hidden_list.property("contentY")
        self.assertTrue(visible_list.hasActiveFocus())
        for _ in range(3):
            self.key(Qt.Key.Key_PageDown)
        self.assertGreater(visible_list.property("contentY"), 0)
        self.assertEqual(hidden_list.property("contentY"), hidden_before,
                         "keys in single-column immersion moved the hidden column")

    def test_escape_leaves_immersion(self):
        self.assertTrue(self.window.property("readingOnly"))
        self.key(Qt.Key.Key_Escape)
        self.assertFalse(self.window.property("readingOnly"))

    def test_reading_keys_still_work_outside_immersion(self):
        QMetaObject.invokeMethod(self.window, "setImmersive", Q_ARG("QVariant", False), Q_ARG("QVariant", ""))
        QTest.qWait(120)
        self.book.forceActiveFocus()
        QTest.qWait(80)
        before = self.book.property("contentY")
        self.key(Qt.Key.Key_PageDown)
        self.assertGreater(self.book.property("contentY"), before)


class BridgeScriptContract(unittest.TestCase):
    def test_node_contract_suite(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available")
        result = subprocess.run([node, str(ROOT / "tests/test_reader_keys.mjs")],
                                capture_output=True, text=True, cwd=str(ROOT))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
