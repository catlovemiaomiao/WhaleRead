from __future__ import annotations

import argparse
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime/lib"))
import task_config as TC  # noqa: E402

SPEC = importlib.util.spec_from_file_location(
    "language_range_runner", ROOT / "runtime/tools/translate_range.py")
assert SPEC and SPEC.loader
TR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(TR)

ENGLISH = ("The harbor was silent, and the wind crossed the old stone wall. "
           "She had not seen him since the winter.\n\n") * 8
FRENCH = ("Le port était silencieux, et le vent traversait le vieux mur de pierre. "
          "Elle ne l'avait pas vu depuis l'hiver.\n\n") * 8
GERMAN = ("Der Hafen war still, und der Wind strich über die alte Steinmauer. "
          "Sie hatte ihn seit dem Winter nicht gesehen.\n\n") * 8
CHINESE = "港口一片寂静，海风吹过古老的石墙。她自冬天以后就没有再见过他。\n\n" * 8
THAI = "ท่าเรือเงียบสงบและลมพัดผ่านกำแพงหินเก่า เธอไม่ได้พบเขาตั้งแต่ฤดูหนาว\n\n" * 8
# Names and nouns only: every Latin candidate shares these function words, so
# no honest detector may call this English.
AMBIGUOUS = "Mara arrived. Iven waited. Letter came. North harbor. Silver register.\n\n" * 8


class DetectionTests(unittest.TestCase):
    def test_english_is_detected_instead_of_any_latin_default(self):
        result = TC.detect_language(ENGLISH)
        self.assertFalse(result["uncertain"])
        self.assertEqual(result["language"], "英语")
        self.assertEqual(result["script"], "latin")
        self.assertEqual(TC.detect_source_language(ENGLISH), "英语")

    def test_another_latin_language_is_recognised(self):
        for text, expected in ((FRENCH, "法语"), (GERMAN, "德语")):
            result = TC.detect_language(text)
            self.assertFalse(result["uncertain"], result)
            self.assertEqual(result["language"], expected)

    def test_ambiguous_latin_is_uncertain_not_english(self):
        result = TC.detect_language(AMBIGUOUS)
        self.assertTrue(result["uncertain"])
        self.assertEqual(result["language"], "")
        self.assertNotIn("英语", result["candidates"][:1])
        self.assertIn("手动", result["detail"])
        with self.assertRaises(TC.LanguageDetectionError) as caught:
            TC.detect_source_language(AMBIGUOUS)
        self.assertTrue(caught.exception.candidates)

    def test_non_latin_script_is_named_or_uncertain(self):
        self.assertEqual(TC.detect_language(CHINESE)["language"], "中文")
        thai = TC.detect_language(THAI)
        self.assertIn(thai["language"] or "泰语", ("泰语",))

    def test_short_latin_text_is_not_forced_to_a_language(self):
        result = TC.detect_language("The harbor was silent.")
        self.assertTrue(result["uncertain"])


