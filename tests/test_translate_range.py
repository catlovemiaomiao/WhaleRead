from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import re
import tempfile
import threading
import unittest
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "translate_range", ROOT / "runtime/tools/translate_range.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class HyHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []
    invalid_responses_remaining = 0
    always_invalid_segments: set[int] = set()

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(length).decode("utf-8"))
        prompt = payload["messages"][0]["content"]
        source = prompt.split("<<<BEGIN_SOURCE_SEGMENTS>>>\n", 1)[1].split(
            "\n<<<END_SOURCE_SEGMENTS>>>", 1
        )[0]
        numbers = [int(value) for value in re.findall(r"^<<<SEG:(\d{6})>>>$", source, re.MULTILINE)]
        type(self).requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "prompt": prompt,
            }
        )
        if type(self).always_invalid_segments.intersection(numbers):
            content = "格式错误"
        elif type(self).invalid_responses_remaining:
            type(self).invalid_responses_remaining -= 1
            content = "只翻译此段"
        else:
            content = "\n".join(
                f"<<<SEG:{number:06d}>>>\n模拟译文（第 {number} 段）" + " 译文内容" * 8
                for number in numbers
            )
        body = json.dumps(
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": content},
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class TranslateRangeTests(unittest.TestCase):
    def setUp(self) -> None:
        HyHandler.requests = []
        HyHandler.invalid_responses_remaining = 0
        HyHandler.always_invalid_segments = set()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), HyHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def make_task(self, root: Path) -> Path:
        source_dir = root / "原文"
        source_dir.mkdir(parents=True)
        source = source_dir / "sample.txt"
        source.write_text(
            "\n\n".join(f"Paragraph {number}: " + ("story text " * 22) for number in range(1, 7)),
            encoding="utf-8",
        )
        config = {
            "schema_version": 1,
            "task_name": "测试",
            "languages": {"source": "英语", "target": "简体中文"},
            "source": {"files": ["原文/sample.txt"], "encoding": "UTF-8"},
            "output": {
                "directory": "译文",
                "filename_rule": "原文件名.zh-CN.txt",
                "overwrite_existing": False,
            },
            "translation": {"style": "自然、忠实"},
            "context": {"background": "", "character_voice": []},
            "chunking": {
                "segment_mode": "blank_line",
                "max_source_chars": 500,
                "parallel_requests": 2,
            },
            "progress": {"file": "翻译进度.json"},
        }
        (root / "翻译任务.yaml").write_text(
            yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        auth = root / "auth.json"
        auth.write_text(
            json.dumps({"dgx-spark-translate": {"type": "api", "key": "test-key"}}),
            encoding="utf-8",
        )
        return auth

    def args(self, root: Path, auth: Path) -> argparse.Namespace:
        return argparse.Namespace(
            task_root=str(root),
            start_segment=1,
            end_segment=0,
            context_segments=2,
            api_base=f"http://127.0.0.1:{self.server.server_port}/v1",
            model="mock-hy",
            auth_path=str(auth),
            request_timeout=10,
        )

    def test_direct_translation_saves_progress_and_resumes_without_glossary(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            auth = self.make_task(root)
            with contextlib.redirect_stdout(io.StringIO()):
                result, success = MODULE.run(self.args(root, auth))

            self.assertTrue(success, result)
            self.assertEqual(result["status"], "completed")
            self.assertGreaterEqual(len(HyHandler.requests), 2)
            self.assertTrue(all(item["path"] == "/v1/chat/completions" for item in HyHandler.requests))
            self.assertTrue(all(item["authorization"] == "Bearer ollama" for item in HyHandler.requests))
            self.assertTrue(all("仅供理解的前文" in item["prompt"] for item in HyHandler.requests))
            self.assertTrue(all("<<<BEGIN_SOURCE_SEGMENTS>>>" in item["prompt"] for item in HyHandler.requests))
            self.assertTrue(all("只翻译此段" not in item["prompt"] for item in HyHandler.requests))
            self.assertTrue((root / "译文/sample.zh-CN.txt").is_file())
            self.assertFalse((root / "译文/sample.zh-CN.txt.partial").exists())
            self.assertFalse((root / "术语表.tsv").exists())

            progress = json.loads((root / "翻译进度.json").read_text(encoding="utf-8"))
            self.assertEqual(progress["schema_version"], 3)
            self.assertEqual(progress["protocol"], "segment-markers-v1")
            self.assertEqual(progress["completed_ranges"], [[1, 6]])
            self.assertIsNone(progress["next_segment"])
            final = (root / "译文/sample.zh-CN.txt").read_text(encoding="utf-8")
            self.assertEqual(final.count("模拟译文"), 6)
            self.assertNotIn("<<<SEG:", final)

            request_count = len(HyHandler.requests)
            resume_args = self.args(root, auth)
            resume_args.start_segment = 0
            with contextlib.redirect_stdout(io.StringIO()):
                resumed, resumed_ok = MODULE.run(resume_args)
            self.assertTrue(resumed_ok)
            self.assertEqual(resumed["status"], "already_complete")
            self.assertEqual(len(HyHandler.requests), request_count)

    def test_resume_accepts_equivalent_legacy_unicode_path_but_not_changed_text(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw) / '温泉が'
            root.mkdir()
            root = root.resolve()
            auth = self.make_task(root)
            with contextlib.redirect_stdout(io.StringIO()):
                result, success = MODULE.run(self.args(root, auth))
            self.assertTrue(success, result)

            source = (root / '原文/sample.txt').resolve()
            current = str(source)
            nfc, nfd = unicodedata.normalize('NFC', current), unicodedata.normalize('NFD', current)
            recorded = nfd if current != nfd else nfc
            self.assertNotEqual(recorded, current)
            digest = hashlib.sha256()
            digest.update(recorded.encode('utf-8'))
            digest.update(b'\0')
            digest.update(source.read_bytes())
            digest.update(b'\0')
            state_path = root / '译文/.hy-direct-state.json'
            state = json.loads(state_path.read_text(encoding='utf-8'))
            state['source_hash'] = digest.hexdigest()
            state['source_files'] = [recorded]
            state_path.write_text(json.dumps(state), encoding='utf-8')

            request_count = len(HyHandler.requests)
            resume_args = self.args(root, auth)
            resume_args.start_segment = 0
            with contextlib.redirect_stdout(io.StringIO()):
                resumed, resumed_ok = MODULE.run(resume_args)
            self.assertTrue(resumed_ok, resumed)
            self.assertEqual(resumed['status'], 'already_complete')
            self.assertEqual(len(HyHandler.requests), request_count)

            source.write_text(source.read_text() + '\nchanged', encoding='utf-8')
            with self.assertRaisesRegex(ValueError, '原文内容已变化'):
                MODULE.run(resume_args)

    def test_new_source_digest_depends_on_bytes_not_path_or_unicode_spelling(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / '母がいた.txt'
            second = root / '別名-母か\u3099いた.txt'
            first.write_bytes('同じ本文です。'.encode('utf-8'))
            second.write_bytes(first.read_bytes())
            expected = hashlib.sha256(first.read_bytes()).hexdigest()
            self.assertEqual(MODULE.source_digest([first]), expected)
            self.assertEqual(MODULE.source_digest([second]), expected)

    def test_existing_translation_is_not_overwritten_without_direct_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            auth = self.make_task(root)
            output = root / "译文"
            output.mkdir()
            final = output / "sample.zh-CN.txt"
            final.write_text("用户已有译文", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "避免覆盖"):
                MODULE.run(self.args(root, auth))
            self.assertEqual(final.read_text(encoding="utf-8"), "用户已有译文")
            self.assertEqual(HyHandler.requests, [])

    def test_auto_segmentation_handles_line_or_blank_paragraph_files(self) -> None:
        line_segments, line_mode = MODULE.split_segments("a\nb\nc\n", "auto")
        blank_segments, blank_mode = MODULE.split_segments("a\n\nb\n\nc\n", "auto")
        self.assertEqual((line_segments, line_mode), (["a", "b", "c"], "nonempty_line"))
        self.assertEqual((blank_segments, blank_mode), (["a", "b", "c"], "blank_line"))

    def test_auto_reflows_fixed_width_latin_prose(self) -> None:
        lines = []
        for number in range(12):
            lines.append(f"Paragraph {number} begins beside the old northern harbor with a long descriptive clause and")
            lines.append("continues across the fixed-width export until this complete sentence finally ends.")
        segments, mode = MODULE.split_segments("\n".join(lines), "auto")
        self.assertEqual(mode, "wrapped_prose")
        self.assertEqual(len(segments), 12)
        self.assertTrue(all("clause and continues" in segment for segment in segments))

    def test_make_chunks_obeys_segment_and_character_caps(self) -> None:
        short_segments = ["短段落"] * 8
        chunks = MODULE.make_chunks(short_segments, 1, 8, max_chars=6000, max_segments=3)
        self.assertEqual(
            [(item["start"], item["end"]) for item in chunks],
            [(1, 3), (4, 6), (7, 8)],
        )

        long_segments = ["长内容" * 80] * 3
        chunks = MODULE.make_chunks(long_segments, 1, 3, max_chars=500, max_segments=60)
        self.assertEqual(
            [(item["start"], item["end"]) for item in chunks],
            [(1, 1), (2, 2), (3, 3)],
        )

    def test_layout_preserves_original_blank_lines(self) -> None:
        segments, mode, layout = MODULE.split_source("标题\n\n甲\n乙\n\n\n丙\n", "nonempty_line")
        self.assertEqual(mode, "nonempty_line")
        self.assertEqual(segments, ["标题", "甲", "乙", "丙"])
        rendered = MODULE.render_layout(
            layout,
            {1: "标题译", 2: "甲译", 3: "乙译", 4: "丙译"},
            1,
            4,
        )
        self.assertEqual(rendered, "标题译\n\n甲译\n乙译\n\n\n丙译\n")

    def test_prompt_echo_is_retried_and_never_saved(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            auth = self.make_task(root)
            HyHandler.invalid_responses_remaining = 1
            with contextlib.redirect_stdout(io.StringIO()):
                result, success = MODULE.run(self.args(root, auth))
            self.assertTrue(success, result)
            self.assertEqual(result["status"], "completed")
            self.assertGreater(len(HyHandler.requests), 2)
            final = (root / "译文/sample.zh-CN.txt").read_text(encoding="utf-8")
            self.assertNotIn("只翻译此段", final)
            state = json.loads((root / "译文/.hy-direct-state.json").read_text(encoding="utf-8"))
            self.assertTrue(all(item["protocol"] == "segment-markers-v1" for item in state["chunks"]))

    def test_malformed_multi_segment_reply_is_bisected_before_anything_is_saved(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            auth = self.make_task(root)
            config_path = root / "翻译任务.yaml"
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            config["chunking"]["max_source_chars"] = 6000
            config_path.write_text(
                yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
            # A malformed six-segment reply must be split immediately; the
            # two smaller, valid chunks then finish it without full-block retries.
            HyHandler.invalid_responses_remaining = 1
            with contextlib.redirect_stdout(io.StringIO()):
                result, success = MODULE.run(self.args(root, auth))

            self.assertTrue(success, result)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(HyHandler.requests), 3)
            final = (root / "译文/sample.zh-CN.txt").read_text(encoding="utf-8")
            self.assertEqual(final.count("模拟译文"), 6)
            self.assertNotIn("只翻译此段", final)

    def test_successful_siblings_are_saved_when_one_leaf_stays_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            auth = self.make_task(root)
            config_path = root / "翻译任务.yaml"
            config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            config["chunking"]["max_source_chars"] = 6000
            config["chunking"]["max_segments"] = 20
            config_path.write_text(
                yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
            HyHandler.always_invalid_segments = {3}
            with patch.object(MODULE.time, "sleep", return_value=None), contextlib.redirect_stdout(io.StringIO()):
                result, success = MODULE.run(self.args(root, auth))

            self.assertFalse(success)
            self.assertEqual(result["status"], "partial_failure")
            # The stable code survives provider-independent diagnostic wording.
            failure = result["failures"][0]
            self.assertEqual(len(result["failures"]), 1)
            self.assertEqual(failure["range"], [3, 3])
            self.assertEqual(failure["error"], "模型返回中缺少段落标记")
            self.assertEqual(failure["code"], "validationFailed")
            self.assertEqual(result["completed_ranges"], [[1, 2], [4, 6]])
            state = json.loads((root / "译文/.hy-direct-state.json").read_text(encoding="utf-8"))
            saved_ids = {
                entry["id"]
                for record in state["chunks"]
                for entry in json.loads((root / "译文" / record["path"]).read_text(encoding="utf-8"))["segments"]
            }
            self.assertEqual(saved_ids, {1, 2, 4, 5, 6})
            leaf_prompts = [
                item["prompt"] for item in HyHandler.requests
                if "待翻译正文（第 3～3 段" in item["prompt"]
            ]
            self.assertTrue(leaf_prompts)
            self.assertTrue(all("仅供理解的前文（前 0 段）" in prompt for prompt in leaf_prompts))

    def test_marked_contract_rejects_missing_or_echoed_segments(self) -> None:
        source = ["甲乙丙丁" * 12, "第二段内容" * 12]
        with self.assertRaisesRegex(MODULE.TranslationContractError, "缺少段落标记"):
            MODULE.parse_marked_translation("只翻译此段", source, 1, 2)
        with self.assertRaisesRegex(MODULE.TranslationContractError, "段号不完整或乱序"):
            MODULE.parse_marked_translation("<<<SEG:000001>>>\n译文", source, 1, 2)
        with self.assertRaisesRegex(MODULE.TranslationContractError, "回显"):
            MODULE.parse_marked_translation(
                "<<<SEG:000001>>>\n只翻译此段\n<<<SEG:000002>>>\n正常译文", source, 1, 2
            )

    def test_marked_contract_rejects_repeated_long_translation_for_different_sources(self) -> None:
        source = ["The first source sentence is deliberately long and distinct.",
                  "The second source sentence is also long but clearly different."]
        repeated = "这是一段被错误重复分配给两个不同源段的完整长译文，程序必须拒绝保存它。" * 2
        content = f"<<<SEG:000001>>>\n{repeated}\n<<<SEG:000002>>>\n{repeated}"
        with self.assertRaisesRegex(MODULE.TranslationContractError, "重复分配"):
            MODULE.parse_marked_translation(content, source, 1, 2, "简体中文")

    def test_legacy_state_is_not_reused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            auth = self.make_task(root)
            output = root / "译文"
            output.mkdir()
            (output / ".hy-direct-state.json").write_text(
                json.dumps({"schema_version": 1, "source_hash": "old", "chunks": []}), encoding="utf-8"
            )
            with self.assertRaisesRegex(ValueError, "旧版、未逐段验证"):
                MODULE.run(self.args(root, auth))

    def test_epub_is_coalesced_but_pause_publication_rebuilds_saved_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            auth = self.make_task(root)
            source = root / '原文/sample.txt'
            source.write_text(
                '\n\n'.join(f'Paragraph {number}: ' + ('story text ' * 22)
                             for number in range(1, 13)),
                encoding='utf-8')
            config_path = root / '翻译任务.yaml'
            config = yaml.safe_load(config_path.read_text(encoding='utf-8'))
            config['chunking']['max_segments'] = 1
            config_path.write_text(
                yaml.safe_dump(config, allow_unicode=True, sort_keys=False),
                encoding='utf-8')
            # Presence of this trusted task marker makes rebuild_outputs publish
            # an EPUB. The exporter itself is mocked: this test measures
            # scheduling and proves the pause flush performs no API request.
            (root / '.epub-source.json').write_text('{}', encoding='utf-8')
            with patch('epub_translation.export') as exporter, \
                    contextlib.redirect_stdout(io.StringIO()):
                result, success = MODULE.run(self.args(root, auth))
                request_count = len(HyHandler.requests)
                published = MODULE.publish_saved_outputs(root)
            self.assertTrue(success, result)
            self.assertEqual(result['status'], 'completed')
            self.assertGreaterEqual(request_count, 12)
            self.assertLess(exporter.call_count, request_count,
                            'whole EPUB must not be rebuilt for every saved chunk')
            self.assertEqual(len(HyHandler.requests), request_count,
                             'publishing a saved checkpoint must never call the model')
            self.assertTrue(Path(published['visible']).is_file())


if __name__ == "__main__":
    unittest.main()
