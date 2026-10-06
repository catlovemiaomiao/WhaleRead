"""Task 01: stable semantic identities for languages, review categories and
review errors, independent of any user-visible label.

Pure Python on purpose: no Qt, no engine, no model request.  Every fixture is a
temporary directory, and the frozen tables below are the historical contracts
this change must not break.
"""
from __future__ import annotations

import json
import random
import sys
import tempfile
import unicodedata
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "runtime/lib"))
import annotations as notes  # noqa: E402
import language_catalog as catalog  # noqa: E402
import task_config as TC  # noqa: E402

ENGLISH = ("The harbor was silent, and the wind crossed the old stone wall. "
           "She had not seen him since the winter.\n\n") * 8

# The tables exactly as released in 1.17.1: existing task files, output names
# and stored per-book selections must keep matching them.
LEGACY_COMMON_LANGUAGES = (
    "简体中文", "繁体中文", "英语", "日语", "韩语",
    "法语", "德语", "西班牙语", "葡萄牙语", "意大利语", "俄语",
    "阿拉伯语", "荷兰语", "波兰语", "土耳其语", "越南语", "泰语", "印地语",
)
LEGACY_OUTPUT_SUFFIXES = {
    "简体中文": "zh-CN", "繁体中文": "zh-TW", "英语": "en", "日语": "ja", "韩语": "ko",
    "法语": "fr", "德语": "de", "西班牙语": "es", "葡萄牙语": "pt", "意大利语": "it",
    "俄语": "ru", "乌克兰语": "uk", "阿拉伯语": "ar", "荷兰语": "nl", "波兰语": "pl",
    "土耳其语": "tr", "越南语": "vi", "泰语": "th", "印地语": "hi", "希腊语": "el",
    "希伯来语": "he",
}
LEGACY_ALIASES = {
    "zh": "简体中文", "zh-cn": "简体中文", "zh-hans": "简体中文", "zh-sg": "简体中文",
    "chinese": "简体中文", "中文": "简体中文", "简体": "简体中文", "简体中文": "简体中文",
    "zh-tw": "繁体中文", "zh-hk": "繁体中文", "zh-mo": "繁体中文", "zh-hant": "繁体中文",
    "chinese-traditional": "繁体中文", "繁体": "繁体中文", "繁体中文": "繁体中文",
    "正体中文": "繁体中文",
    "en": "英语", "en-us": "英语", "en-gb": "英语", "english": "英语", "英文": "英语", "英语": "英语",
    "ja": "日语", "jp": "日语", "japanese": "日语", "日文": "日语", "日语": "日语",
    "ko": "韩语", "korean": "韩语", "韩文": "韩语", "朝鲜语": "韩语", "韩语": "韩语",
    "fr": "法语", "fr-fr": "法语", "french": "法语", "法文": "法语", "法语": "法语",
    "de": "德语", "german": "德语", "德文": "德语", "德语": "德语",
    "es": "西班牙语", "spanish": "西班牙语", "español": "西班牙语", "西班牙文": "西班牙语",
    "西班牙语": "西班牙语",
    "pt": "葡萄牙语", "portuguese": "葡萄牙语", "葡萄牙文": "葡萄牙语", "葡萄牙语": "葡萄牙语",
    "it": "意大利语", "italian": "意大利语", "意大利文": "意大利语", "意大利语": "意大利语",
    "ru": "俄语", "russian": "俄语", "俄文": "俄语", "俄语": "俄语",
    "uk": "乌克兰语", "ukrainian": "乌克兰语", "乌克兰文": "乌克兰语", "乌克兰语": "乌克兰语",
    "ar": "阿拉伯语", "arabic": "阿拉伯语", "阿拉伯文": "阿拉伯语", "阿拉伯语": "阿拉伯语",
    "nl": "荷兰语", "dutch": "荷兰语", "荷兰文": "荷兰语", "荷兰语": "荷兰语",
    "pl": "波兰语", "polish": "波兰语", "波兰文": "波兰语", "波兰语": "波兰语",
    "tr": "土耳其语", "turkish": "土耳其语", "土耳其文": "土耳其语", "土耳其语": "土耳其语",
    "vi": "越南语", "vietnamese": "越南语", "越南文": "越南语", "越南语": "越南语",
    "th": "泰语", "thai": "泰语", "泰文": "泰语", "泰语": "泰语",
    "hi": "印地语", "hindi": "印地语", "印地文": "印地语", "印地语": "印地语",
    "el": "希腊语", "greek": "希腊语", "希腊文": "希腊语", "希腊语": "希腊语",
    "he": "希伯来语", "hebrew": "希伯来语", "希伯来文": "希伯来语", "希伯来语": "希伯来语",
}