class PrepareDirectionTests(unittest.TestCase):
    def prepare(self, root: Path, text: str, spec: dict) -> dict:
        (root / "draft.txt").write_text(text, encoding="utf-8")
        merged = {"source_files": ["draft.txt"], **spec}
        TC.prepare(root, merged)
        return yaml.safe_load((root / "翻译任务.yaml").read_text(encoding="utf-8"))

    def test_auto_direction_writes_detected_source_and_simplified_chinese(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.prepare(root, ENGLISH, {
                "source_language": "", "source_language_explicit": False,
                "target_language": "简体中文", "target_language_explicit": True,
            })
            self.assertEqual(config["languages"], {"source": "英语", "target": "简体中文"})
            self.assertEqual(config["output"]["filename_rule"], "原文件名.zh-CN.txt")
            self.assertEqual(TC.validate(root, config), config)

    def test_manual_french_to_english_is_not_silently_reverted(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.prepare(root, FRENCH, {
                "source_language": "法语", "source_language_explicit": True,
                "target_language": "英语", "target_language_explicit": True,
            })
            self.assertEqual(config["languages"], {"source": "法语", "target": "英语"})
            self.assertEqual(config["output"]["filename_rule"], "原文件名.en.txt")
            self.assertEqual(TC.validate(root, config), config)

    def test_manual_source_resolves_undetectable_text(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "draft.txt").write_text(AMBIGUOUS, encoding="utf-8")
            with self.assertRaises(TC.LanguageDetectionError):
                TC.prepare(root, {"source_files": ["draft.txt"], "source_language": "",
                                  "source_language_explicit": False,
                                  "target_language": "简体中文",
                                  "target_language_explicit": True})
            config = yaml.safe_load((root / "翻译任务.yaml").read_text(encoding="utf-8")) \
                if (root / "翻译任务.yaml").exists() else None
            self.assertIsNone(config)
            result = TC.prepare(root, {"source_files": ["draft.txt"], "source_language": "法语",
                                       "source_language_explicit": True,
                                       "target_language": "简体中文",
                                       "target_language_explicit": True})
            self.assertEqual(result["source_language"], "法语")

    def test_manual_choice_flags_obvious_script_conflict(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "draft.txt").write_text(CHINESE, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "语言方向冲突"):
                TC.prepare(root, {"source_files": ["draft.txt"], "source_language": "英语",
                                  "source_language_explicit": True,
                                  "target_language": "简体中文",
                                  "target_language_explicit": True})

    def test_same_script_manual_choice_is_respected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "draft.txt").write_text(ENGLISH, encoding="utf-8")
            result = TC.prepare(root, {"source_files": ["draft.txt"], "source_language": "法语",
                                       "source_language_explicit": True,
                                       "target_language": "简体中文",
                                       "target_language_explicit": True})
            self.assertEqual(result["source_language"], "法语")

    def test_non_chinese_target_names_are_consistent_and_distinct(self):
        with tempfile.TemporaryDirectory() as raw:
            rules = {}
            for target in ("英语", "法语", "斯瓦希里语"):
                root = Path(raw) / target
                root.mkdir()
                config = self.prepare(root, ENGLISH, {
                    "source_language": "", "source_language_explicit": False,
                    "target_language": target, "target_language_explicit": True,
                })
                self.assertEqual(TC.validate(root, config), config)
                rules[target] = config["output"]["filename_rule"]
            self.assertEqual(rules["英语"], "原文件名.en.txt")
            self.assertEqual(rules["法语"], "原文件名.fr.txt")
            self.assertNotEqual(rules["英语"], rules["法语"])
            self.assertNotEqual(rules["法语"], rules["斯瓦希里语"])
            self.assertNotEqual(rules["英语"], rules["斯瓦希里语"])

    def test_custom_targets_with_the_same_prefix_get_distinct_outputs(self):
        first = TC.output_suffix("TransliterationLanguageA")
        second = TC.output_suffix("TransliterationLanguageB")
        self.assertNotEqual(first, second)
        self.assertEqual(first, TC.output_suffix("TransliterationLanguageA"))
        self.assertEqual(TC.epub_language_tag("斯瓦希里语"), "und")
        self.assertEqual(TC.epub_language_tag("法语"), "fr")


class ValidateDirectionTests(unittest.TestCase):
    def legacy(self, root: Path, text: str, source: str = "英语",
               target: str = "简体中文", rule: str = "原文件名.zh-CN.txt") -> dict:
        source_dir = root / "原文"
        source_dir.mkdir(exist_ok=True)
        (source_dir / "legacy.txt").write_text(text, encoding="utf-8")
        return {"schema_version": 1, "languages": {"source": source, "target": target},
                "source": {"files": ["原文/legacy.txt"], "encoding": "UTF-8"},
                "output": {"directory": "译文", "filename_rule": rule},
                "progress": {"file": "翻译进度.json"}}

    def test_legacy_tasks_still_validate(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.legacy(root, ENGLISH)
            self.assertEqual(TC.validate(root, config), config)
            plain_rule = self.legacy(root, ENGLISH, rule="原文件名.txt")
            self.assertEqual(TC.validate(root, plain_rule), plain_rule)
            manual = self.legacy(root, FRENCH, source="法语")
            self.assertEqual(TC.validate(root, manual), manual)

    def test_suffix_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.legacy(root, ENGLISH, target="英语")
            with self.assertRaisesRegex(ValueError, "zh-CN"):
                TC.validate(root, config)

    def test_auto_marker_must_not_reach_the_task_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.legacy(root, ENGLISH, source=TC.AUTO_LANGUAGE)
            with self.assertRaisesRegex(ValueError, "自动识别"):
                TC.validate(root, config)

    def test_obvious_script_conflict_is_reported_on_resume(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = self.legacy(root, CHINESE, source="英语")
            with self.assertRaisesRegex(ValueError, "语言方向冲突"):
                TC.validate(root, config)

    def test_checkpoint_blocks_in_place_direction_change(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self.prepare_task(root)
            state = root / "译文/.hy-direct-state.json"
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(json.dumps({"chunks": [{"start": 1, "end": 1}]}), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "已有译文"):
                TC.prepare(root, {"source_files": ["draft.txt"], "source_language": "英语",
                                  "source_language_explicit": True,
                                  "target_language": "英语",
                                  "target_language_explicit": True})

    def prepare_task(self, root: Path) -> None:
        (root / "draft.txt").write_text(ENGLISH, encoding="utf-8")
        TC.prepare(root, {"source_files": ["draft.txt"], "source_language": "英语",
                          "source_language_explicit": True, "target_language": "简体中文",
                          "target_language_explicit": True})


class NonChineseTargetEngineTests(unittest.TestCase):
    """The real worker path with a mocked model: never a translation API call."""

    def run_task(self, target: str, translated_paragraphs: list[str]):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            segments = [
                "The harbor was silent and the wind crossed the old stone wall, long enough to validate.",
                "She had not seen him since the winter, and the letter waited on the table, unopened.",
            ]
            (root / "draft.txt").write_text("\n\n".join(segments), encoding="utf-8")
            TC.prepare(root, {
                "source_files": ["draft.txt"], "source_language": "英语",
                "source_language_explicit": True, "target_language": target,
                "target_language_explicit": True, "native_defaults": True,
            })
            auth = root / "auth.json"
            auth.write_text(json.dumps({"dgx-spark-translate": {"type": "api", "key": "test"}}),
                            encoding="utf-8")
            args = argparse.Namespace(
                task_root=str(root), start_segment=1, end_segment=0, context_segments=2,
                api_base="http://unused.invalid/v1", model="mock-hy", auth_path=str(auth),
                request_timeout=10, prompt_mode="native")
            prompts: list[str] = []

            def fake_translate(_url, _model, _key, prompt, _timeout):
                prompts.append(prompt)
                return "\n\n".join(translated_paragraphs), 0.1

            with patch.object(TR, "translate_once", side_effect=fake_translate), \
                    contextlib.redirect_stdout(io.StringIO()):
                result, success = TR.run(args)
            output_name = Path(result["output_file"]).name if result.get("output_file") else ""
            output_text = (Path(result["output_file"]).read_text(encoding="utf-8")
                           if result.get("output_file") else "")
            return result, success, prompts, output_name, output_text

    def test_english_target_writes_en_output_and_prompt(self):
        result, success, prompts, output_name, output_text = self.run_task(
            "英语", ["The port was quiet and the wind crossed the old wall.",
                     "She had not seen him since winter, and the letter waited."])
        self.assertTrue(success, result)
        self.assertTrue(output_name.endswith(".en.txt"), output_name)
        self.assertNotIn("zh-CN", output_name)
        self.assertIn("翻译为英语", prompts[0])
        self.assertTrue(output_text.strip())

    def test_french_target_writes_fr_output_and_keeps_chinese_rule_out(self):
        result, success, prompts, output_name, _ = self.run_task(
            "法语", ["Le port était calme et le vent traversait le vieux mur.",
                     "Elle ne l'avait pas vu depuis l'hiver, et la lettre attendait."])
        self.assertTrue(success, result)
        self.assertTrue(output_name.endswith(".fr.txt"), output_name)
        self.assertIn("翻译为法语", prompts[0])
        self.assertNotIn("规范简体中文", prompts[0])


if __name__ == "__main__":
    unittest.main()
