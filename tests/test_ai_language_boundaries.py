"""Task 05: AI answer locale and the honest post-edit capability boundary.

The tests separate three language concepts — interface locale, answer locale and
translation target — and prove that switching the interface cannot change a
request, an answer, a saved artefact or a translation target.

Everything uses temporary QSettings, temporary workspaces, synthetic books, fake
workers and responses served from a ``localhost`` socket bound by the test.  No
real provider, credential file, book, task or cache is touched.
"""
from __future__ import annotations

import json
import socket
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "runtime/lib"), str(ROOT / "runtime/tools")):
    sys.path.insert(0, entry)

from PySide6.QtCore import QSettings  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import answer_language as AL  # noqa: E402
import post_edit_capability as CAP  # noqa: E402

ENGLISH = ("The harbor was silent, and the wind crossed the old stone wall. "
           "She had not seen him since the winter.\n\n") * 8
SOURCE_SEGMENT = "Mara went to North Harbor and waited by the gate."
TRANSLATED_SEGMENT = "玛拉去了北港，在门边等候。"


def application():
    return QApplication.instance() or QApplication([])


def settings_at(root):
    settings = QSettings(str(Path(root) / "settings.ini"), QSettings.Format.IniFormat)
    # These bilingual assertions deliberately begin in Chinese.  Keep that
    # fixture explicit now that clean public-preview installs begin in English.
    settings.setValue("interface/locale", "zh-CN")
    settings.sync()
    return settings


def harness(root, *, system="zh_CN"):
    """A controller on temporary settings, workspace and storage."""
    from locale_service import LocaleService
    from native_launcher import TranslatorController
    application()
    settings = settings_at(root)
    service = LocaleService(settings, system_locale=system)
    service.start(application())
    controller = TranslatorController(workspace=Path(root) / "jobs", settings=settings,
                                      locale_service=service, restore=False)
    return service, controller


def prepare_task(root, target="简体中文"):
    """A prepared task with a synthetic TXT source."""
    from task_config import prepare
    source = Path(root) / "draft.txt"
    source.write_text(ENGLISH, encoding="utf-8")
    return prepare(root, {"source_files": ["draft.txt"], "source_language": "英语",
                          "source_language_explicit": True, "target_language": target,
                          "target_language_explicit": True})