NATIVE_NAMES = (
    "简体中文", "繁體中文", "English", "日本語", "한국어", "Français", "Deutsch",
    "Español", "Português", "Italiano", "Русский", "العربية", "Nederlands",
    "Polski", "Türkçe", "Tiếng Việt", "ไทย", "हिन्दी",
)


def snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes()
            for path in sorted(root.rglob("*")) if path.is_file()}


class LanguageCatalogTests(unittest.TestCase):
    def test_historical_task_values_and_suffixes_are_frozen(self):
        self.assertEqual(tuple(LEGACY_COMMON_LANGUAGES), TC.COMMON_LANGUAGES)
        self.assertEqual(LEGACY_OUTPUT_SUFFIXES, TC.OUTPUT_SUFFIXES)
        for legacy, suffix in LEGACY_OUTPUT_SUFFIXES.items():
            self.assertEqual(legacy, TC.language(legacy))
            self.assertEqual(suffix, TC.output_suffix(legacy))

    def test_every_historical_alias_still_resolves(self):
        for alias, legacy in LEGACY_ALIASES.items():
            self.assertEqual(legacy, TC.language(alias), alias)
            self.assertEqual(legacy, catalog.normalize(alias), alias)

    def test_every_common_language_has_an_id_and_a_native_name(self):
        entries = list(catalog.common_languages())
        self.assertEqual(tuple(LEGACY_COMMON_LANGUAGES),
                         tuple(entry.legacy_value for entry in entries))
        self.assertEqual(tuple(NATIVE_NAMES), tuple(entry.native_name for entry in entries))
        self.assertEqual(len({entry.id for entry in entries}), len(entries))
        self.assertEqual(len({entry.legacy_value for entry in entries}), len(entries))
        for entry in entries:
            self.assertEqual(entry.legacy_value, TC.language(entry.native_name))
            self.assertEqual(entry.legacy_value, TC.language(entry.id))
            self.assertEqual(entry.output_suffix, TC.output_suffix(entry.native_name))

    def test_japanese_native_name_carries_every_identity(self):
        self.assertEqual("日语", TC.language("日本語"))
        self.assertEqual("ja", catalog.language_id("日本語"))
        self.assertEqual("ja", TC.output_suffix("日本語"))
        self.assertEqual("ja", TC.epub_language_tag("日本語"))
        self.assertEqual("japanese", TC.language_family("日本語"))
        self.assertEqual(("han", "kana"), TC.language_scripts("日本語"))

    def test_french_native_name_carries_every_identity(self):
        self.assertEqual("法语", TC.language("Français"))
        self.assertEqual("fr", catalog.language_id("Français"))
        self.assertEqual("fr", TC.output_suffix("Français"))
        self.assertEqual("latin", TC.language_family("Français"))

    def test_manual_only_aliases_are_not_lost_by_leaving_the_picker(self):
        common = set(TC.COMMON_LANGUAGES)
        for raw, legacy in (("uk", "乌克兰语"), ("Ukrainian", "乌克兰语"),
                            ("el", "希腊语"), ("Greek", "希腊语"),
                            ("he", "希伯来语"), ("Hebrew", "希伯来语")):
            self.assertEqual(legacy, TC.language(raw))
            self.assertNotIn(legacy, common)
            self.assertEqual(legacy, catalog.normalize(raw))
            self.assertEqual(LEGACY_OUTPUT_SUFFIXES[legacy], TC.output_suffix(raw))

    def test_parenthetical_manual_input_still_resolves(self):
        self.assertEqual("法语", TC.language("法语（法国）"))
        self.assertEqual("英语", TC.language("English (US)"))
        self.assertEqual("日语", TC.language("日本語 (ja)"))

    def test_nfc_and_nfd_spellings_resolve_identically(self):
        # Some scripts (Devanagari) are already decomposed, so only the
        # equivalence is asserted, never a spelling difference.
        for native in ("Français", "Español", "Português", "Türkçe", "Tiếng Việt", "हिन्दी"):
            nfc = unicodedata.normalize("NFC", native)
            nfd = unicodedata.normalize("NFD", native)
            self.assertEqual(TC.language(native), TC.language(nfc), native)
            self.assertEqual(TC.language(native), TC.language(nfd), native)
            self.assertEqual(TC.output_suffix(native), TC.output_suffix(nfd), native)
            self.assertEqual(catalog.language_id(native), catalog.language_id(nfd), native)

    def test_nfd_only_spelling_is_accepted_where_the_forms_differ(self):
        native = unicodedata.normalize("NFC", "Français")
        nfd = unicodedata.normalize("NFD", native)
        self.assertNotEqual(native, nfd)
        self.assertEqual("法语", TC.language(nfd))
        self.assertEqual("fr", TC.output_suffix(nfd))
        self.assertEqual("法语", catalog.normalize(nfd))

    def test_unknown_custom_names_are_preserved_verbatim(self):
        for custom in ("斯瓦希里语", "Klingon", "自定义混合语"):
            self.assertEqual(custom, TC.language(custom))
            self.assertEqual("", catalog.language_id(custom))
            self.assertEqual("und", TC.epub_language_tag(custom))
            self.assertEqual((), TC.language_scripts(custom))
            self.assertEqual("custom", catalog.option_value(custom))

    def test_unknown_parenthetical_qualifiers_remain_distinct(self):
        names = ("克林贡语（古典）", "克林贡语（现代）",
                 "Klingon (classic)", "Klingon (modern)")
        for name in names:
            self.assertEqual(name, TC.language(name))
            self.assertEqual("custom", catalog.option_value(name))
        self.assertEqual(len(names), len({TC.output_suffix(name) for name in names}))

    def test_custom_names_that_slug_alike_get_distinct_stable_suffixes(self):
        first = TC.output_suffix("斯瓦希里语")
        second = TC.output_suffix("斯瓦西里语")
        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("x-other-"), first)
        self.assertTrue(second.startswith("x-other-"), second)
        self.assertEqual(first, TC.output_suffix("斯瓦希里语"))

    def test_native_name_and_label_round_trip_through_the_option_model(self):
        for options in (catalog.source_options(), catalog.target_options()):
            for item in options:
                if item["kind"] != "language":
                    continue
                for representation in (item["value"], item["text"], item["legacy"], item["suffix"]):
                    self.assertEqual(item["value"], catalog.option_value(representation),
                                     representation)

    def test_option_models_keep_actions_out_of_the_language_set(self):
        source = catalog.source_options()
        target = catalog.target_options()
        self.assertEqual("auto", source[0]["value"])
        self.assertEqual("action", source[0]["kind"])
        self.assertEqual("custom", source[-1]["value"])
        self.assertEqual("custom", target[-1]["value"])
        self.assertNotIn("auto", [item["value"] for item in target])
        self.assertNotIn("auto", [item["value"] for item in target if item["kind"] == "language"])
        self.assertEqual([entry.id for entry in catalog.common_languages()],
                         [item["value"] for item in target if item["kind"] == "language"])

    def test_manual_only_languages_reopen_through_custom_input(self):
        for raw, stable_id in (("Українська", "uk"), ("Ελληνικά", "el"), ("עברית", "he")):
            self.assertEqual(stable_id, catalog.language_id(raw))
            self.assertEqual("custom", catalog.option_value(raw))

    def test_reordering_or_relabelling_the_model_cannot_change_a_selection(self):
        options = catalog.target_options()
        stored = "日本語"
        chosen = catalog.option_value(stored)

        def restore(items):
            values = [item["value"] for item in items]
            index = values.index(chosen)
            return items[index]

        shuffled = list(options)
        random.Random(20260916).shuffle(shuffled)
        relabelled = [dict(item, text=f"标签 {position}") for position, item in enumerate(options)]
        for items in (options, shuffled, relabelled):
            restored = restore(items)
            self.assertEqual("ja", restored["value"])
            self.assertEqual("日语", TC.language(restored["value"]))

    def test_empty_and_automatic_selections_are_explicit(self):
        self.assertEqual("", TC.language(""))
        self.assertEqual("", catalog.option_value(""))
        self.assertEqual("auto", catalog.option_value("", automatic=True))
        self.assertEqual("auto", catalog.AUTO_ID)
        self.assertEqual("custom", catalog.CUSTOM_ID)

    def test_task_file_still_stores_legacy_values_from_native_input(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "draft.txt").write_text(ENGLISH, encoding="utf-8")
            result = TC.prepare(root, {
                "source_files": ["draft.txt"], "source_language": "English",
                "source_language_explicit": True, "target_language": "日本語",
                "target_language_explicit": True,
            })
            self.assertEqual("英语", result["source_language"])
            self.assertEqual("日语", result["target_language"])
            import yaml
            saved = yaml.safe_load((root / "翻译任务.yaml").read_text(encoding="utf-8"))
            self.assertEqual({"source": "英语", "target": "日语"}, saved["languages"])
            self.assertEqual("原文件名.ja.txt", saved["output"]["filename_rule"])
            self.assertEqual(saved, TC.validate(root, saved))

    def test_existing_checkpointed_task_refuses_a_direction_change_without_writing(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "draft.txt").write_text(ENGLISH, encoding="utf-8")
            TC.prepare(root, {
                "source_files": ["draft.txt"], "source_language": "英语",
                "source_language_explicit": True, "target_language": "简体中文",
                "target_language_explicit": True,
            })
            state = root / "译文/.hy-direct-state.json"
            state.parent.mkdir(parents=True, exist_ok=True)
            state.write_text(json.dumps({"chunks": [{"start": 1, "end": 1}]}), encoding="utf-8")
            before = snapshot(root)
            with self.assertRaisesRegex(ValueError, "已有译文"):
                TC.prepare(root, {
                    "source_files": ["draft.txt"], "source_language": "英语",
                    "source_language_explicit": True, "target_language": "日本語",
                    "target_language_explicit": True,
                })
            self.assertEqual(before, snapshot(root))


