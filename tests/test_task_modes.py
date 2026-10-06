import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime/lib"))
import task_config as tc
import glossary as gl
spec = importlib.util.spec_from_file_location("mode_runner", ROOT / "runtime/tools/translate_range.py")
tr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tr)


class TaskModeTests(unittest.TestCase):
    def test_prepare_repairs_legacy_rtf_without_changing_root(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "draft.rtf"
            original = r"{\rtf1\ansi A captain.\par A harbor.}"
            source.write_text(original)
            legacy = {"schema_version": "2", "languages": {"source": "English", "target": "Chinese"},
                      "source": {"files": [{"path": "draft.rtf", "name": "draft.rtf"}], "encoding": "ANSI-CP936"}}
            (root / "翻译任务.yaml").write_text(yaml.safe_dump(legacy))
            result = tc.prepare(root, {})
            config = yaml.safe_load((root / "翻译任务.yaml").read_text())
            self.assertEqual(result["task_root"], str(root.resolve()))
            self.assertEqual(config["languages"]["target"], "简体中文")
            self.assertEqual(config["schema_version"], 1)
            self.assertEqual(config["source"]["encoding"], "UTF-8")
            identity = config["source"]["identity"]
            self.assertEqual(identity["schema"], "sha256-bytes-v1")
            self.assertEqual(len(identity["sha256"]), 64)
            self.assertEqual(len(identity["text_sha256"]), 64)
            self.assertEqual(result["book_hash"], identity["sha256"])
            self.assertTrue((root / '.whaleread-identity.json').is_file())
            text = (root / config["source"]["files"][0]).read_text()
            self.assertIn("A captain.\nA harbor.", text)
            self.assertNotIn("\\rtf", text)
            self.assertEqual(source.read_text(), original)
            self.assertEqual(tc.validate(root, config), config)
            (root / config["source"]["files"][0]).write_text(text + '\nchanged')
            with self.assertRaisesRegex(ValueError, "内容身份"):
                tc.validate(root, config)
            (root / config["source"]["files"][0]).write_text(text)
            state = root / "译文/.hy-direct-state.json"
            state.parent.mkdir(); state.write_text(json.dumps({"chunks": [{"start": 1}]}))
            with self.assertRaisesRegex(ValueError, "已有译文"):
                tc.prepare(root, {})

    def test_implicit_direction_detects_document_and_forces_simplified_chinese(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "draft.txt").write_text("The harbor was silent. " * 20, encoding="utf-8")
            # A small-model guess cannot override the default unless it marks
            # the target as explicitly requested by the user.
            tc.prepare(root, {
                "source_files": ["draft.txt"],
                "source_language": "zh",
                "target_language": "en",
            })
            config = yaml.safe_load((root / "翻译任务.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["languages"], {"source": "英语", "target": "简体中文"})
            self.assertEqual(config["output"]["filename_rule"], "原文件名.zh-CN.txt")

    def test_explicit_target_is_respected_and_sets_matching_suffix(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "draft.txt").write_text("港口一片寂静。海风吹过城墙。" * 10, encoding="utf-8")
            tc.prepare(root, {
                "source_files": ["draft.txt"],
                "target_language": "en",
                "target_language_explicit": True,
            })
            config = yaml.safe_load((root / "翻译任务.yaml").read_text(encoding="utf-8"))
            self.assertEqual(config["languages"], {"source": "中文", "target": "英语"})
            self.assertEqual(config["output"]["filename_rule"], "原文件名.en.txt")

    def test_schema_errors_and_duplicate_sources_fail_before_api(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "a.txt").write_text("Hello")
            cfg = {"schema_version": 1, "languages": {"source": "en", "target": "zh-CN"},
                   "source": {"files": [{"path": "a.txt"}]}}
            with self.assertRaisesRegex(ValueError, "字符串列表"):
                tc.validate(root, cfg)
            cfg["source"]["files"] = ["a.txt", "a.txt"]
            with self.assertRaisesRegex(ValueError, "重复"):
                tc.validate(root, cfg)

    def test_none_does_not_read_or_scan_glossary(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "术语表.json").write_text("invalid file")
            def forbidden(_):
                self.fail("none mode called Hy")
            self.assertEqual(gl.prepare_glossary(root, {"glossary": {"mode": "none"}}, ["text"], "hash", forbidden, lambda _: None), [])

    def test_fixed_tsv_and_matching_only_current_terms(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "terms.tsv"
            path.write_text("source\ttarget\tnote\nAsh\t阿什\t人名\nNorth Harbor\t北港\t港口\n")
            entries = tc.read_glossary(path)
            self.assertEqual(len(gl.selected(entries, "Ash visited North Harbor.")), 2)
            self.assertEqual(gl.selected(entries, "A flash."), [])
            cfg = {"languages": {"source": "en", "target": "zh-CN"}, "_glossary_entries": entries}
            prompt = tr.build_prompt(cfg, ["Ash returned."], {"start": 1, "end": 1, "text": "Ash returned."}, 0)
            self.assertIn("规范简体中文", prompt)
            self.assertIn("阿什", prompt)
            self.assertNotIn("北港", prompt)

    def test_auto_resumes_scan_and_locks_conflicts_deterministically(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cfg = {"languages": {"source": "en", "target": "简体中文"}, "glossary": {"mode": "auto"}}
            segments = ["Ash watches. " * 300, "Ash sleeps. " * 300]
            calls = []
            def request(prompt):
                calls.append(prompt)
                if len(calls) == 2:
                    raise TimeoutError("network outage")
                return json.dumps([{"source": "Ash", "target": "阿什", "note": "人名"}]), 1
            with self.assertRaises(TimeoutError):
                gl.prepare_glossary(root, cfg, segments, "same", request, lambda _: None)
            self.assertTrue((root / ".hy-glossary-scan.json").exists())
            resumed = []
            def retry(prompt):
                resumed.append(prompt)
                return json.dumps([{"source": "Ash", "target": "艾什", "note": "人名"}]), 1
            entries = gl.prepare_glossary(root, cfg, segments, "same", retry, lambda _: None)
            self.assertEqual(len(resumed), 1)
            self.assertEqual(entries[0]["target"], "阿什")
            locked = json.loads((root / "术语表.auto.json").read_text())
            self.assertEqual(len(locked["conflicts"]), 1)
            self.assertEqual(locked["status"], "locked")
            gl.prepare_glossary(root, cfg, segments, "same", lambda _: self.fail("repeated scan"), lambda _: None)
            with self.assertRaisesRegex(ValueError, "已改变"):
                gl.prepare_glossary(root, cfg, segments, "changed", retry, lambda _: None)

    def test_invalid_auto_output_is_not_accepted(self):
        with self.assertRaises(ValueError):
            gl.parse_candidates('[{"source":"Invented","target":"虚构"}]', "Ash returned.")
        with self.assertRaises(ValueError):
            gl.parse_candidates("这是一段译文", "Ash returned.")

    def test_task_lock_prevents_duplicate_process_work(self):
        with tempfile.TemporaryDirectory() as raw:
            with tc.task_lock(Path(raw)):
                with self.assertRaisesRegex(ValueError, "正在执行"):
                    with tc.task_lock(Path(raw)):
                        self.fail("second lock acquired")

    def test_inline_marker_normalization_keeps_mapping_checks(self):
        source = ["A sailor.", "A harbor."]
        result = tr.parse_marked_translation('<<<SEG:000001>>> 水手。\n<<<SEG:000002>>> 港口。', source, 1, 2)
        self.assertEqual([row['text'] for row in result], ['水手。', '港口。'])
        result = tr.parse_marked_translation('<<<SEG:000001>>>水手。\n<<<SEG:000002>>>港口。', source, 1, 2)
        self.assertEqual([row['text'] for row in result], ['水手。', '港口。'])
        for malformed in ['<<<SEG:000001>>> 水手。', '<<<SEG:000002>>> 港口。\n<<<SEG:000001>>> 水手。', '<<<SEG:000001>>> 水手。\n<<<SEG:000001>>> 港口。']:
            with self.assertRaises(tr.TranslationContractError):
                tr.parse_marked_translation(malformed, source, 1, 2)

    def test_chinese_target_rejects_english_source_echo(self):
        source = ["The harbor was silent and the wind crossed the old stone wall. " * 2]
        echoed = "<<<SEG:000001>>>\n" + source[0]
        with self.assertRaisesRegex(tr.TranslationContractError, "简体中文语言门禁|原样回传"):
            tr.parse_marked_translation(echoed, source, 1, 1, "简体中文")


if __name__ == "__main__":
    unittest.main()
