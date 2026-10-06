"""Task 04: dynamic Python/worker messages and cache projections.

Every test uses temporary QSettings, a temporary workspace, synthetic TXT/EPUB
input, fake workers and a fake credential store.  No network, model or
translation API is called, and no real book, task, cache or credential file is
touched.

The assertions read real controller properties and real event projections, not
just helper return values.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "runtime/lib"), str(ROOT / "runtime/tools")):
    sys.path.insert(0, entry)

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import ui_messages as UM  # noqa: E402

SOURCE = ("Mara returned to North Harbor before dawn. She carried the sealed letter in her coat "
          "and waited quietly by the old lighthouse.\n\n") * 4


def application():
    return QApplication.instance() or QApplication([])


def settings_at(root):
    settings = QSettings(str(Path(root) / "settings.ini"), QSettings.Format.IniFormat)
    # These projection tests deliberately compare Chinese with English.  Keep
    # their starting locale explicit now that the public preview starts clean
    # installations in English.
    settings.setValue("interface/locale", "zh-CN")
    settings.sync()
    return settings


def harness(root, *, locale_system="zh_CN"):
    """A controller on temporary settings, workspace and storage."""
    from locale_service import LocaleService
    from native_launcher import TranslatorController
    application()
    settings = settings_at(root)
    service = LocaleService(settings, system_locale=locale_system)
    service.start(application())
    controller = TranslatorController(workspace=Path(root) / "jobs", settings=settings,
                                      locale_service=service, restore=False)
    return service, controller


def synthetic_epub(path: Path, chapters=("第一章", "第二章"), *, headings=True,
                   mapped_counts=None, pending_counts=None) -> Path:
    """A minimal but valid EPUB with publisher chapter titles."""
    container = ('<?xml version="1.0"?><container version="1.0" '
                 'xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                 '<rootfiles><rootfile full-path="OEBPS/content.opf" '
                 'media-type="application/oebps-package+xml"/></rootfiles></container>')
    items, refs, docs = [], [], []
    for index, _ in enumerate(chapters, 1):
        items.append(f'<item id="c{index}" href="c{index}.xhtml" media-type="application/xhtml+xml"/>')
        refs.append(f'<itemref idref="c{index}"/>')
        heading = f'<h1>{chapters[index - 1]}</h1>' if headings else ''
        head = f'<title>{chapters[index - 1]}</title>' if headings else '<title></title>'
        mapped = ((mapped_counts or [1] * len(chapters))[index - 1])
        pending = ((pending_counts or [0] * len(chapters))[index - 1])
        paragraphs = ''.join(
            f'<p data-whale-source-text="Paragraph {index}.{number}"'
            f'{" data-whale-pending=\"true\"" if number <= pending else ""}>'
            f'Paragraph {index}.{number}</p>'
            for number in range(1, mapped + 1)
        ) or f'<p>Paragraph {index}.</p>'
        docs.append((f"OEBPS/c{index}.xhtml",
                     '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><head>'
                     f'{head}</head><body>'
                     f'{heading}{paragraphs}</body></html>'))
    opf = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
           '<dc:title>Synthetic Book</dc:title><dc:creator>Tester</dc:creator></metadata>'
           f'<manifest>{"".join(items)}</manifest><spine>{"".join(refs)}</spine></package>')
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        for name, body in docs:
            archive.writestr(name, body)
    return path


class MessageLayerTests(unittest.TestCase):
    """The message layer itself: codes, parameters, legacy adapters."""

    def setUp(self):
        application()

    def test_render_uses_parameters_not_concatenation(self):
        message = UM.Message(UM.MessageCode.CHUNK_SAVED, (3, 7))
        self.assertIn("3", UM.render(message))
        self.assertIn("7", UM.render(message))
        self.assertNotIn("%1", UM.render(message))

    def test_nested_application_label_rerenders_and_parameter_text_is_not_reparsed(self):
        nested = UM.Message(UM.MessageCode.MODEL_PROFILE_LOCAL_1_8_SHORT)
        message = UM.Message(UM.MessageCode.STARTING_MODEL, (nested,))
        self.assertIn("本机", UM.render(message))
        # A placeholder-looking token supplied by a worker/user is literal.
        literal = UM.Message(UM.MessageCode.CHUNK_SAVED, ("%2", 7))
        self.assertIn("%2", UM.render(literal))

    def test_all_codes_have_a_static_source_literal(self):
        codes = [value for name, value in vars(UM.MessageCode).items()
                 if not name.startswith("_") and isinstance(value, str)]
        missing = [code for code in codes if not UM.source_for(code)]
        self.assertEqual([], missing)

    def test_unknown_code_falls_back_to_a_summary_and_keeps_detail(self):
        message = UM.Message("someFutureCode", (), "raw diagnostic text")
        self.assertEqual("操作未完成：raw diagnostic text", UM.summary_with_detail(message))
        self.assertEqual("raw diagnostic text", UM.detail_text(message))

    def test_empty_message_is_not_misreported_as_an_unknown_error(self):
        message = UM.Message("")
        self.assertEqual("", UM.render(message))
        self.assertEqual("", UM.summary_with_detail(message))
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            self.assertEqual("", controller.errorCode)
            self.assertEqual("", controller.errorDetail)
            self.assertEqual("", controller.errorMessage)

    def test_legacy_adapter_maps_only_exact_or_known_sentences(self):
        self.assertEqual(UM.MessageCode.READY, UM.legacy_code("准备就绪"))
        self.assertEqual(UM.MessageCode.CACHE_NOT_COUNTED, UM.legacy_code("尚未统计"))
        self.assertEqual("", UM.legacy_code("这是一条全新的错误"))
        # Common words in user/model diagnostics are not an error taxonomy.
        self.assertEqual("", UM.legacy_code("模型建议调整这个段落的语气"))
        self.assertEqual("", UM.legacy_code("这份说明讨论了安全边界"))
        legacy = UM.legacy_message("这是一条全新的错误")
        self.assertEqual("", legacy.code)
        self.assertEqual("这是一条全新的错误", legacy.detail)

    def test_same_code_keeps_its_category_in_both_locales(self):
        from locale_service import LocaleService
        with tempfile.TemporaryDirectory() as raw:
            settings = settings_at(raw)
            service = LocaleService(settings, system_locale="zh_CN")
            service.start(application())
            message = UM.Message(UM.MessageCode.VALIDATION_FAILED)
            chinese = UM.render(message)
            service.set_locale("en")
            english = UM.render(message)
            self.assertNotEqual(chinese, english)
            self.assertEqual(message.code, UM.MessageCode.VALIDATION_FAILED)
            self.assertEqual("", UM.detail_text(message))


class WorkerEventCompatibilityTests(unittest.TestCase):
    """New structured events and old ``message``/``error`` events both work."""

    def setUp(self):
        application()

    def test_worker_failure_codes_cover_the_required_categories(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "translate_range_task04", ROOT / "runtime/tools/translate_range.py")
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        cases = (
            (FileNotFoundError("missing"), "fileMissing"),
            (TimeoutError("timed out"), "requestTimeout"),
            (urllib.error.HTTPError("u", 503, "boom", {}, None), "httpFailed"),
            (urllib.error.URLError(socket.timeout()), "requestTimeout"),
            (urllib.error.URLError("connection refused"), "httpFailed"),
            (worker.TranslationContractError("Hy 返回内容未达到目标语言门禁"), "validationFailed"),
            (ValueError("OpenCode 认证存储中缺少 dgx-spark-translate API 密钥"), "credentialsMissing"),
            (ValueError("校对需要 Qwen 凭据，请配置 dgx-spark。"), "credentialsMissing"),
            (ValueError("原文内容已变化。为避免错位，未继续使用旧断点"), "sourceChanged"),
            (ValueError("翻译模型或服务已改变，请新建译本"), "checkpointIncompatible"),
            (ValueError("输出目录必须位于当前任务目录内"), "safetyPaused"),
            (RuntimeError("Hy 返回中没有 choices"), "modelFailed"),
            (OSError("disk I/O failed"), "unknownError"),
        )
        for exc, expected in cases:
            self.assertEqual(expected, worker.failure_code(exc), type(exc).__name__)

    def test_failure_payload_keeps_code_and_raw_error(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "translate_range_task04b", ROOT / "runtime/tools/translate_range.py")
        worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(worker)
        payload = worker.failure_payload(TimeoutError("took too long"))
        self.assertEqual("requestTimeout", payload["code"])
        self.assertEqual("took too long", payload["error"])

    def worker_events(self, events):
        """Feed events through the controller's real handler."""
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            for event in events:
                controller._handle_event(event)
            return controller

    def test_structured_error_event_renders_in_both_locales(self):
        controller = self.worker_events([
            {"type": "error", "message_code": "credentialsMissing", "message_args": [],
             "detail": "OpenCode 认证存储中缺少 dgx-spark-translate API 密钥",
             "error": "OpenCode 认证存储中缺少 dgx-spark-translate API 密钥"}])
        self.assertEqual("credentialsMissing", controller.errorCode)
        self.assertIn("凭据", controller.errorMessage)
        self.assertIn("OpenCode 认证存储中缺少", controller.errorDetail)
        self.assertTrue(controller.setUiLocale("en"))
        self.assertIn("Credentials", controller.errorMessage)
        # The raw diagnostic stays byte-identical across the switch.
        self.assertIn("OpenCode 认证存储中缺少", controller.errorDetail)
        self.assertEqual("credentialsMissing", controller.errorCode)

    def test_legacy_error_event_without_code_is_classified_conservatively(self):
        controller = self.worker_events([
            {"type": "error", "error": "Hy 返回内容未达到目标语言门禁，当前块未保存"}])
        self.assertEqual("validationFailed", controller.errorCode)
        controller.setUiLocale("en")
        self.assertIn("did not pass", controller.errorMessage)
        self.assertIn("语言门禁", controller.errorDetail)

    def test_unrecognized_legacy_error_is_not_guessed_into_a_category(self):
        controller = self.worker_events([
            {"type": "error", "error": "翻译范围必须位于 1～10 段，当前为 20～30"}])
        self.assertEqual("", controller.errorCode)
        self.assertIn("翻译范围必须位于", controller.errorDetail)

    def test_unknown_legacy_error_shows_summary_and_untouched_detail(self):
        detail = "Some brand new worker failure the desktop has never seen"
        controller = self.worker_events([{"type": "error", "error": detail}])
        self.assertEqual("", controller.errorCode)
        self.assertEqual(detail, controller.errorDetail)
        self.assertEqual("操作未完成", controller.errorMessage)
        controller.setUiLocale("en")
        self.assertEqual("The operation did not finish", controller.errorMessage)
        self.assertEqual(detail, controller.errorDetail)

    def test_old_worker_status_fields_still_drive_the_interface(self):
        controller = self.worker_events([
            {"type": "working", "range": [4, 9], "phase": "split"},
            {"type": "progress", "range": [1, 3], "percent": 25.0},
            {"type": "result", "status": "completed", "failures": []},
        ])
        self.assertEqual(1.0, controller.progress)
        self.assertTrue(controller._finished)
        self.assertEqual("翻译完成", controller.statusTitle)
        self.assertIn("完整性检查", controller.statusDetail)

    def test_glossary_events_preserve_limit_and_pending_counts(self):
        controller = self.worker_events([
            {"type": "glossary_indexed", "strategy": "candidates",
             "selected_candidates": 24, "omitted_by_limit": 6},
        ])
        self.assertEqual("正在整理全书译名", controller.statusTitle)
        self.assertNotIn("%1", controller.statusTitle)
        self.assertIn("24", controller.glossarySummary)
        self.assertIn("6", controller.glossarySummary)
        controller._handle_event({"type": "glossary_ready", "entries": 18,
                                  "pending_review": 3})
        self.assertIn("18", controller.statusTitle)
        self.assertIn("3", controller.glossarySummary)
        controller.setUiLocale("en")
        self.assertIn("18", controller.statusTitle)
        self.assertIn("3", controller.glossarySummary)
        self.assertNotIn("%1", controller.statusTitle)

    def test_result_failure_entry_uses_its_code(self):
        controller = self.worker_events([
            {"type": "result", "status": "partial_failure",
             "failures": [{"range": [1, 2], "code": "validationFailed",
                           "error": "Hy 漏掉了第 2 段译文"}]}])
        self.assertEqual("validationFailed", controller.errorCode)
        self.assertIn("Hy 漏掉了第 2 段译文", controller.errorDetail)

    def test_completed_range_does_not_claim_zero_failures(self):
        controller = self.worker_events([
            {"type": "result", "status": "range_completed", "failures": []}])
        self.assertEqual("部分完成", controller.statusTitle)
        self.assertIn("所选范围已完成", controller.statusDetail)
        self.assertNotIn("0 个译块未完成", controller.statusDetail)
        controller.setUiLocale("en")
        self.assertIn("selected range", controller.statusDetail.lower())

    def test_hard_error_says_saved_work_can_resume(self):
        controller = self.worker_events([
            {"type": "error", "message_code": "httpFailed",
             "detail": "HTTP Error 502", "error": "HTTP Error 502"}])
        self.assertIn("断点继续", controller.statusDetail)
        controller.setUiLocale("en")
        self.assertIn("checkpoint", controller.statusDetail.lower())

    def test_legacy_non_json_status_line_remains_visible_verbatim(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            controller._process = _FakeProcess(b"legacy worker diagnostic 17\n")
            controller._read_process()
            self.assertEqual("legacy worker diagnostic 17", controller.statusDetail)
            controller.setUiLocale("en")
            self.assertEqual("legacy worker diagnostic 17", controller.statusDetail)


class LocaleSwitchPreservesWorkTests(unittest.TestCase):
    """Switching locale must not disturb a running translation."""

    def setUp(self):
        application()

    def fake_run(self, root, events):
        """Drive the controller through fake worker events without a process."""
        service, controller = harness(root)
        self.addCleanup(controller.shutdown)
        self.addCleanup(service.shutdown)
        source = Path(root) / "shell.txt"
        source.write_text(SOURCE, encoding="utf-8")
        controller._select_source(source)
        process = _FakeProcess()
        controller._process = process
        controller._running = True
        for event in events:
            controller._handle_event(event)
        return service, controller, process

    def test_switch_while_running_changes_text_only(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller, process = self.fake_run(Path(raw), [
                {"type": "started", "total_source_chars": 400},
                {"type": "progress", "range": [1, 2], "percent": 30.0,
                 "completed_source_chars": 120, "total_source_chars": 400},
            ])
            before = (controller._process, process.identity(), 0,
                      controller.progress, controller._current_completed_chars)
            chinese_title, chinese_detail = controller.statusTitle, controller.statusDetail
            self.assertTrue(controller.setUiLocale("en"))
            after = (controller._process, process.identity(), 0,
                     controller.progress, controller._current_completed_chars)
            self.assertEqual(before, after, "切换语言不得更换进程、计数或译块进度")
            self.assertNotEqual(chinese_title, controller.statusTitle)
            self.assertNotEqual(chinese_detail, controller.statusDetail)
            self.assertFalse(process.terminated)

    def test_switch_while_paused_keeps_the_checkpoint_state(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller, process = self.fake_run(Path(raw), [
                {"type": "started", "total_source_chars": 400},
                {"type": "progress", "range": [1, 2], "percent": 50.0},
            ])
            controller._pausing = True
            controller._process_finished(0, None)
            checkpoint = dict(controller._progress_snapshot() or {}) if hasattr(
                controller, "_progress_snapshot") else {}
            before = (controller._pausing, controller.progress, controller._process)
            self.assertTrue(controller.setUiLocale("en"))
            self.assertEqual(before, (controller._pausing, controller.progress, controller._process))
            self.assertEqual(checkpoint, dict(controller._progress_snapshot() or {})
                             if hasattr(controller, "_progress_snapshot") else {})
            self.assertEqual("Paused", controller.statusTitle)

    def test_switch_while_failing_keeps_the_error_code_and_detail(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller, process = self.fake_run(Path(raw), [
                {"type": "error", "message_code": "httpFailed", "detail": "HTTP Error 502: Bad Gateway",
                 "error": "HTTP Error 502: Bad Gateway"}])
            code, detail = controller.errorCode, controller.errorDetail
            self.assertTrue(controller.setUiLocale("en"))
            self.assertEqual(code, controller.errorCode)
            self.assertEqual(detail, controller.errorDetail)
            self.assertIn("502", controller.errorDetail)

    def test_switch_after_completion_keeps_the_output_path(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller, process = self.fake_run(Path(raw), [
                {"type": "result", "status": "completed", "output_file": str(Path(raw) / "out.txt"),
                 "failures": []}])
            output = controller.outputPath
            self.assertTrue(controller.setUiLocale("en"))
            self.assertEqual(output, controller.outputPath)
            self.assertTrue(controller._finished)

    def test_no_network_or_process_is_created_by_a_locale_switch(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller, process = self.fake_run(Path(raw), [
                {"type": "started", "total_source_chars": 400}])
            spawned = []
            with patch("subprocess.Popen", side_effect=lambda *a, **k: spawned.append(a)), \
                    patch("urllib.request.urlopen", side_effect=AssertionError("network")), \
                    patch("socket.create_connection", side_effect=AssertionError("network")):
                self.assertTrue(controller.setUiLocale("en"))
                self.assertTrue(controller.setUiLocale("zh-CN"))
            self.assertEqual([], spawned)
            self.assertIs(controller._process, process)


class _FakeProcess:
    """A QProcess-shaped stub with a stable identity that records termination."""

    def __init__(self, output=b""):
        from PySide6.QtCore import QProcess
        self.terminated = False
        self._identity = id(self)
        self._state = QProcess.ProcessState.Running
        self._output = bytes(output)

    def identity(self):
        return self._identity

    def state(self):
        return self._state

    def terminate(self):
        self.terminated = True
        from PySide6.QtCore import QProcess
        self._state = QProcess.ProcessState.NotRunning

    def readAllStandardOutput(self):
        output, self._output = self._output, b""
        return output

    def errorString(self):
        return ""

    def waitForFinished(self, _timeout=0):
        return True

    def deleteLater(self):
        pass


class ProjectionRefreshTests(unittest.TestCase):
    """Already-visible Python state refreshes on an in-place locale switch."""

    def setUp(self):
        application()

    def test_status_model_shelf_cache_and_bookmarks_refresh(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            source = Path(raw) / "shell.txt"
            source.write_text(SOURCE, encoding="utf-8")
            controller._select_source(source)
            controller.library.remember(source, enrich=False)

            chinese = {
                "title": controller.statusTitle,
                "detail": controller.statusDetail,
                "model": controller.modelStatus,
                "cache": controller.readerCacheSummary,
                "postedit": controller.postEditor.status,
                "research": controller.research.state["status"],
                "shelf": [row["detail"] for row in controller.bookshelf],
            }
            self.assertTrue(controller.setUiLocale("en"))
            english = {
                "title": controller.statusTitle,
                "detail": controller.statusDetail,
                "model": controller.modelStatus,
                "cache": controller.readerCacheSummary,
                "postedit": controller.postEditor.status,
                "research": controller.research.state["status"],
                "shelf": [row["detail"] for row in controller.bookshelf],
            }
            for key in chinese:
                self.assertNotEqual(chinese[key], english[key], key)
            self.assertTrue(english["shelf"], english)
            self.assertIn("translated", english["shelf"][0])
            self.assertFalse(any("已译" in value for value in english["shelf"]), english["shelf"])

    def test_cache_state_map_exposes_code_args_and_text(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            state = controller.readerCacheState
            self.assertEqual("cacheNotCounted", state["code"])
            self.assertEqual("尚未统计", state["text"])
            controller.setUiLocale("en")
            self.assertEqual("Not counted yet", controller.readerCacheState["text"])
            self.assertEqual("cacheNotCounted", controller.readerCacheState["code"])

    def test_computed_cache_stats_render_in_both_locales(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            # A real (temporary) cache directory with one generation.
            controller._reader_cache_stats = {"bytes": 2048, "physical_bytes": 2048,
                                              "books": 1, "managed": 1, "active": 0, "unknown": 0}
            chinese = controller.readerCacheSummary
            self.assertIn("2.0 KB", chinese)
            self.assertIn("可识别版本", chinese)
            controller.setUiLocale("en")
            english = controller.readerCacheSummary
            self.assertIn("2.0 KB", english)
            self.assertIn("identified versions", english)

    def test_cache_summary_keeps_active_and_unknown_counts_together(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            controller._reader_cache_stats = {"bytes": 4096, "physical_bytes": 4096,
                                              "books": 5, "managed": 5,
                                              "active": 2, "unknown": 3}
            self.assertIn("2 个在用", controller.readerCacheSummary)
            self.assertIn("3 个来源待确认", controller.readerCacheSummary)
            controller.setUiLocale("en")
            self.assertIn("2 in use", controller.readerCacheSummary)
            self.assertIn("3 sources to confirm", controller.readerCacheSummary)

    def test_cache_failure_has_a_specific_localized_summary_and_raw_detail(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            controller._reader_load_futures = {}
            controller._complete_reader_load({
                "purpose": "cache-stats", "ok": False,
                "error": "disk probe failed at inode 42",
            })
            state = controller.readerCacheState
            self.assertEqual("cacheCountFailed", state["code"])
            self.assertEqual("缓存统计失败", state["text"])
            self.assertEqual("disk probe failed at inode 42", state["detail"])
            self.assertEqual("disk probe failed at inode 42", controller.readerCacheDetail)
            controller.setUiLocale("en")
            self.assertEqual("Could not calculate cache usage",
                             controller.readerCacheState["text"])
            self.assertEqual("disk probe failed at inode 42",
                             controller.readerCacheState["detail"])
            self.assertEqual("disk probe failed at inode 42", controller.readerCacheDetail)

    def test_probe_failure_separates_localized_summary_and_raw_detail(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            controller._probe_status = UM.Message(
                UM.MessageCode.PROBE_FAILED, (), "provider returned status 503")
            self.assertEqual("试译失败，请刷新模型状态后重试", controller.probeStatus)
            self.assertEqual("provider returned status 503", controller.probeStatusDetail)
            controller.setUiLocale("en")
            self.assertEqual("The test translation failed. Refresh the model status and try again.",
                             controller.probeStatus)
            self.assertEqual("provider returned status 503", controller.probeStatusDetail)

    def test_default_bookmark_title_is_marked_and_user_title_is_not(self):
        with tempfile.TemporaryDirectory() as raw:
            settings = settings_at(raw)
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            source = Path(raw) / "shell.txt"
            source.write_text(SOURCE, encoding="utf-8")
            controller._select_source(source)
            preview = Path(raw) / "preview.txt"
            preview.write_text("北港的钟声越过水面。\n\n玛拉把信放进口袋。", encoding="utf-8")
            controller._output_path = str(preview)
            controller._refresh_reader()
            column = controller._active_column()
            self.assertIsNotNone(column, "应有活动的阅读栏")
            # The reader loads asynchronously; populate the rows directly so the
            # bookmark path is exercised without waiting on a worker.
            column.reader.rows = ["北港的钟声越过水面。", "玛拉把信放进口袋。"]
            column.reader.heading_rows = [False, False]
            controller.addBookmark(0, "")
            controller.addBookmark(1, "我自己的标题")
            marks = column._bookmark_rows()
            self.assertEqual(2, len(marks), marks)
            generated = [m for m in marks if m.get("generated_title")]
            custom = [m for m in marks if m.get("title") == "我自己的标题"]
            self.assertEqual(1, len(generated))
            self.assertEqual(1, len(custom))
            self.assertFalse(custom[0].get("generated_title"))
            raw_before = json.dumps(marks, ensure_ascii=False, sort_keys=True)
            chinese_titles = [m["title"] for m in column.bookmarks]
            self.assertTrue(any(title.startswith("第 1 段") for title in chinese_titles),
                            chinese_titles)
            # A custom title is user content and is never rewritten. The
            # generated label is projected in English without rewriting QSettings.
            controller.setUiLocale("en")
            english_titles = [m["title"] for m in column.bookmarks]
            self.assertTrue(any(title.startswith("Paragraph 1") for title in english_titles),
                            english_titles)
            self.assertIn("我自己的标题", english_titles)
            self.assertEqual(raw_before,
                             json.dumps(column._bookmark_rows(), ensure_ascii=False,
                                        sort_keys=True))

    def test_dynamic_model_source_probe_glossary_font_and_speed_projections_refresh(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            controller._translation_profile = "local_7b"
            controller._reader_fonts = [{
                "label_code": UM.MessageCode.FONT_SONGTI, "family": "Synthetic Songti"
            }]
            controller._source_info = UM.Message(
                UM.MessageCode.SOURCE_INSPECTED, ("4 KB", "1,234"))
            controller._language_label = UM.Message(
                UM.MessageCode.LANGUAGE_DIRECTION_DETECTED, ("English", "简体中文"))
            controller._probe_status = UM.Message(
                UM.MessageCode.PROBE_RUNNING, (controller._model_display_message(),))
            controller._glossary_summary = UM.Message(UM.MessageCode.GLOSSARY_READY, (7,))
            controller._status_detail = UM.Message(
                UM.MessageCode.READY_WITH_SOURCE, (controller._model_display_message(),))
            controller._speed = 1234

            stable = (controller.translationProfile, controller._source_info,
                      controller._language_label, controller._probe_status,
                      controller._glossary_summary,
                      [row["id"] for row in controller.translationProfiles],
                      [row["family"] for row in controller.readerFonts])
            chinese = {
                "model": controller.modelDisplayName,
                "status": controller.statusDetail,
                "profiles": [row["label"] + row["detail"]
                             for row in controller.translationProfiles],
                "fonts": [row["label"] for row in controller.readerFonts],
                "source": controller.sourceInfo,
                "direction": controller.languageLabel,
                "probe": controller.probeStatus,
                "glossary": controller.glossarySummary,
                "speed": controller.speedText,
            }
            self.assertTrue(controller.setUiLocale("en"))
            english = {
                "model": controller.modelDisplayName,
                "status": controller.statusDetail,
                "profiles": [row["label"] + row["detail"]
                             for row in controller.translationProfiles],
                "fonts": [row["label"] for row in controller.readerFonts],
                "source": controller.sourceInfo,
                "direction": controller.languageLabel,
                "probe": controller.probeStatus,
                "glossary": controller.glossarySummary,
                "speed": controller.speedText,
            }
            for key in chinese:
                self.assertNotEqual(chinese[key], english[key], key)
            self.assertIn("On this Mac", english["model"])
            self.assertNotIn("本机", english["status"])
            self.assertEqual(stable, (
                controller.translationProfile, controller._source_info,
                controller._language_label, controller._probe_status,
                controller._glossary_summary,
                [row["id"] for row in controller.translationProfiles],
                [row["family"] for row in controller.readerFonts],
            ))

    def test_post_edit_compound_report_and_worker_status_rerender_without_mutation(self):
        from glossary import fingerprint

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            service, controller = harness(root)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            project = root / "review-job"
            directory = project / "译后校对"
            directory.mkdir(parents=True)
            report = {
                "revision": "post-edit-v5", "stamp": "book-v1",
                "issues": [{"id": "mara", "source": "Mara", "preferred": "玛拉",
                            "decision": "use", "variant_counts": {"玛拉": 2}}],
                "coverage": {"translated_segments": 4, "total_segments": 4,
                             "gate_accepted": 1, "gate_filtered": 0,
                             "omitted_candidates": 0},
                "failures": ["one deferred comparison"],
            }
            preferences = {}
            draft = {
                "preview_revision": "name-plan-v2",
                "plan_stamp": fingerprint({"stamp": report["stamp"],
                                           "issues": report["issues"]}),
                "preferences_stamp": fingerprint(preferences),
                "status": "completed",
                "edits": [{"id": 1, "safe": True}],
                "failures": {},
            }
            report_path = directory / "检查报告.json"
            draft_path = directory / "校订预览.json"
            report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            draft_path.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
            controller._job_path = str(project)
            controller._progress = 1
            editor = controller.postEditor
            editor.load()  # Regression: this used to crash by adding str to Message.
            self.assertIsInstance(editor._status, list)
            self.assertGreaterEqual(len(editor._status), 3)
            chinese = editor.status
            codes = [item.code for item in editor._status if isinstance(item, UM.Message)]
            before = (report_path.read_bytes(), draft_path.read_bytes(),
                      json.dumps(editor._issues, ensure_ascii=False, sort_keys=True),
                      json.dumps(editor._edits, ensure_ascii=False, sort_keys=True))
            controller.setUiLocale("en")
            self.assertNotEqual(chinese, editor.status)
            self.assertEqual(codes, [item.code for item in editor._status
                                     if isinstance(item, UM.Message)])
            self.assertEqual(before, (
                report_path.read_bytes(), draft_path.read_bytes(),
                json.dumps(editor._issues, ensure_ascii=False, sort_keys=True),
                json.dumps(editor._edits, ensure_ascii=False, sort_keys=True),
            ))

            class OneEvent:
                def __init__(self):
                    self.data = (json.dumps({
                        "type": "error", "message_code": "unknownError",
                        "message_args": [], "detail": "worker exploded",
                        "message": "worker exploded",
                    }) + "\n").encode()

                def readAllStandardOutput(self):
                    data, self.data = self.data, b""
                    return data

            editor.process = OneEvent()
            editor._read()
            editor.process = None
            self.assertEqual("unknownError", editor.statusCode)
            self.assertEqual("The operation did not finish", editor.status)
            self.assertEqual("worker exploded", editor.statusDetail)

    def test_post_edit_user_preference_keeps_its_meaning_after_locale_switch(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            service, controller = harness(root)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            project = root / "preference-job"
            directory = project / "译后校对"
            directory.mkdir(parents=True)
            (directory / "检查报告.json").write_text(json.dumps({
                "revision": "post-edit-v5", "issues": [{
                    "id": "mara", "source": "Mara", "preferred": "马拉",
                    "decision": "use", "variant_counts": {"玛拉": 2},
                }], "coverage": {}, "failures": {},
            }, ensure_ascii=False), encoding="utf-8")
            (directory / "偏好译名.json").write_text(
                json.dumps({"Mara": "玛拉"}, ensure_ascii=False), encoding="utf-8")
            controller._job_path = str(project)
            editor = controller.postEditor
            editor.load()
            self.assertIn("你指定", editor.issues[0]["display_reason"])
            controller.setUiLocale("en")
            self.assertIn("your chosen translation",
                          editor.issues[0]["display_reason"].lower())
            self.assertEqual("玛拉", editor.issues[0]["preferred"])

    def test_unknown_legacy_post_edit_error_gets_summary_and_raw_detail(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            editor = controller.postEditor

            class OneEvent:
                def __init__(self):
                    self.data = (json.dumps({
                        "type": "error", "message": "future worker exploded",
                    }) + "\n").encode()

                def readAllStandardOutput(self):
                    data, self.data = self.data, b""
                    return data

            editor.process = OneEvent()
            editor._read()
            editor.process = None
            self.assertEqual("操作未完成", editor.status)
            self.assertEqual("future worker exploded", editor.statusDetail)
            controller.setUiLocale("en")
            self.assertEqual("The operation did not finish", editor.status)
            self.assertEqual("future worker exploded", editor.statusDetail)

    def test_epub_publication_failure_is_not_reported_as_a_model_failure(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            controller._reader_load_futures = {"publication": object()}
            controller._complete_reader_load({
                "purpose": "publication", "key": "publication", "ok": False,
                "error": "zip central directory is damaged",
            })
            self.assertEqual("epubExportFailed", controller.errorCode)
            self.assertIn("zip central directory", controller.errorDetail)
            controller.setUiLocale("en")
            self.assertIn("EPUB", controller.errorMessage)

    def test_model_status_code_is_stable_across_locales(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            code = controller.modelStatusCode
            self.assertTrue(code)
            controller.setUiLocale("en")
            self.assertEqual(code, controller.modelStatusCode)


class ContentPreservationTests(unittest.TestCase):
    """A locale switch must not rewrite any stored content."""

    def setUp(self):
        application()

    def snapshot(self, root):
        result = {}
        for path in sorted(Path(root).rglob("*")):
            if path.is_file():
                result[str(path.relative_to(root))] = path.read_bytes()
        return result

    def test_task_source_translation_notes_reviews_and_answers_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            service, controller = harness(root)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            source = root / "shell.txt"
            source.write_text(SOURCE, encoding="utf-8")
            controller._select_source(source)
            with patch("native_launcher.ollama_models", return_value={'jingdu-hy-mt2:7b-q4'}):
                project = controller._prepare_job()
            (project / "译后校对").mkdir(exist_ok=True)
            (project / "译文").mkdir(exist_ok=True)
            (project / "译后校对/人工标注.json").write_text(
                json.dumps({"version": "human-review-v4", "items": [
                    {"id": "a-1", "category": "人名 / 地名", "quote": "Mara",
                     "note": "我的疑问", "translation": "玛拉"}]}, ensure_ascii=False),
                encoding="utf-8")
            (project / "译文/story.zh-CN.txt").write_text("玛拉在黎明前抵达北港。\n", encoding="utf-8")
            (project / "译后校对/偏好译名.json").write_text(
                json.dumps({"Mara": "玛拉"}, ensure_ascii=False), encoding="utf-8")
            (project / "问答.json").write_text(
                json.dumps({"answer": "这是先前的回答"}, ensure_ascii=False), encoding="utf-8")

            before = self.snapshot(project)
            self.assertTrue(controller.setUiLocale("en"))
            self.assertTrue(controller.setUiLocale("zh-CN"))
            self.assertEqual(before, self.snapshot(project))
            self.assertIn("我的疑问", (project / "译后校对/人工标注.json").read_text(encoding="utf-8"))
            self.assertIn("这是先前的回答", (project / "问答.json").read_text(encoding="utf-8"))


class EpubMetadataTests(unittest.TestCase):
    """Semantic EPUB state, publisher titles and old-cache compatibility."""

    def setUp(self):
        application()

    def inspect(self, root, *, chapters=("第一章", "第二章")):
        """Open a synthetic EPUB through the real cache/unpack path."""
        from epub_reader import open_epub
        epub = synthetic_epub(Path(root) / "book.epub", chapters)
        return open_epub(epub, cache=Path(root) / "cache")

    def test_metadata_stores_semantic_state_and_marks_generated_titles(self):
        with tempfile.TemporaryDirectory() as raw:
            book = self.inspect(raw)
            for chapter in book["chapters"]:
                self.assertIn(chapter["translationState"],
                              {"", "translated", "partial", "pending"})
                self.assertIn("generatedTitle", chapter)
            # Every chapter here has a publisher heading, so none is generated.
            self.assertEqual([], [c for c in book["chapters"] if c["generatedTitle"]])

    def test_real_epub_metadata_distinguishes_empty_pending_partial_and_translated(self):
        with tempfile.TemporaryDirectory() as raw:
            from epub_reader import open_epub
            epub = synthetic_epub(
                Path(raw) / "states.epub", ("Empty", "Pending", "Partial", "Done"),
                mapped_counts=[0, 2, 2, 2], pending_counts=[0, 2, 1, 0])
            book = open_epub(epub, cache=Path(raw) / "cache")
            self.assertEqual(
                ["", "pending", "partial", "translated"],
                [chapter["translationState"] for chapter in book["chapters"]])
            self.assertEqual("", UM.epub_state_value(0, 0))
            self.assertEqual("pending", UM.epub_state_value(2, 2))
            self.assertEqual("partial", UM.epub_state_value(2, 1))
            self.assertEqual("translated", UM.epub_state_value(2, 0))

    def test_publisher_title_that_looks_generated_stays_content(self):
        with tempfile.TemporaryDirectory() as raw:
            from epub_reader import open_epub
            epub = Path(raw) / "book.epub"
            synthetic_epub(epub, ("第 1 节", "第 2 节"), headings=True)
            book = open_epub(epub, cache=Path(raw) / "cache")
            titles = [chapter["title"] for chapter in book["chapters"]]
            self.assertEqual(["第 1 节", "第 2 节"], titles)
            self.assertEqual([], [c for c in book["chapters"] if c["generatedTitle"]])

    def test_generated_fallback_label_is_marked_and_localizable(self):
        with tempfile.TemporaryDirectory() as raw:
            from epub_reader import open_epub
            # No headings and no nav labels, so the reader must invent defaults.
            epub = synthetic_epub(Path(raw) / "plain.epub", headings=False)
            book = open_epub(epub, cache=Path(raw) / "cache")
            generated = [c for c in book["chapters"] if c["generatedTitle"]]
            self.assertTrue(generated, book["chapters"])
            for chapter in generated:
                self.assertTrue(chapter.get("generatedIndex"))

    def test_old_chinese_state_projects_to_english_without_rewriting(self):
        with tempfile.TemporaryDirectory() as raw:
            import epub_session
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            legacy = [{"title": "第一章", "path": "c1.xhtml", "translationState": "已译"},
                      {"title": "第 2 节", "path": "c2.xhtml", "translationState": "部分已译"}]
            projected = [epub_session._project_chapter(dict(c)) for c in legacy]
            # In Chinese the legacy value and the projection read the same, but
            # the stable code is already semantic.
            self.assertEqual("已译", projected[0]["translationState"])
            self.assertEqual("epubTranslated", projected[0]["translationStateCode"])
            self.assertEqual("epubPartial", projected[1]["translationStateCode"])
            # The stored metadata is never rewritten by a projection.
            self.assertEqual("已译", legacy[0]["translationState"])
            # The publisher title is content and is never touched.
            self.assertEqual("第 2 节", projected[1]["title"])

            # In English the old Chinese state projects read-only to English.
            controller.setUiLocale("en")
            projected = [epub_session._project_chapter(dict(c)) for c in legacy]
            self.assertEqual("Translated", projected[0]["translationState"])
            self.assertEqual("Partly translated", projected[1]["translationState"])
            self.assertEqual("已译", legacy[0]["translationState"], "投影不得改写存储值")
            self.assertEqual("第 2 节", projected[1]["title"], "出版社标题不得被改写")
            controller.setUiLocale("en")
            self.assertEqual("Partly translated",
                             epub_session._project_chapter(
                                 {"title": "第一章", "translationState": "部分已译"})["translationState"])

    def test_generated_title_projects_to_section_in_english(self):
        import epub_session
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            raw_chapter = {"title": "第 1 节", "path": "c1.xhtml", "translationState": "",
                           "generatedTitle": True, "generatedIndex": 1}
            self.assertEqual("第 1 节", epub_session._project_chapter(raw_chapter)["title"])
            controller.setUiLocale("en")
            self.assertEqual("Section 1", epub_session._project_chapter(raw_chapter)["title"])
            publisher = {"title": "第 1 节", "path": "c1.xhtml", "translationState": "",
                         "generatedTitle": False}
            self.assertEqual("第 1 节", epub_session._project_chapter(publisher)["title"])

    def test_epub_generated_bookmark_projects_but_publisher_and_legacy_titles_do_not(self):
        import epub_session
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            generated_chapter = {"title": "第 1 节", "path": "c1.xhtml",
                                 "translationState": "", "generatedTitle": True,
                                 "generatedIndex": 1}
            raw_mark = {"chapter": 0, "fraction": .42, "percent": 42,
                        "title": "第 1 节 · 42%", "generated_title": True}
            legacy_custom = {"chapter": 0, "fraction": .50,
                             "title": "我保存的第 1 节", "generated_title": False}
            stored = json.dumps([raw_mark, legacy_custom], ensure_ascii=False,
                                sort_keys=True)
            zh_chapters = [epub_session._project_chapter(generated_chapter)]
            self.assertEqual("第 1 节 · 42%",
                             epub_session._project_bookmark(raw_mark, zh_chapters)["title"])
            controller.setUiLocale("en")
            en_chapters = [epub_session._project_chapter(generated_chapter)]
            self.assertEqual("Section 1 · 42%",
                             epub_session._project_bookmark(raw_mark, en_chapters)["title"])
            self.assertEqual("我保存的第 1 节",
                             epub_session._project_bookmark(legacy_custom, en_chapters)["title"])
            self.assertEqual(stored, json.dumps([raw_mark, legacy_custom], ensure_ascii=False,
                                                sort_keys=True))

            publisher = [{"title": "第 1 节", "path": "c1.xhtml",
                          "translationState": "", "generatedTitle": False}]
            self.assertEqual("第 1 节 · 42%",
                             epub_session._project_bookmark(raw_mark, publisher)["title"])

    def test_locale_switch_creates_no_new_cache_generation(self):
        with tempfile.TemporaryDirectory() as raw:
            from epub_reader import cache_stats, open_epub
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            cache = controller.storage_context.cache_root
            epub = synthetic_epub(Path(raw) / "cached.epub")
            open_epub(epub, cache=cache)
            before = cache_stats(cache)["generations"]
            self.assertGreater(before, 0, "test must exercise an existing cache generation")
            self.assertTrue(controller.setUiLocale("en"))
            self.assertEqual(before, cache_stats(cache)["generations"])


class ScriptedWorkerTests(unittest.TestCase):
    """One fake worker run, with a real locale switch while it is alive."""

    def setUp(self):
        application()

    def test_locale_switch_during_a_real_fake_worker_run(self):
        from native_launcher import TranslatorController
        from locale_service import LocaleService
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            script = root / "fake_worker.py"
            script.write_text(
                "import json, sys, time\n"
                "for index in range(6):\n"
                "    print(json.dumps({'type': 'progress', 'range': [index + 1, index + 1],\n"
                "                      'percent': (index + 1) * 10.0}), flush=True)\n"
                "    time.sleep(0.15)\n"
                "print(json.dumps({'type': 'result', 'status': 'completed', 'failures': []}),\n"
                "      flush=True)\n"
                "time.sleep(0.3)\n",
                encoding="utf-8")
            settings = settings_at(root)
            service = LocaleService(settings, system_locale="zh_CN")
            service.start(application())
            controller = TranslatorController(workspace=root / "jobs", settings=settings,
                                              locale_service=service, restore=False)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            from PySide6.QtCore import QProcess
            process = QProcess(controller)
            controller._process = process
            controller._running = True
            controller._started_at = time.monotonic()
            process.readyReadStandardOutput.connect(controller._read_process)
            process.finished.connect(controller._process_finished)
            process.setProgram(sys.executable)
            process.setArguments(["-B", str(script)])
            process.start()
            self.assertTrue(process.waitForStarted(5000))
            deadline = time.monotonic() + 5
            while controller.progress <= 0 and time.monotonic() < deadline:
                application().processEvents()
                time.sleep(0.02)
            identity = process
            progress_before = controller.progress
            self.assertGreater(progress_before, 0)
            self.assertTrue(controller.setUiLocale("en"))
            self.assertIs(process, identity, "切换语言不得更换 worker 进程")
            self.assertGreaterEqual(controller.progress, progress_before)
            deadline = time.monotonic() + 5
            while not controller._finished and time.monotonic() < deadline:
                application().processEvents()
                time.sleep(0.02)
            self.assertTrue(controller._finished)
            self.assertEqual("Translation complete", controller.statusTitle)


if __name__ == "__main__":
    unittest.main()