class ReviewCategoryTests(unittest.TestCase):
    def test_categories_have_stable_ids_and_display_labels(self):
        options = notes.category_options()
        self.assertEqual([category.id for category in notes.REVIEW_CATEGORIES],
                         [item["value"] for item in options])
        self.assertEqual(["疑似误译", "人名 / 地名", "漏译 / 多译", "数字 / 时间", "语气 / 表达", "其他"],
                         [item["text"] for item in options])
        for item in options:
            self.assertEqual(item["value"], notes.category_id(item["value"]))
            self.assertEqual(item["value"], notes.category_id(item["text"]))
            self.assertEqual(item["text"], notes.category_label(item["value"]))

    def test_unknown_free_text_category_is_preserved_not_guessed(self):
        self.assertEqual("", notes.category_id("语气问题"))
        self.assertEqual("语气问题", notes.category_label("语气问题"))
        self.assertFalse(notes.category_needs_related_samples("语气问题"))

    def test_legacy_chinese_label_keeps_its_historical_behaviour(self):
        self.assertEqual("names_places", notes.category_id("人名 / 地名"))
        self.assertEqual("names_places", notes.category_id("人名"))
        self.assertTrue(notes.category_needs_related_samples("人名"))
        self.assertTrue(notes.category_needs_related_samples("地名"))
        self.assertTrue(notes.category_needs_related_samples("译名"))
        self.assertFalse(notes.category_needs_related_samples("其他"))
        self.assertFalse(notes.category_needs_related_samples(""))

    def test_chinese_and_english_labels_share_one_identity(self):
        for raw in ("人名 / 地名", "names / places", "names_places", "人名/地名"):
            self.assertEqual("names_places", notes.category_id(raw))
            self.assertTrue(notes.category_needs_related_samples(raw))

    def test_related_samples_do_not_depend_on_the_display_language(self):
        book = {
            "segments": ["Mara went to North Harbor.", "Mara waited by the gate.",
                         "The letter came at dawn.", "Mara left North Harbor."],
            "translated": {1: "玛拉去了北港。", 2: "玛拉在门边等候。",
                           3: "信在黎明时到了。", 4: "玛拉离开了北港。"},
        }
        base = {"id": "a-1", "segment_id": 2, "source_text": "Mara waited by the gate.",
                "translation": "玛拉在门边等候。", "quote": "Mara", "note": ""}
        samples = []
        for raw in ("人名 / 地名", "names / places", "names_places"):
            row = {**base, "category": raw}
            data = json.loads(notes.review_prompt(row, book).split("\n资料：\n", 1)[1])
            samples.append(data.get("related_samples"))
        self.assertNotIn(None, samples)
        self.assertEqual(samples[0], samples[1])
        self.assertEqual(samples[0], samples[2])
        self.assertEqual([4], [item["segment_id"] for item in samples[0]])

        other = {**base, "category": "其他"}
        data = json.loads(notes.review_prompt(other, book).split("\n资料：\n", 1)[1])
        self.assertNotIn("related_samples", data)

    def test_stored_category_id_wins_without_rewriting_legacy_text(self):
        self.assertEqual("names_places",
                         notes.row_category_id({"category_id": "names_places"}))
        self.assertEqual("names_places",
                         notes.row_category_id({"category": "人名 / 地名"}))
        self.assertEqual("", notes.row_category_id({"category": "语气问题"}))
        self.assertEqual("", notes.row_category_id({"category": "人名 / 地名", "category_id": "bogus"}))
        self.assertEqual("人名 / 地名",
                         notes.row_category_label({"category": "人名 / 地名"}))
        self.assertEqual("人名 / 地名",
                         notes.row_category_label({"category_id": "names_places"}))
        self.assertEqual("自由文本", notes.row_category_label({"category": "自由文本"}))


