from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import signal
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import yaml


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "native_translate_range", ROOT / "runtime/tools/translate_range.py"
)
assert SPEC and SPEC.loader
TR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TR)

SPEC_TASK = importlib.util.spec_from_file_location(
    "native_task_config", ROOT / "runtime/lib/task_config.py"
)
assert SPEC_TASK and SPEC_TASK.loader
TC = importlib.util.module_from_spec(SPEC_TASK)
SPEC_TASK.loader.exec_module(TC)


class NativeTranslatorTests(unittest.TestCase):
    defPara = "A distinct paragraph about Mara and the harbor, long enough for validation. " * 4

    def make_task(self, root: Path, count: int = 3, max_chars: int = 500) -> Path:
        source_dir = root / "原文"
        source_dir.mkdir(parents=True)
        source = source_dir / "story.txt"
        source.write_text(
            "\n\n".join(f"Paragraph {number}. {self.TemperatureText(number)}" for number in range(1, count + 1)),
            encoding="utf-8",
        )
        config = {
            "schema_version": 1,
            "task_name": "原生提示测试",
            "languages": {"source": "英语", "target": "简体中文"},
            "source": {"files": ["原文/story.txt"], "encoding": "UTF-8"},
            "output": {"directory": "译文", "filename_rule": "原文件名.zh-CN.txt", "overwrite_existing": False},
            "translation": {},
            "context": {"background": "", "character_voice": []},
            "chunking": {
                "segment_mode": "blank_line",
                "max_source_chars": max_chars,
                "max_segments": 20,
                "context_segments": 2,
                "parallel_requests": 1,
            },
            "progress": {"file": "翻译进度.json"},
            "glossary": {"mode": "none", "file": "术语表.json"},
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

    @classmethod
    def TemperatureText(cls, number: int) -> str:  # noqa: N802 - visibly unique in failures
        return (f"Mara sees object {number} beside the quiet northern quay. " * 5).strip()

    @staticmethod
    def args(root: Path, auth: Path) -> argparse.Namespace:
        return argparse.Namespace(
            task_root=str(root),
            start_segment=1,
            end_segment=0,
            context_segments=2,
            api_base="https://unused.invalid/v1",
            model="mock-hy",
            auth_path=str(auth),
            request_timeout=10,
            prompt_mode="native",
        )

    def test_plain_parser_accepts_only_exact_paragraph_mapping(self) -> None:
        source = ["First long source paragraph. " * 5, "Second different source paragraph. " * 5]
        parsed = TR.parse_plain_translation(
            "第一段译文内容自然完整。\n\n第二段译文内容同样完整。", source, 1, 2, "简体中文"
        )
        self.assertEqual([item["id"] for item in parsed], [1, 2])
        with self.assertRaisesRegex(TR.TranslationContractError, "段落数不匹配"):
            TR.parse_plain_translation("两段被错误合并成一段。", source, 1, 2, "简体中文")

    def test_native_mode_is_sequential_and_carries_adopted_translation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            auth = self.make_task(root, count=3, max_chars=500)
            prompts: list[str] = []
            call_number = 0

            def fake_translate(_url, _model, _key, prompt, _timeout):
                nonlocal call_number
                call_number += 1
                prompts.append(prompt)
                self.assertNotIn("<<<SEG:", prompt)
                return f"这是第{call_number}块采用的完整中文译文，内容各不相同且可以安全保存。", 0.1

            with patch.object(TR, "translate_once", side_effect=fake_translate), contextlib.redirect_stdout(io.StringIO()):
                result, success = TR.run(self.args(root, auth))

            self.assertTrue(success, result)
            self.assertEqual(result["status"], "completed")
            self.assertEqual(len(prompts), 3)
            self.assertNotIn("【上文原文与已采用译文】", prompts[0])
            self.assertIn("【上文原文与已采用译文】", prompts[1])
            self.assertIn("第1块采用的完整中文译文", prompts[1])
            progress = json.loads((root / "翻译进度.json").read_text(encoding="utf-8"))
            self.assertEqual(progress["percent"], 100.0)
            self.assertEqual(progress["completed_source_chars"], progress["total_source_chars"])

    def test_native_plain_failure_escalates_once_to_documented_markers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            auth = self.make_task(root, count=2, max_chars=6000)
            prompts: list[str] = []

            def fake_translate(_url, _model, _key, prompt, _timeout):
                prompts.append(prompt)
                if "<<<SEG:" not in prompt:
                    return "模型把两段错误合并了，因此程序必须升级输出协议。", 0.1
                numbers = TR.MARKER_PATTERN.findall(prompt)
                return "\n".join(
                    f"<<<SEG:{number}>>>\n这是段落 {number} 的独立中文译文，内容完整且不同。"
                    for number in numbers
                ), 0.1

            with patch.object(TR, "translate_once", side_effect=fake_translate), contextlib.redirect_stdout(io.StringIO()):
                result, success = TR.run(self.args(root, auth))

            self.assertTrue(success, result)
            self.assertEqual(len(prompts), 2)
            self.assertNotIn("<<<SEG:", prompts[0])
            self.assertIn("<<<SEG:000001>>>", prompts[1])

    def test_bisection_persists_left_branch_before_translating_right(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            auth = self.make_task(root, count=4, max_chars=6000)
            call_number = 0

            def fake_translate(_url, _model, _key, _prompt, _timeout):
                nonlocal call_number
                call_number += 1
                if call_number == 1:
                    return "四段被错误合并为一段，所以需要结构重试。", 0.1
                if call_number == 2:
                    return "结构重试也没有返回段号。", 0.1
                if call_number == 3:
                    return (
                        "这是第一段已经验收的译文，内容完整自然且不会与其他段落重复。\n\n"
                        "这是第二段已经验收的译文，内容同样完整并保留段落边界。"
                    ), 0.1
                if call_number == 4:
                    progress = json.loads((root / "翻译进度.json").read_text(encoding="utf-8"))
                    self.assertEqual(progress["completed_segments"], 2)
                    self.assertEqual(progress["next_segment"], 3)
                    return (
                        "这是第三段随后完成的译文，程序在此之前已经保存了左侧分支。\n\n"
                        "这是第四段最后完成的译文，整个任务现在可以正常收尾。"
                    ), 0.1
                self.fail(f"unexpected translation call {call_number}")

            events = io.StringIO()
            with patch.object(TR, "translate_once", side_effect=fake_translate), contextlib.redirect_stdout(events):
                result, success = TR.run(self.args(root, auth))

            self.assertTrue(success, result)
            self.assertEqual(call_number, 4)
            payloads = [json.loads(line) for line in events.getvalue().splitlines()]
            self.assertTrue(any(item.get("phase") == "split" for item in payloads))
            self.assertEqual(sum(item.get("type") == "progress" for item in payloads), 2)

    def test_epub_import_follows_spine_and_discards_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            epub = Path(raw) / "sample.epub"
            with zipfile.ZipFile(epub, "w") as archive:
                archive.writestr(
                    "META-INF/container.xml",
                    '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OPS/book.opf"/></rootfiles></container>',
                )
                archive.writestr(
                    "OPS/book.opf",
                    '<package xmlns="http://www.idpf.org/2007/opf"><manifest><item id="c1" href="one.xhtml" media-type="application/xhtml+xml"/><item id="c2" href="two.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="c2"/><itemref idref="c1"/></spine></package>',
                )
                archive.writestr("OPS/one.xhtml", "<html><body><h1>First</h1><p>Opening prose.</p></body></html>")
                archive.writestr("OPS/two.xhtml", "<html><body><nav>Skip me</nav><h1>Second</h1><p>Earlier in spine.</p></body></html>")
            text = TC.extract_source_text(epub)
            self.assertLess(text.index("Second"), text.index("First"))
            self.assertNotIn("Skip me", text)

    def test_parent_watchdog_stops_an_orphaned_worker(self) -> None:
        module_path = str(ROOT / "runtime/tools/translate_range.py")
        script = (
            "import importlib.util, time\n"
            f"spec = importlib.util.spec_from_file_location('worker', {module_path!r})\n"
            "worker = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(worker)\n"
            "worker.start_parent_watchdog(99999999)\n"
            "time.sleep(5)\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            check=False,
            capture_output=True,
            text=True,
            timeout=4,
        )
        self.assertEqual(completed.returncode, -signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
