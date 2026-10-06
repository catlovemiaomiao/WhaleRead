"""Stable language identities, independent of display labels and of Qt.

Three different things are easy to confuse and must stay separate:

* the **stable ID** (``ja``) is the identity used by option models and by any
  new preference; it never changes with the interface locale;
* the **native name** (``日本語``) is what the picker shows and accepts as
  manual input;
* the **legacy value** (``日语``) is what existing task files, prompts and
  stored per-book selections already contain and must keep containing.

Aliases are matched on a Unicode-normalised key, so NFC and NFD spellings of a
native name resolve identically.  Unknown custom names are never collapsed:
they keep their exact text and continue to receive the hashed output suffix
produced by :func:`output_suffix`.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import NamedTuple, Optional

AUTO_ID = "auto"
CUSTOM_ID = "custom"
AUTO_LABEL = "自动识别"
CUSTOM_LABEL = "其他语言（手动输入）"


class Language(NamedTuple):
    """One semantic language, with every representation it is known by."""

    id: str
    native_name: str
    legacy_value: str
    output_suffix: str
    family: str
    scripts: tuple[str, ...]
    aliases: tuple[str, ...] = ()
    common: bool = True

    def option(self) -> dict:
        """A QML-friendly option row: stable value plus native display text."""
        return {"value": self.id, "text": self.native_name, "kind": "language",
                "legacy": self.legacy_value, "suffix": self.output_suffix}


# The 18 common languages keep the historical picker order; uk/el/he stay
# manually typeable (and keep their alias support) without joining the picker.
LANGUAGES: tuple[Language, ...] = (
    Language("zh-Hans", "简体中文", "简体中文", "zh-CN", "han", ("han",),
             ("zh", "zh-cn", "zh-hans", "zh-sg", "chinese", "中文", "简体", "简体中文")),
    Language("zh-Hant", "繁體中文", "繁体中文", "zh-TW", "han", ("han",),
             ("zh-tw", "zh-hk", "zh-mo", "zh-hant", "chinese-traditional",
              "繁体", "繁体中文", "正体中文")),
    Language("en", "English", "英语", "en", "latin", ("latin",),
             ("en-us", "en-gb", "english", "英文", "英语")),
    Language("ja", "日本語", "日语", "ja", "japanese", ("han", "kana"),
             ("jp", "japanese", "日文", "日语")),
    Language("ko", "한국어", "韩语", "ko", "korean", ("hangul", "han"),
             ("korean", "韩文", "朝鲜语", "韩语")),
    Language("fr", "Français", "法语", "fr", "latin", ("latin",),
             ("fr-fr", "french", "法文", "法语")),
    Language("de", "Deutsch", "德语", "de", "latin", ("latin",),
             ("german", "德文", "德语")),
    Language("es", "Español", "西班牙语", "es", "latin", ("latin",),
             ("spanish", "español", "西班牙文", "西班牙语")),
    Language("pt", "Português", "葡萄牙语", "pt", "latin", ("latin",),
             ("portuguese", "葡萄牙文", "葡萄牙语")),
    Language("it", "Italiano", "意大利语", "it", "latin", ("latin",),
             ("italian", "意大利文", "意大利语")),
    Language("ru", "Русский", "俄语", "ru", "cyrillic", ("cyrillic",),
             ("russian", "俄文", "俄语")),
    Language("ar", "العربية", "阿拉伯语", "ar", "arabic", ("arabic",),
             ("arabic", "阿拉伯文", "阿拉伯语")),
    Language("nl", "Nederlands", "荷兰语", "nl", "latin", ("latin",),
             ("dutch", "荷兰文", "荷兰语")),
    Language("pl", "Polski", "波兰语", "pl", "latin", ("latin",),
             ("polish", "波兰文", "波兰语")),
    Language("tr", "Türkçe", "土耳其语", "tr", "latin", ("latin",),
             ("turkish", "土耳其文", "土耳其语")),
    Language("vi", "Tiếng Việt", "越南语", "vi", "latin", ("latin",),
             ("vietnamese", "越南文", "越南语")),
    Language("th", "ไทย", "泰语", "th", "thai", ("thai",),
             ("thai", "泰文", "泰语")),
    Language("hi", "हिन्दी", "印地语", "hi", "devanagari", ("devanagari",),
             ("hindi", "印地文", "印地语")),
    Language("uk", "Українська", "乌克兰语", "uk", "cyrillic", ("cyrillic",),
             ("ukrainian", "乌克兰文", "乌克兰语"), common=False),
    Language("el", "Ελληνικά", "希腊语", "el", "greek", ("greek",),
             ("greek", "希腊文", "希腊语"), common=False),
    Language("he", "עברית", "希伯来语", "he", "hebrew", ("hebrew",),
             ("hebrew", "希伯来文", "希伯来语"), common=False),
)

_BY_LEGACY = {entry.legacy_value: entry for entry in LANGUAGES}


def _lookup_key(value: str) -> str:
    """Fold a user-supplied name to its lookup key without changing identity."""
    return unicodedata.normalize("NFC", value).casefold().replace("_", "-")


def _build_aliases() -> dict[str, str]:
    table: dict[str, str] = {}
    for entry in LANGUAGES:
        for alias in (entry.id, entry.native_name, entry.legacy_value, *entry.aliases):
            table.setdefault(_lookup_key(alias), entry.legacy_value)
    return table


ALIASES: dict[str, str] = _build_aliases()

_PARENTHETICAL = re.compile(r"[（(][^）)]*[）)]")


def normalize(raw) -> str:
    """Return the legacy task value for any accepted name, else the raw text.

    This is the single normalizer used by both the UI adapter and the task
    contract.  An unknown custom name is returned exactly as the reader typed
    it: it must not be collapsed into a known language or a generic bucket.
    """
    original = str(raw or "").strip()
    if not original:
        return ""
    # A parenthetical qualifier may help a known alias resolve ("法语（法国）"),
    # but it is part of an unknown custom name's identity.  Only return the
    # stripped candidate when it resolves to a catalog entry; otherwise keep
    # the reader's original text so distinct custom languages cannot collapse.
    stripped = _PARENTHETICAL.sub("", original).strip()
    for candidate in dict.fromkeys((original, stripped)):
        if not candidate:
            continue
        key = _lookup_key(candidate)
        known = ALIASES.get(key) or ALIASES.get(key.split("-", 1)[0])
        if known:
            return known
    return original


def find(raw) -> Optional[Language]:
    """Return the catalog entry for a name, or None for an unknown custom name."""
    return _BY_LEGACY.get(normalize(raw))


def language_id(raw) -> str:
    """Stable identity of a name; '' when it is not in the catalog."""
    entry = find(raw)
    return entry.id if entry else ""


def common_languages() -> list[Language]:
    return [entry for entry in LANGUAGES if entry.common]


def common_legacy_values() -> tuple[str, ...]:
    return tuple(entry.legacy_value for entry in common_languages())


def _action(value: str, text: str) -> dict:
    return {"value": value, "text": text, "kind": "action", "legacy": "", "suffix": ""}


def source_options() -> list[dict]:
    """Structured source picker: automatic, the common languages, then manual input."""
    return ([_action(AUTO_ID, AUTO_LABEL)]
            + [entry.option() for entry in common_languages()]
            + [_action(CUSTOM_ID, CUSTOM_LABEL)])


def target_options() -> list[dict]:
    """Structured target picker; ``auto`` is deliberately not a target."""
    return ([entry.option() for entry in common_languages()]
            + [_action(CUSTOM_ID, CUSTOM_LABEL)])


def option_value(raw, automatic: bool = False) -> str:
    """Return the picker value that represents a stored selection.

    ``auto``/``custom`` are actions, never translation languages, so an
    unknown custom name maps to ``custom`` while ``automatic`` maps to ``auto``.
    """
    if automatic:
        return AUTO_ID
    value = normalize(raw)
    if not value:
        return ""
    entry = _BY_LEGACY.get(value)
    # Manual-only catalog entries retain aliases/suffixes, but because they do
    # not appear in the common picker they must reopen through its custom row.
    return entry.id if entry and entry.common else CUSTOM_ID


def output_suffix(raw) -> str:
    """Output filename marker for a target language.

    Known languages keep their stable tag.  Unknown custom names keep the
    existing readable-slug-plus-hash form so two names that slug alike never
    share an output path.
    """
    value = normalize(raw)
    entry = _BY_LEGACY.get(value)
    if entry:
        return entry.output_suffix
    slug = re.sub(r"[^0-9A-Za-z]+", "-", value).strip("-")[:16] or "other"
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
    return f"x-{slug}-{digest}"


def epub_tag(raw) -> str:
    """Valid known language tag, or ``und`` for an unknown custom name."""
    entry = find(raw)
    return entry.output_suffix if entry else "und"


def writing_scripts(raw) -> tuple[str, ...]:
    entry = find(raw)
    return entry.scripts if entry else ()


def declared_family(raw) -> str:
    entry = find(raw)
    return entry.family if entry else ""