class ReviewErrorCodeTests(unittest.TestCase):
    LEGACY_MESSAGES = (
        ("人工复查未返回匹配的标注 ID", "annotation.id_mismatch", "format"),
        ("人工复查结论格式无效", "annotation.verdict_format", "format"),
        ("复查缺少具体依据，不能仅以“无需改动”作为保留理由", "annotation.reason_not_specific", "evidence"),
        ("复查的证据编号不存在，请选择本段已有的 E 编号", "annotation.evidence_unknown", "evidence"),
        ("复查的原文证据并非逐字摘录", "annotation.evidence_not_exact", "evidence"),
        ("复查缺少原文证据，请选择相关的证据编号；无法确定时返回 uncertain",
         "annotation.evidence_missing", "evidence"),
        ("复查建议格式无效", "annotation.suggestion_format", "format"),
        ("建议修改时必须返回修改后的完整段落", "annotation.suggested_text_missing", "draft"),
        ("建议修改稿与原译完全相同", "annotation.suggested_text_unchanged", "draft"),
        ("建议修改稿超过 20000 字符", "annotation.suggested_text_too_long", "draft"),
        ("建议修改稿改写范围过大，请只修改标注涉及的内容", "annotation.suggested_text_rewrite", "draft"),
        ("复查结论与修改稿矛盾，请先明确是否建议修改", "annotation.suggested_text_conflict", "format"),
    )

    def test_exact_legacy_messages_keep_their_bucket(self):
        for message, code, bucket in self.LEGACY_MESSAGES:
            self.assertEqual(code, notes.legacy_error_code(message), message)
            self.assertEqual(code, notes.row_error_code({"error": message}), message)
            self.assertEqual(bucket, notes.error_bucket(code), message)

    def test_unknown_or_reworded_errors_are_never_guessed(self):
        for message in ("复查缺少具体依据",                     # a prefix, not the message
                        "复查的原文证据并非逐字摘录，另外还有补充说明",
                        "复查的原文证据并非逐字摘要",
                        "段落校验失败：目标语言不合格",
                        ""):
            self.assertEqual("", notes.legacy_error_code(message), message)
            self.assertEqual("", notes.error_bucket(notes.legacy_error_code(message)), message)

    def test_surrounding_whitespace_does_not_hide_a_known_legacy_message(self):
        message = "复查的原文证据并非逐字摘录"
        self.assertEqual("annotation.evidence_not_exact",
                         notes.legacy_error_code(f"  {message}\n"))

    def test_stored_code_is_authoritative_and_raw_text_is_kept(self):
        row = {"error_code": "annotation.evidence_not_exact",
               "error": "复查的原文证据并非逐字摘录"}
        self.assertEqual("annotation.evidence_not_exact", notes.row_error_code(row))
        self.assertEqual("evidence", notes.error_bucket(notes.row_error_code(row)))
        self.assertEqual("复查的原文证据并非逐字摘录", row["error"])
        unknown = {"error_code": "annotation.future_code", "error": "未来版本的错误"}
        self.assertEqual("annotation.future_code", notes.row_error_code(unknown))
        self.assertEqual("", notes.error_bucket(notes.row_error_code(unknown)))

    def test_review_errors_carry_a_code_and_the_raw_message(self):
        error = notes.ReviewError("人工复查结论格式无效", "annotation.verdict_format")
        self.assertEqual("annotation.verdict_format", error.code)
        self.assertEqual("人工复查结论格式无效", str(error))
        self.assertEqual("format", notes.error_bucket(getattr(error, "code", "")))


if __name__ == "__main__":
    unittest.main()
