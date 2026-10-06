"""Task 01: research failure handling is a state, not a localized message.

The controller used to decide whether to show the raw worker detail by
comparing the visible Chinese placeholder text.  These tests pin the
replacement: a boolean state, so rewording or translating the placeholder
cannot change the behaviour.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime/lib"))


class FakeProcess:
    def __init__(self, stderr: bytes = b"", stdout: bytes = b""):
        self._stderr, self._stdout, self.deleted = stderr, stdout, False
        self.terminated = False

    def readAllStandardOutput(self):
        data, self._stdout = self._stdout, b""
        return data

    def readAllStandardError(self):
        return self._stderr

    def terminate(self):
        self.terminated = True

    def deleteLater(self):
        self.deleted = True


class ResearchStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        # Keep the Python owner alive throughout the suite. Reconstructing a
        # destroyed QApplication on macOS can abort between test methods.
        cls.app = QApplication.instance() or QApplication([])

    def controller(self, root: Path):
        from PySide6.QtCore import QObject, QSettings, Signal
        from PySide6.QtWidgets import QApplication
        from research_controller import ResearchController
        QApplication.instance() or QApplication([])

        class Owner(QObject):
            changed = Signal()

            def __init__(self):
                super().__init__()
                self.workspace = root / "workspace"
                self.settings = QSettings(str(root / "settings.ini"), QSettings.Format.IniFormat)

        return ResearchController(Owner(), restore=False)

    def fail_with(self, controller, code=1, action="analyze"):
        process = FakeProcess(stderr=b"worker exploded")
        controller.process = process
        controller._action = action
        controller._done(code)
        return process

    def test_generic_placeholder_is_replaced_by_the_raw_failure_detail(self):
        with tempfile.TemporaryDirectory() as raw:
            controller = self.controller(Path(raw))
            controller.message = "正在处理本页…"
            controller._generic_status = True
            process = self.fail_with(controller)
            self.assertEqual("处理失败", controller.message_text())
            self.assertEqual("worker exploded", controller.message_detail())
            self.assertTrue(process.deleted)
            self.assertIsNone(controller.process)
            self.assertFalse(controller._generic_status)

    def test_translated_placeholder_is_still_replaced(self):
        with tempfile.TemporaryDirectory() as raw:
            controller = self.controller(Path(raw))
            controller.message = "Working…"  # a future English placeholder
            controller._generic_status = True
            self.fail_with(controller)
            self.assertEqual("处理失败", controller.message_text())
            self.assertEqual("worker exploded", controller.message_detail())

    def test_worker_reported_status_is_never_overwritten_by_the_detail(self):
        with tempfile.TemporaryDirectory() as raw:
            controller = self.controller(Path(raw))
            controller.message = "已识别出含行内公式的正文。"
            controller._generic_status = False
            self.fail_with(controller)
            self.assertEqual("已识别出含行内公式的正文。", controller.message_text())

    def test_worker_status_line_clears_the_placeholder_state(self):
        with tempfile.TemporaryDirectory() as raw:
            controller = self.controller(Path(raw))
            controller.message = "正在处理本页…"
            controller._generic_status = True
            process = FakeProcess(stdout='{"status": "正在解析第 3 页"}\n'.encode())
            controller.process = process
            controller._read()
            self.assertEqual("正在解析第 3 页", controller.message_text())
            self.assertFalse(controller._generic_status)
            controller._action = "analyze"
            controller._done(1)
            self.assertEqual("正在解析第 3 页", controller.message_text())

    def test_structured_worker_status_rerenders_and_keeps_raw_detail(self):
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        from locale_service import LocaleService

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            app = QApplication.instance() or QApplication([])
            service = LocaleService(
                QSettings(str(root / "locale.ini"), QSettings.Format.IniFormat),
                system_locale="zh_CN")
            service.start(app)
            service.set_locale("zh-CN")
            self.addCleanup(service.shutdown)
            controller = self.controller(root)
            controller.process = FakeProcess(stdout=(json.dumps({
                "status": "legacy line remains available",
                "message_code": "researchRetryingBlock",
                "message_args": [],
                "detail": "provider timeout after 180 seconds",
            }) + "\n").encode())
            controller._read()
            controller.process = None
            self.assertEqual("researchRetryingBlock", controller.state["statusCode"])
            self.assertEqual("本段校验未通过，正在重试…", controller.message_text())
            self.assertEqual("provider timeout after 180 seconds",
                             controller.message_detail())
            self.assertTrue(service.set_locale("en"))
            self.assertEqual("This passage did not pass validation. Retrying…",
                             controller.message_text())
            self.assertEqual("provider timeout after 180 seconds",
                             controller.message_detail())

    def test_pause_notice_survives_the_terminated_process(self):
        with tempfile.TemporaryDirectory() as raw:
            controller = self.controller(Path(raw))
            controller.message = "正在处理本页…"
            controller._generic_status = True
            process = FakeProcess()
            controller.process = process
            controller.stop()
            self.assertEqual("已请求暂停，已完成段落保留。", controller.message_text())
            self.assertFalse(controller._generic_status)
            controller._done(1)
            self.assertEqual("已请求暂停，已完成段落保留。", controller.message_text())

    def test_restore_failure_keeps_its_recovery_message(self):
        with tempfile.TemporaryDirectory() as raw:
            controller = self.controller(Path(raw))
            controller.job = Path(raw) / "job"
            controller._generic_status = False
            controller.message = "正在后台恢复上次科研文档…"
            self.fail_with(controller, action="restore")
            self.assertEqual("上次科研文档无法恢复，请重新导入原 PDF。", controller.message_text())

    def test_unparsable_worker_output_keeps_the_placeholder_state(self):
        with tempfile.TemporaryDirectory() as raw:
            controller = self.controller(Path(raw))
            controller.process = FakeProcess(stdout=b"not json\n{\"status\": 5}\n")
            controller.message = "正在处理本页…"
            controller._generic_status = True
            controller._read()
            self.assertEqual("正在处理本页…", controller.message_text())
            self.assertTrue(controller._generic_status)

    def test_pdf_selection_is_authorized_before_import(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            controller = self.controller(root)
            selected = root / "external.pdf"
            authorized = root / "authorized.pdf"
            events = []
            controller.owner.file_access = Mock()
            controller.owner.file_access.selected.side_effect = (
                lambda path, **kwargs: events.append(("grant", path, kwargs)) or authorized)
            controller.openPath = Mock(side_effect=lambda path: events.append(("import", path)))
            with patch("research_controller.choose_open_file",
                       return_value=(str(selected), "")), \
                 patch("PySide6.QtWidgets.QFileDialog.getOpenFileName",
                       side_effect=AssertionError("Use the sandbox-aware selection boundary")):
                controller.pick()
            self.assertEqual(events, [("grant", str(selected), {"read_only": True}),
                                      ("import", str(authorized))])

    def test_cancelled_pdf_selection_does_not_grant_or_import(self):
        with tempfile.TemporaryDirectory() as raw:
            controller = self.controller(Path(raw))
            controller.owner.file_access = Mock()
            controller.openPath = Mock()
            with patch("research_controller.choose_open_file", return_value=("", "")), \
                 patch("PySide6.QtWidgets.QFileDialog.getOpenFileName",
                       side_effect=AssertionError("Use the sandbox-aware selection boundary")):
                controller.pick()
            controller.owner.file_access.selected.assert_not_called()
            controller.openPath.assert_not_called()

    def test_refused_pdf_authorization_blocks_import_and_reports_retryable_failure(self):
        from file_access import FileAccessError
        with tempfile.TemporaryDirectory() as raw:
            controller = self.controller(Path(raw))
            controller.owner.file_access = Mock()
            controller.owner.file_access.selected.side_effect = FileAccessError(
                "File authorization creation failed (OS code 256)")
            controller.openPath = Mock()
            with patch("research_controller.choose_open_file",
                       return_value=(str(Path(raw) / "external.pdf"), "")), \
                 patch("PySide6.QtWidgets.QFileDialog.getOpenFileName",
                       side_effect=AssertionError("Use the sandbox-aware selection boundary")):
                controller.pick()
            controller.openPath.assert_not_called()
            self.assertIsNone(controller.job)
            self.assertIn("OS code 256", controller.message_detail())


if __name__ == "__main__":
    unittest.main()