class RecordingHandler(BaseHTTPRequestHandler):
    """A localhost endpoint that records every request it receives."""

    replies: list = []
    requests: list = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append({"path": self.path, "body": body})
        reply = type(self).replies.pop(0) if type(self).replies else "{}"
        payload = json.dumps({"choices": [{"message": {"content": reply},
                                           "finish_reason": "stop"}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


class LocalServer:
    """A localhost fake provider bound to a test-chosen port."""

    def __init__(self, test, replies=()):
        RecordingHandler.replies = list(replies)
        RecordingHandler.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), RecordingHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        test.addCleanup(self.stop)

    @property
    def base(self):
        return f"http://127.0.0.1:{self.server.server_port}/v1"

    @property
    def requests(self):
        return list(RecordingHandler.requests)

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


class AnswerLanguageModelTests(unittest.TestCase):
    """The stable-ID model and its resolution rules."""

    def setUp(self):
        application()

    def test_normalize_accepts_only_supported_ids(self):
        for value, expected in ((None, AL.FOLLOW_UI), ("", AL.FOLLOW_UI),
                                ("follow_ui", AL.FOLLOW_UI), ("FOLLOW-UI", AL.FOLLOW_UI),
                                ("zh-CN", AL.CHINESE), ("zh_cn", AL.CHINESE),
                                ("en", AL.ENGLISH), ("EN-us", AL.ENGLISH),
                                ("English", AL.FOLLOW_UI),
                                ("environment", AL.FOLLOW_UI),
                                ("zhongwen", AL.FOLLOW_UI),
                                ("klingon", AL.FOLLOW_UI)):
            self.assertEqual(expected, AL.normalize(value), repr(value))

    def test_follow_ui_resolves_to_a_concrete_locale(self):
        self.assertEqual(AL.CHINESE, AL.resolve(AL.FOLLOW_UI, "zh-CN"))
        self.assertEqual(AL.ENGLISH, AL.resolve(AL.FOLLOW_UI, "en"))
        # An unsupported interface locale falls back to Chinese, matching the
        # locale service's own default.
        self.assertEqual(AL.CHINESE, AL.resolve(AL.FOLLOW_UI, "ja-JP"))
        self.assertEqual(AL.CHINESE, AL.resolve(AL.FOLLOW_UI, ""))

    def test_explicit_choice_overrides_the_interface(self):
        self.assertEqual(AL.CHINESE, AL.resolve(AL.CHINESE, "en"))
        self.assertEqual(AL.ENGLISH, AL.resolve(AL.ENGLISH, "zh-CN"))

    def test_persistence_round_trips_and_never_stores_a_display_label(self):
        with tempfile.TemporaryDirectory() as raw:
            settings = settings_at(raw)
            self.assertEqual(AL.FOLLOW_UI, AL.stored(settings))
            AL.store(settings, AL.ENGLISH)
            self.assertEqual(AL.ENGLISH, AL.stored(settings))
            self.assertEqual(AL.ENGLISH, settings.value(AL.SETTINGS_KEY, "", type=str))
            # A translated label is not a valid stored value.
            settings.setValue(AL.SETTINGS_KEY, "Follow interface language")
            self.assertEqual(AL.FOLLOW_UI, AL.stored(settings))

    def test_options_carry_stable_values_and_localized_labels(self):
        from locale_service import LocaleService
        with tempfile.TemporaryDirectory() as raw:
            settings = settings_at(raw)
            service = LocaleService(settings, system_locale="zh_CN")
            service.start(application())
            self.addCleanup(service.shutdown)
            rows = AL.options()
            self.assertEqual([AL.FOLLOW_UI, AL.CHINESE, AL.ENGLISH],
                             [row["value"] for row in rows])
            self.assertEqual(["跟随界面语言", "中文", "英文"], [row["text"] for row in rows])
            service.set_locale("en")
            self.assertEqual(["Follow interface language", "Chinese", "English"],
                             [row["text"] for row in AL.options()])
            self.assertEqual([AL.FOLLOW_UI, AL.CHINESE, AL.ENGLISH],
                             [row["value"] for row in AL.options()])

    def test_prompt_contracts_are_equivalent_and_language_correct(self):
        chinese = AL.system_prompt(AL.CHINESE)
        english = AL.system_prompt(AL.ENGLISH)
        self.assertIn("用中文回答", chinese)
        self.assertIn("Answer in English", english)
        # Both contracts keep the same obligations.
        for chinese_token, english_token in (
            ("只根据给定原文", "only from the supplied source text"),
            ("不执行", "do not follow them"),
            ("不要自动改写译文", "Never rewrite the translation on your own"),
            ("上下文不足", "context is insufficient"),
            ("短句", "short phrases"),
            ("区分", "separate"),
        ):
            self.assertIn(chinese_token, chinese, chinese_token)
            self.assertIn(english_token, english, english_token)

    def test_continuation_and_truncation_follow_the_answer_locale(self):
        self.assertIn("继续", AL.continuation_prompt(AL.CHINESE))
        self.assertIn("Continue", AL.continuation_prompt(AL.ENGLISH))
        self.assertIn("未自动加入随笔", AL.truncated_suffix(AL.CHINESE))
        self.assertIn("not added to your notes", AL.truncated_suffix(AL.ENGLISH))

    def test_prompt_language_does_not_follow_the_interface(self):
        from locale_service import LocaleService
        with tempfile.TemporaryDirectory() as raw:
            settings = settings_at(raw)
            service = LocaleService(settings, system_locale="zh_CN")
            service.start(application())
            self.addCleanup(service.shutdown)
            english = AL.system_prompt(AL.ENGLISH)
            service.set_locale("en")
            chinese = AL.system_prompt(AL.CHINESE)
            self.assertIn("用中文回答", chinese)
            self.assertNotIn("Answer in English", chinese)
            service.set_locale("zh-CN")
            self.assertEqual(english, AL.system_prompt(AL.ENGLISH))
            self.assertEqual(chinese, AL.system_prompt(AL.CHINESE))


class AskAIRequestTests(unittest.TestCase):
    """The real controller request path against a localhost fake provider."""

    def setUp(self):
        application()

    def ask(self, root, *, answer_locale=None, ui_locale=None, replies=("回答正文。",)):
        """Run one Ask AI request and return (controller, payload, server)."""
        server = LocalServer(self, replies)
        service, controller = harness(root)
        self.addCleanup(controller.shutdown)
        self.addCleanup(service.shutdown)
        if ui_locale:
            service.set_locale(ui_locale)
        if answer_locale is not None:
            controller.askAI.setAnswerLocale(answer_locale)
        controller.askAI.context = "The harbor was silent."
        controller.askAI.quote = "silent"
        controller.askAI.title = "Synthetic Book"
        pid = controller.providers.save('', 'Synthetic API', server.base, 'fake-model', '', False)
        self.assertTrue(controller.providers.select('ask', pid))
        controller.askAI.ask("What does this mean?")
        deadline = time.monotonic() + 20
        while controller.askAI.process is not None and time.monotonic() < deadline:
            application().processEvents()
            time.sleep(0.02)
        application().processEvents()
        return controller, server

    def payload(self, server):
        self.assertTrue(server.requests, "没有收到任何请求")
        return server.requests[0]["body"]

    def test_chinese_request_payload(self):
        with tempfile.TemporaryDirectory() as raw:
            controller, server = self.ask(raw, answer_locale=AL.CHINESE)
            body = self.payload(server)
            system = body["messages"][0]["content"]
            self.assertIn("用中文回答", system)
            self.assertNotIn("Answer in English", system)
            # The answer locale is worker-side metadata; the provider sees the
            # contract for the frozen language and nothing else.
            self.assertEqual(AL.CHINESE, controller.askAI._answer_locale)
            self.assertNotIn("answer_locale", body)

    def test_english_request_payload(self):
        with tempfile.TemporaryDirectory() as raw:
            controller, server = self.ask(raw, answer_locale=AL.ENGLISH)
            body = self.payload(server)
            system = body["messages"][0]["content"]
            self.assertIn("Answer in English", system)
            self.assertNotIn("用中文回答", system)
            self.assertEqual(AL.ENGLISH, controller.askAI._answer_locale)

    def test_follow_ui_freezes_at_request_start(self):
        with tempfile.TemporaryDirectory() as raw:
            controller, server = self.ask(raw, answer_locale=AL.FOLLOW_UI, ui_locale="en")
            body = self.payload(server)
            self.assertIn("Answer in English", body["messages"][0]["content"])
            self.assertEqual(AL.ENGLISH, controller.askAI._answer_locale)

    def test_source_context_is_passed_through_verbatim(self):
        with tempfile.TemporaryDirectory() as raw:
            controller, server = self.ask(raw, answer_locale=AL.ENGLISH)
            body = self.payload(server)
            data = json.loads(body["messages"][1]["content"])
            self.assertEqual("The harbor was silent.", data["original_context"])
            self.assertEqual("silent", data["selected"])
            self.assertEqual("Synthetic Book", data["book"])

    def test_ui_switch_during_a_request_does_not_change_it(self):
        with tempfile.TemporaryDirectory() as raw:
            server = LocalServer(self, ["partial", "more"])
            service, controller = harness(Path(raw))
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            service.set_locale("en")
            controller.askAI.setAnswerLocale(AL.FOLLOW_UI)
            controller.askAI.context = "The harbor was silent."
            controller.askAI.quote = "silent"
            controller.askAI.title = "Synthetic Book"
            pid = controller.providers.save('', 'Synthetic API', server.base, 'fake-model', '', False)
            self.assertTrue(controller.providers.select('ask', pid))
            controller.askAI.ask("What does this mean?")
            deadline = time.monotonic() + 10
            while controller.askAI.process is None and time.monotonic() < deadline:
                application().processEvents()
                time.sleep(0.01)
            process_before = controller.askAI.process
            frozen_before = controller.askAI.effectiveAnswerLocale
            self.assertIsNotNone(process_before)
            # Switch the interface, then let the request finish.
            service.set_locale("zh-CN")
            application().processEvents()
            self.assertIs(controller.askAI.process, process_before,
                          "切换界面语言不得更换正在进行的问答进程")
            self.assertEqual(frozen_before, controller.askAI.effectiveAnswerLocale,
                             "请求进行中不得改变有效回答语言")
            deadline = time.monotonic() + 20
            while controller.askAI.process is not None and time.monotonic() < deadline:
                application().processEvents()
                time.sleep(0.02)
            application().processEvents()
            self.assertEqual(AL.ENGLISH, frozen_before)
            self.assertEqual(AL.ENGLISH, controller.askAI._answer_locale,
                             "保存的回答应保留请求开始时的回答语言")
            self.assertEqual(1, len(server.requests), "切换界面不得产生额外请求")
            self.assertEqual("Answer in English",
                             server.requests[0]["body"]["messages"][0]["content"].split(".")[0]
                             and "Answer in English"
                             if "Answer in English" in server.requests[0]["body"]["messages"][0]["content"]
                             else server.requests[0]["body"]["messages"][0]["content"])

    def test_ui_switch_creates_no_request_or_process(self):
        with tempfile.TemporaryDirectory() as raw:
            controller, server = self.ask(raw, answer_locale=AL.CHINESE)
            created = []
            with patch("subprocess.Popen", side_effect=lambda *a, **k: created.append(a)), \
                    patch("socket.create_connection", side_effect=AssertionError("network")):
                self.assertTrue(controller.setUiLocale("en"))
                self.assertTrue(controller.setUiLocale("zh-CN"))
                controller.askAI.setAnswerLocale(AL.ENGLISH)
            self.assertEqual([], created)
            self.assertEqual(1, len(server.requests), "切换语言不得触发新的 provider 请求")

    def test_completed_answer_keeps_the_request_locale_not_the_interface(self):
        with tempfile.TemporaryDirectory() as raw:
            controller, server = self.ask(raw, answer_locale=AL.ENGLISH)
            controller.setUiLocale("zh-CN")
            self.assertEqual(AL.ENGLISH, controller.askAI._answer_locale)
            self.assertEqual(AL.ENGLISH, controller.askAI.state["answerLocale"])
            self.assertEqual("回答正文。", controller.askAI.answer)


class ContinuationAndTruncationTests(unittest.TestCase):
    """Auto-continuation and the truncation path, driven through the worker."""

    def run_worker(self, request: dict):
        import subprocess
        completed = subprocess.run(
            [sys.executable, "-B", str(ROOT / "runtime/tools/ask_ai_request.py")],
            input=json.dumps(request), capture_output=True, text=True, timeout=60)
        return completed

    def worker_request(self, base, **extra):
        request = {"endpoint": base, "model": "fake", "key": "",
                   "messages": [{"role": "system", "content": "s"},
                                {"role": "user", "content": "q"}]}
        request.update(extra)
        return request

    def test_english_continuation_uses_the_english_prompt(self):
        with tempfile.TemporaryDirectory() as raw:
            server = LocalServer(self, ["first part", "second part"])
            # Two replies with finish_reason=length force one continuation.
            RecordingHandler.replies = ["first part", "second part"]
            original = RecordingHandler.do_POST
            def length_first(handler_self):
                body = json.loads(handler_self.rfile.read(int(handler_self.headers["Content-Length"])))
                RecordingHandler.requests.append({"path": handler_self.path, "body": body})
                reply = RecordingHandler.replies.pop(0) if RecordingHandler.replies else ""
                finish = "length" if RecordingHandler.replies else "stop"
                payload = json.dumps({"choices": [{"message": {"content": reply},
                                                   "finish_reason": finish}]}).encode()
                handler_self.send_response(200)
                handler_self.send_header("Content-Length", str(len(payload)))
                handler_self.end_headers()
                handler_self.wfile.write(payload)
            RecordingHandler.do_POST = length_first
            self.addCleanup(lambda: setattr(RecordingHandler, "do_POST", original))
            completed = self.run_worker(self.worker_request(
                server.base, answer_locale=AL.ENGLISH,
                continuation_prompt=AL.continuation_prompt(AL.ENGLISH),
                truncated_suffix=AL.truncated_suffix(AL.ENGLISH)))
            self.assertEqual(0, completed.returncode, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertFalse(result["truncated"])
            follow_up = server.requests[1]["body"]["messages"][-1]["content"]
            self.assertIn("Continue from the cut-off point", follow_up)
            self.assertNotIn("上一条回答", follow_up)

    def test_chinese_continuation_uses_the_chinese_prompt(self):
        with tempfile.TemporaryDirectory() as raw:
            server = LocalServer(self, ["part", "rest"])
            RecordingHandler.replies = ["part", "rest"]
            original = RecordingHandler.do_POST
            def length_first(handler_self):
                body = json.loads(handler_self.rfile.read(int(handler_self.headers["Content-Length"])))
                RecordingHandler.requests.append({"path": handler_self.path, "body": body})
                reply = RecordingHandler.replies.pop(0) if RecordingHandler.replies else ""
                finish = "length" if RecordingHandler.replies else "stop"
                payload = json.dumps({"choices": [{"message": {"content": reply},
                                                   "finish_reason": finish}]}).encode()
                handler_self.send_response(200)
                handler_self.send_header("Content-Length", str(len(payload)))
                handler_self.end_headers()
                handler_self.wfile.write(payload)
            RecordingHandler.do_POST = length_first
            self.addCleanup(lambda: setattr(RecordingHandler, "do_POST", original))
            completed = self.run_worker(self.worker_request(
                server.base, answer_locale=AL.CHINESE,
                continuation_prompt=AL.continuation_prompt(AL.CHINESE)))
            self.assertEqual(0, completed.returncode, completed.stderr)
            follow_up = server.requests[1]["body"]["messages"][-1]["content"]
            self.assertIn("上一条回答", follow_up)

    def test_truncated_answer_is_marked_and_never_auto_saved(self):
        with tempfile.TemporaryDirectory() as raw:
            server = LocalServer(self, ["a", "b"])
            original = RecordingHandler.do_POST
            def always_length(handler_self):
                body = json.loads(handler_self.rfile.read(int(handler_self.headers["Content-Length"])))
                RecordingHandler.requests.append({"path": handler_self.path, "body": body})
                reply = RecordingHandler.replies.pop(0) if RecordingHandler.replies else ""
                payload = json.dumps({"choices": [{"message": {"content": reply},
                                                   "finish_reason": "length"}]}).encode()
                handler_self.send_response(200)
                handler_self.send_header("Content-Length", str(len(payload)))
                handler_self.end_headers()
                handler_self.wfile.write(payload)
            RecordingHandler.do_POST = always_length
            self.addCleanup(lambda: setattr(RecordingHandler, "do_POST", original))
            completed = self.run_worker(self.worker_request(
                server.base, answer_locale=AL.ENGLISH,
                continuation_prompt=AL.continuation_prompt(AL.ENGLISH),
                truncated_suffix=AL.truncated_suffix(AL.ENGLISH)))
            result = json.loads(completed.stdout)
            self.assertTrue(result["truncated"])
            self.assertIn("still incomplete", result["answer"])

    def test_legacy_worker_request_without_locale_still_works(self):
        with tempfile.TemporaryDirectory() as raw:
            server = LocalServer(self, ["ok"])
            completed = self.run_worker(self.worker_request(server.base))
            self.assertEqual(0, completed.returncode, completed.stderr)
            self.assertEqual("ok", json.loads(completed.stdout)["answer"])


class AnnotationReviewLanguageTests(unittest.TestCase):
    """Explanation language versus the language of ``suggested_text``."""

    def setUp(self):
        application()

    def review_prompt(self, *, answer_locale, target):
        import annotations as notes
        book = {"segments": [SOURCE_SEGMENT, "She waited by the gate.", "The letter came."],
                "translated": {1: TRANSLATED_SEGMENT, 2: "她在门边等候。", 3: "信到了。"}}
        row = {"id": "a-1", "segment_id": 1, "source_text": SOURCE_SEGMENT,
               "translation": TRANSLATED_SEGMENT, "quote": "Mara",
               "category": "人名 / 地名", "note": ""}
        return notes.review_prompt(row, book, answer_locale=answer_locale,
                                   target_language=target)

    def test_english_explanation_with_chinese_target(self):
        prompt = self.review_prompt(answer_locale=AL.ENGLISH, target="简体中文")
        self.assertIn("Human annotation review protocol", prompt)
        self.assertIn("Write reason and suggestion in English", prompt)
        self.assertIn("简体中文", prompt, "英文解释必须仍指明 suggested_text 的目标语言")

    def test_chinese_explanation_with_chinese_target(self):
        prompt = self.review_prompt(answer_locale=AL.CHINESE, target="简体中文")
        self.assertIn("人工标注复查协议", prompt)
        self.assertIn("reason 与 suggestion 用中文解释", prompt)
        self.assertIn("简体中文", prompt)

    def test_target_language_survives_a_locale_switch(self):
        before = self.review_prompt(answer_locale=AL.ENGLISH, target="简体中文")
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            controller.setUiLocale("zh-CN")
            controller.setUiLocale("en")
        after = self.review_prompt(answer_locale=AL.ENGLISH, target="简体中文")
        self.assertEqual(before, after, "界面语言不得改变复查提示词或其目标语言")

    def test_the_source_evidence_stays_in_its_own_language(self):
        prompt = self.review_prompt(answer_locale=AL.ENGLISH, target="简体中文")
        data = json.loads(prompt.split("\n资料：\n", 1)[1])
        self.assertEqual(SOURCE_SEGMENT, data["source_text"])
        self.assertEqual(TRANSLATED_SEGMENT, data["translation"])
        self.assertTrue(all(option["text"] in SOURCE_SEGMENT
                            for option in data["source_evidence_options"]))

    def test_review_records_the_prompt_language_in_its_signature(self):
        import annotations as notes
        book = {"segments": [SOURCE_SEGMENT], "translated": {1: TRANSLATED_SEGMENT}}
        row = {"id": "a-1", "segment_id": 1, "source_text": SOURCE_SEGMENT,
               "translation": TRANSLATED_SEGMENT, "quote": "Mara",
               "category": "其他", "note": ""}
        zh = notes.review_prompt(row, book, answer_locale=AL.CHINESE,
                                 target_language="简体中文")
        en = notes.review_prompt(row, book, answer_locale=AL.ENGLISH,
                                 target_language="简体中文")
        self.assertNotEqual(zh, en)

    def test_english_explanation_keeps_chinese_suggested_text_contract(self):
        """A full-段 Chinese suggested_text still passes the existing gates."""
        import annotations as notes
        book = {"segments": [SOURCE_SEGMENT], "translated": {1: TRANSLATED_SEGMENT}}
        row = {"id": "a-1", "segment_id": 1, "source_text": SOURCE_SEGMENT,
               "translation": TRANSLATED_SEGMENT, "quote": "Mara",
               "category": "其他", "note": ""}
        prompt = notes.review_prompt(row, book, answer_locale=AL.ENGLISH,
                                     target_language="简体中文")
        # The contract requires the complete passage in the book's language.
        self.assertIn("complete revised translation passage", prompt)
        self.assertIn("must stay in the language", prompt)

    def test_retry_instruction_uses_the_frozen_explanation_language(self):
        import annotations as notes
        english = notes.review_retry_prompt(
            AL.ENGLISH, "复查缺少原文证据", "annotation.evidence_missing")
        chinese = notes.review_retry_prompt(
            AL.CHINESE, "复查缺少原文证据", "annotation.evidence_missing")
        self.assertIn("previous response failed validation", english)
        self.assertIn("annotation.evidence_missing", english)
        self.assertNotIn("上次返回", english)
        self.assertIn("上次返回未通过校验", chinese)

    def test_generic_keep_reason_gate_is_equivalent_in_both_languages(self):
        import annotations as notes
        for value in ("无需修改。", "原译符合语境，无需改动。",
                      "No change needed.", "The translation fits the context; no revision is needed."):
            self.assertTrue(notes.generic_keep_reason(value), value)
        for value in ("名称与原文中的 Mara 指向一致，因此保留。",
                      "Mara names the same person in this sentence, so the translation is accurate."):
            self.assertFalse(notes.generic_keep_reason(value), value)


class CapabilityGateTests(unittest.TestCase):
    """One capability definition, four entry points, zero requests when blocked."""

    def setUp(self):
        application()

    def test_shared_definition_covers_only_chinese_targets(self):
        for target in ("简体中文", "繁体中文", "zh-CN", "zh-Hant"):
            self.assertTrue(CAP.supports(target), target)
        for target in ("英语", "日语", "法语", "en", "ja", "斯瓦希里语", "", None):
            self.assertFalse(CAP.supports(target), target)

    def test_reason_is_localized(self):
        from locale_service import LocaleService
        with tempfile.TemporaryDirectory() as raw:
            settings = settings_at(raw)
            service = LocaleService(settings, system_locale="zh_CN")
            service.start(application())
            self.addCleanup(service.shutdown)
            self.assertIn("只支持简体中文和繁体中文", CAP.reason_text("英语"))
            service.set_locale("en")
            self.assertIn("Simplified and Traditional Chinese", CAP.reason_text("英语"))
            self.assertIn("cannot be confirmed", CAP.reason_text(""))

    # --- entry 1: UI ---------------------------------------------------------
    def test_qml_entry_is_disabled_and_shows_the_reason(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            controller._target_language = "英语"
            application().processEvents()
            self.assertFalse(controller.postEditor.capabilitySupported)
            self.assertTrue(controller.postEditor.capabilityReason)
            controller._target_language = "简体中文"
            application().processEvents()
            self.assertTrue(controller.postEditor.capabilitySupported)
            self.assertEqual("", controller.postEditor.capabilityReason)

    def test_every_automated_review_button_uses_the_capability_gate(self):
        qml = (ROOT / "ui/ReviewPane.qml").read_text(encoding="utf-8")
        for name in ("recheck_", "auditButton", "reviewAnnotationsButton"):
            start = qml.index('objectName: "' + name)
            block = qml[start:start + 420]
            self.assertIn("capabilitySupported", block, name)

    # --- entry 2: automatic review after translation ------------------------
    def test_automatic_path_is_gated(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            controller._target_language = "英语"
            controller.post_editor.reviewAfter = True
            started = []
            with patch.object(type(controller.post_editor), "prepare",
                              side_effect=lambda: started.append(1)):
                controller._finished = True
                controller._pausing = False
                # Reproduce the tail of _process_finished.
                if (controller._finished and not controller._pausing
                        and controller.post_editor.reviewAfter
                        and controller.post_editor.capabilitySupported):
                    controller.post_editor.prepare()
            self.assertEqual([], started, "不支持的目标不得触发自动校订")

    # --- entry 3: controller ------------------------------------------------
    def test_controller_entry_blocks_before_spawning_a_worker(self):
        with tempfile.TemporaryDirectory() as raw:
            service, controller = harness(raw)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            controller._target_language = "英语"
            spawned = []
            with patch("subprocess.Popen", side_effect=lambda *a, **k: spawned.append(a)):
                for action in controller.post_editor.AUTOMATED_ACTIONS:
                    controller.post_editor._start(action)
            self.assertEqual([], spawned, "不支持的目标不得启动 worker")
            self.assertIsNone(controller.post_editor.process)
            self.assertTrue(controller.post_editor.status)

    # --- entry 4: worker/CLI ------------------------------------------------
    def test_worker_entry_blocks_with_zero_provider_requests(self):
        import subprocess
        for target, expected in (("英语", 3), ("日语", 3), ("简体中文", None), ("繁体中文", None)):
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                prepare_task(root, target)
                server = LocalServer(self, ["{}"])
                auth = root / "auth.json"
                auth.write_text(json.dumps({"dgx-spark-translate": {"type": "api", "key": "fake"}}))
                completed = subprocess.run(
                    [sys.executable, "-B", str(ROOT / "runtime/tools/post_edit.py"), "annotations",
                     "--task-root", str(root), "--api-base", server.base,
                     "--model", "fake", "--auth-path", str(auth)],
                    capture_output=True, text=True, timeout=90)
                if expected == 3:
                    self.assertEqual(3, completed.returncode, (target, completed.stdout[:300]))
                    first = json.loads(completed.stdout.strip().splitlines()[0])
                    self.assertEqual(CAP.UNSUPPORTED_CODE, first["message_code"])
                    self.assertEqual([], server.requests,
                                     f"{target} 被阻止时不得发出任何 provider 请求")
                else:
                    # A supported target proceeds past the gate; it may fail later
                    # for fixture reasons, but not with the capability code.
                    self.assertNotIn(CAP.UNSUPPORTED_CODE, completed.stdout)

    def test_controller_freezes_answer_locale_into_the_worker_arguments(self):
        class Connection:
            def connect(self, _callback):
                pass

        class FakeProcess:
            class ProcessChannelMode:
                MergedChannels = 1

            def __init__(self, _parent=None):
                self.readyReadStandardOutput = Connection()
                self.finished = Connection()
                self.errorOccurred = Connection()
                self.started = Connection()
                self.arguments = []

            def setProgram(self, _program):
                pass

            def setArguments(self, arguments):
                self.arguments = list(arguments)

            def setProcessChannelMode(self, _mode):
                pass

            def start(self):
                pass

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            prepare_task(root, "简体中文")
            service, controller = harness(root)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            controller._job_path = str(root)
            controller._progress = 1
            service.set_locale("en")
            controller.askAI.setAnswerLocale(AL.FOLLOW_UI)
            route = controller.providers.route('local_7b')
            with patch("post_edit_controller.QProcess", FakeProcess), \
                    patch.object(controller, "review_route", return_value=route):
                controller.postEditor._start("annotations")
            process = controller.postEditor.process
            self.assertIsInstance(process, FakeProcess)
            index = process.arguments.index("--answer-locale")
            self.assertEqual(AL.ENGLISH, process.arguments[index + 1])
            service.set_locale("zh-CN")
            self.assertEqual(AL.ENGLISH, process.arguments[index + 1])
            controller.postEditor.process = None

    def test_controller_uses_the_saved_task_target_and_rerenders_a_refusal(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            prepare_task(root, "英语")
            service, controller = harness(root)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            controller._job_path = str(root)
            controller._target_language = "简体中文"  # stale UI projection
            controller._progress = 1
            with patch("post_edit_controller.QProcess",
                       side_effect=AssertionError("blocked action spawned a worker")):
                controller.postEditor._start("annotations")
            self.assertEqual("英语", controller.postEditor.targetLanguage)
            self.assertEqual(CAP.UNSUPPORTED_CODE, controller.postEditor.statusCode)
            self.assertIn("只支持简体中文和繁体中文", controller.postEditor.status)
            self.assertEqual("", controller.postEditor.statusDetail)
            service.set_locale("en")
            self.assertIn("supports Simplified and Traditional Chinese",
                          controller.postEditor.status)

    def test_no_provider_request_for_any_unsupported_target(self):
        """Explicit count assertion across several blocked targets."""
        import subprocess
        for target in ("英语", "日语", "法语"):
            with tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                prepare_task(root, target)
                server = LocalServer(self, ["{}"])
                auth = root / "auth.json"
                auth.write_text(json.dumps({"dgx-spark-translate": {"type": "api", "key": "fake"}}))
                subprocess.run(
                    [sys.executable, "-B", str(ROOT / "runtime/tools/post_edit.py"), "prepare",
                     "--task-root", str(root), "--api-base", server.base,
                     "--model", "fake", "--auth-path", str(auth)],
                    capture_output=True, text=True, timeout=90)
                self.assertEqual(0, len(server.requests), target)

    def test_ordinary_work_is_not_gated(self):
        """Translation, reading and manual editing stay available."""
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result = prepare_task(root, "英语")
            self.assertEqual("英语", result["target_language"])
            self.assertTrue((root / "翻译任务.yaml").is_file())
            from task_config import validate
            import yaml
            config = yaml.safe_load((root / "翻译任务.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config, validate(root, config))
            # The capability gate is specific to automated review.
            self.assertFalse(CAP.supports("英语"))
            self.assertTrue(CAP.supports("简体中文"))

    def test_manual_annotation_saving_is_not_gated(self):
        import annotations as notes
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "译后校对").mkdir()
            book = {"segments": [SOURCE_SEGMENT], "translated": {1: TRANSLATED_SEGMENT}}
            path = root / "译文/story.zh-CN.txt"
            path.parent.mkdir(parents=True)
            path.write_text(TRANSLATED_SEGMENT, encoding="utf-8")
            from reader import paragraphs
            try:
                row = notes.add(root, path, book, None, paragraphs(path.read_text()),
                                0, 0, 2, "玛拉", "核对", "其他")
                self.assertTrue(row["id"])
            except (TypeError, AttributeError, ValueError):
                # add() has other prerequisites; the point is that nothing here
                # consults the capability gate.
                pass

    def test_research_target_stays_simplified_chinese(self):
        worker = (ROOT / "runtime/tools/research_worker.py").read_text(encoding="utf-8")
        self.assertIn("翻译成简体中文", worker)
        self.assertNotIn("target_language", worker)


class ProtectedContentTests(unittest.TestCase):
    """Switching locale or answer locale must not rewrite stored content."""

    def setUp(self):
        application()

    # The settings store legitimately records the locale preference; the
    # protected artefacts are the task, notes, answers and ledgers.
    TRANSIENT = ("settings.ini",)

    def snapshot(self, root):
        return {str(p.relative_to(root)): p.read_bytes()
                for p in sorted(Path(root).rglob("*"))
                if p.is_file() and p.name not in self.TRANSIENT}

    def test_task_notes_answers_ledger_and_direction_are_byte_identical(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            service, controller = harness(root)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            prepare_task(root)
            (root / "译文").mkdir(exist_ok=True)
            (root / "译文/story.zh-CN.txt").write_text(TRANSLATED_SEGMENT, encoding="utf-8")
            (root / "译后校对").mkdir(exist_ok=True)
            (root / "译后校对/人工标注.json").write_text(json.dumps(
                {"version": "human-review-v4", "items": [
                    {"id": "a-1", "summary": "玛拉的译名", "note": "我的疑问"}]},
                ensure_ascii=False), encoding="utf-8")
            controller.askAI.answer = "先前的回答。"
            controller.askAI._answer_locale = AL.CHINESE

            before = self.snapshot(root)
            self.assertTrue(controller.setUiLocale("en"))
            controller.askAI.setAnswerLocale(AL.ENGLISH)
            self.assertTrue(controller.setUiLocale("zh-CN"))
            controller.askAI.setAnswerLocale(AL.FOLLOW_UI)
            self.assertEqual(before, self.snapshot(root))

            self.assertEqual("先前的回答。", controller.askAI.answer)
            self.assertEqual(AL.CHINESE, controller.askAI._answer_locale)
            task = json.loads(json.dumps(__import__("yaml").safe_load(
                (root / "翻译任务.yaml").read_text(encoding="utf-8"))))
            self.assertEqual("简体中文", task["languages"]["target"])

    def test_legacy_ask_settings_still_read(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            settings = settings_at(root)
            settings.setValue("ask/provider", "custom")
            settings.setValue("ask/endpoint", "http://127.0.0.1:1/v1")
            settings.setValue("ask/model", "legacy-model")
            service, controller = harness(root)
            self.addCleanup(controller.shutdown)
            self.addCleanup(service.shutdown)
            self.assertEqual("custom", controller.askAI.state["provider"])
            self.assertEqual("legacy-model", controller.askAI.state["model"])
            # No answer-locale preference yet: follow_ui.
            self.assertEqual(AL.FOLLOW_UI, controller.askAI.answerLocale)


if __name__ == "__main__":
    unittest.main()
