"""Shared, model-independent task preparation and validation."""
from __future__ import annotations

import argparse
import codecs
import csv
import hashlib
import io
import json
import os
import posixpath
import re
import subprocess
import tempfile
import zipfile
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

import fcntl
import yaml

from book_identity import (SCHEMA as BOOK_IDENTITY_SCHEMA, aggregate_sha256,
                           file_sha256, task_identity, text_sha256, valid_sha256)
from language_catalog import (ALIASES, LANGUAGES, common_legacy_values,
                              declared_family, epub_tag, normalize,
                              output_suffix as catalog_output_suffix,
                              writing_scripts)

MODES = ("none", "fixed", "auto")

AUTO_LANGUAGE = "自动识别"

# Semantic language identities (stable ID, native name, legacy task value,
# output suffix, accepted aliases) live in the model-independent
# ``language_catalog`` module.  The constants below are kept because callers
# already import them, but they are derived from that single table: a legacy
# value, an ID, a native name and an English alias can only ever mean one
# language, and no translation value is declared twice.
COMMON_LANGUAGES = common_legacy_values()

LANGUAGE_ALIASES = dict(ALIASES)

# Output filename markers follow the language actually handed to the translator.
OUTPUT_SUFFIXES = {entry.legacy_value: entry.output_suffix for entry in LANGUAGES}

# Writing systems a language actually uses. Used to flag an obvious script
# conflict without pretending same-script languages are distinguishable.
LANGUAGE_SCRIPTS = {entry.legacy_value: entry.scripts for entry in LANGUAGES}
LANGUAGE_SCRIPTS["中文"] = ("han",)

_LANGUAGE_FAMILIES = (
    ("han", ("简体中文", "繁体中文", "中文", "chinese", "zh")),
    ("japanese", ("日语", "日文", "japanese", "ja")),
    ("korean", ("韩语", "韩文", "朝鲜语", "korean", "ko")),
    ("cyrillic", ("俄语", "俄文", "乌克兰语", "russian", "ukrainian", "ru", "uk")),
    ("arabic", ("阿拉伯语", "阿拉伯文", "arabic", "ar")),
    ("hebrew", ("希伯来语", "hebrew", "he")),
    ("greek", ("希腊语", "greek", "el")),
    ("thai", ("泰语", "泰文", "thai", "th")),
    ("devanagari", ("印地语", "hindi", "hi")),
)

_SCRIPT_LABELS = {
    "han": "汉字", "kana": "假名", "hangul": "谚文", "latin": "拉丁字母",
    "cyrillic": "西里尔字母", "arabic": "阿拉伯字母", "hebrew": "希伯来字母",
    "greek": "希腊字母", "thai": "泰文字母", "devanagari": "天城文",
}

# Function words used to separate the Latin-script languages that share an
# alphabet. A small model is never asked to guess; a document without enough
# signal is reported as uncertain instead of being called English.
_LATIN_STOPWORDS = {
    "英语": frozenset(
        "the a an and or of to in is was were are be been being for on with at by from as that "
        "this these those it its he she they we you i his her their our your not but had has have "
        "will would could should there here when what who whom which while about after before over "
        "under into out up down no yes than then them him us my me said all one two more most some "
        "any many much very can may might must shall do does did done".split()),
    "法语": frozenset(
        "le la les un une des du de et est sont était étaient être dans pour par avec sans sur sous "
        "entre chez ce cette ces qui que quoi dont où il elle ils elles nous vous je tu ne pas plus "
        "comme mais ou donc car son sa ses leur leurs au aux en y se aussi très bien fait "
        "faire dit deux même autre tous tout".split()),
    "德语": frozenset(
        "der die das und ist sind war waren nicht ein eine einen einem einer mit von zu im in auf "
        "für als sich dem den des es er sie wir ihr ich du auch aber oder aus bei nach über unter "
        "noch nur wie wenn dann dass werden wurde haben hat hatte sein seine ihren ihre seiner "
        "dieser diese dieses kann muss soll will".split()),
    "西班牙语": frozenset(
        "el la los las un una unos unas de del y es son era eran en por para con sin sobre entre "
        "que quien como pero o no más muy se su sus al lo le les me te nos os yo tú él ella ellos "
        "ellas esto esta ese esa hay fue han ha está están desde hasta cuando donde porque también "
        "todo todos dos".split()),
    "葡萄牙语": frozenset(
        "o os as um uma uns umas de do da dos das e é são era eram em no na nos nas por para com "
        "sem sobre entre que quem como mas ou não mais muito se seu sua seus suas ao aos lhe eu tu "
        "ele ela eles elas isto este esta isso esse essa há foi ser estar está estão desde até "
        "quando onde porque também todo todos dois".split()),
    "意大利语": frozenset(
        "il lo la i gli le un uno una di del della dei delle e è sono era erano in nel nella per "
        "con senza su tra fra che chi come ma o non più molto si suo sua suoi sue al allo alla ai "
        "alle io tu lui lei noi voi loro questo questa quello quella da dal dalla quando dove "
        "perché anche tutto tutti due".split()),
    "荷兰语": frozenset(
        "de het een en van in is zijn was waren niet op te dat die dit deze met voor aan als er ook "
        "maar of om door naar over onder nog alleen dan toen heeft hebben had wordt worden hij zij "
        "wij jij ik je we ze zich hun".split()),
    "波兰语": frozenset(
        "i w na z do nie się jest są był była było byli że to ten ta te jak ale lub po przez dla "
        "od przy nad pod już jeszcze tylko także czy on ona oni one my wy ja ty jego jej ich oraz "
        "być ma mają miał bardzo kiedy gdzie dlaczego".split()),
    "土耳其语": frozenset(
        "ve bir bu şu o da de için ile ama fakat çok daha en gibi kadar sonra önce olan olarak var "
        "yok değil ben sen biz siz onlar ne nasıl neden çünkü ki ise mi mı mu mü her iki".split()),
    "越南语": frozenset(
        "và là của có không được trong một những các với cho về khi thì mà này đó kia như để đến "
        "từ trên dưới sau trước cũng nhưng hoặc vì nên người tôi bạn anh chị em chúng hai".split()),
}

_LATIN_DIACRITICS = {
    "法语": "àâçéèêëîïôûùüÿœæ",
    "德语": "äöüß",
    "西班牙语": "ñ¿¡áíóú",
    "葡萄牙语": "ãõçáâàéêíóôú",
    "意大利语": "àèéìòù",
    "波兰语": "ąćęłńóśźż",
    "土耳其语": "ğışçöü",
    "越南语": "ơưăâđêôạảấầẩẫậắằẳẵặếềểễệốồổỗộớờởỡợụủứừửữựỳỵỷỹ",
}


class _EpubTextParser(HTMLParser):
    """Small dependency-free XHTML-to-prose extractor for EPUB imports."""

    BLOCKS = {
        "address", "article", "aside", "blockquote", "div", "figcaption", "footer",
        "h1", "h2", "h3", "h4", "h5", "h6", "header", "li", "main", "p",
        "pre", "section", "td", "th", "title",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "svg", "nav"}:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        if tag in self.BLOCKS:
            self.parts.append("\n\n")
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"script", "style", "svg", "nav"} and self.skip_depth:
            self.skip_depth -= 1
            return
        if not self.skip_depth and tag in self.BLOCKS:
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        if not self.skip_depth:
            self.parts.append(data)

    def text(self) -> str:
        raw = "".join(self.parts).replace("\xa0", " ")
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r" *\n *", "\n", raw)
        return re.sub(r"\n{3,}", "\n\n", raw).strip()


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def extract_epub(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        container = ElementTree.fromstring(archive.read("META-INF/container.xml"))
        rootfiles = [node for node in container.iter() if _local_name(node.tag) == "rootfile"]
        if not rootfiles or not rootfiles[0].get("full-path"):
            raise ValueError("EPUB 缺少有效的 OPF 入口")
        opf_name = posixpath.normpath(rootfiles[0].get("full-path") or "")
        opf = ElementTree.fromstring(archive.read(opf_name))
        manifest: dict[str, tuple[str, str]] = {}
        spine: list[str] = []
        for node in opf.iter():
            name = _local_name(node.tag)
            if name == "item" and node.get("id") and node.get("href"):
                manifest[node.get("id") or ""] = (
                    node.get("href") or "",
                    node.get("media-type") or "",
                )
            elif name == "itemref" and node.get("idref"):
                spine.append(node.get("idref") or "")
        base = posixpath.dirname(opf_name)
        chapters: list[str] = []
        for item_id in spine:
            entry = manifest.get(item_id)
            if not entry or "html" not in entry[1]:
                continue
            member = posixpath.normpath(posixpath.join(base, entry[0].split("#", 1)[0]))
            if member.startswith("../") or member.startswith("/"):
                raise ValueError("EPUB 章节路径越界")
            parser = _EpubTextParser()
            parser.feed(archive.read(member).decode("utf-8", errors="replace"))
            chapter = parser.text()
            if chapter:
                chapters.append(chapter)
        if not chapters:
            raise ValueError("EPUB 书脊中没有可提取正文")
        return "\n\n".join(chapters)


def read_text_auto(path: Path, preferred: str | None = None) -> str:
    data = path.read_bytes()
    candidates = [preferred] if preferred else []
    candidates.extend(["utf-8-sig", "utf-16", "gb18030", "cp932", "big5"])
    seen: set[str] = set()
    for encoding in candidates:
        if not encoding or encoding.casefold() in seen:
            continue
        seen.add(encoding.casefold())
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    raise ValueError(f"无法识别文本编码：{path.name}；请另存为 UTF-8")


def extract_source_text(path: Path, preferred_encoding: str | None = None) -> str:
    suffix = path.suffix.lower()
    if suffix in (".txt", ".md"):
        return read_text_auto(path, preferred_encoding)
    if suffix in (".rtf", ".doc", ".docx"):
        from apple_services import extract_document_text
        return extract_document_text(path)
    if suffix == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(str(path))
        text = '\n\n'.join(page.extract_text(extraction_mode='layout') or '' for page in reader.pages)
        if not text.strip():
            raise ValueError("PDF 没有可读取的文字层；请使用带文字层的 PDF。")
        return text
    if suffix == ".epub":
        return extract_epub(path)
    raise ValueError("支持 TXT、Markdown、RTF、DOC/DOCX、EPUB 和带文字层的 PDF")


class LanguageDetectionError(ValueError):
    """Raised when the document cannot be identified with confidence."""

    def __init__(self, message: str, detection: dict | None = None) -> None:
        super().__init__(message)
        self.detection = detection or {}
        self.candidates = list(self.detection.get("candidates") or [])


def script_counts(text: str) -> dict[str, int]:
    """Count writing-system signals used for deterministic language guards."""
    return {
        "han": len(re.findall(r"[\u3400-\u9fff]", text)),
        "kana": len(re.findall(r"[\u3040-\u30ff]", text)),
        "hangul": len(re.findall(r"[\uac00-\ud7af]", text)),
        "latin": len(re.findall(r"[A-Za-zÀ-ÖØ-öø-ÿĀ-ž]", text)),
        "cyrillic": len(re.findall(r"[\u0400-\u04ff]", text)),
        "arabic": len(re.findall(r"[\u0600-\u06ff\u0750-\u077f]", text)),
        "hebrew": len(re.findall(r"[\u0590-\u05ff]", text)),
        "greek": len(re.findall(r"[\u0370-\u03ff\u1f00-\u1fff]", text)),
        "thai": len(re.findall(r"[\u0e00-\u0e7f]", text)),
        "devanagari": len(re.findall(r"[\u0900-\u097f]", text)),
    }


def _detection(language_name: str, confidence: str, script: str,
               candidates: list[str] | None = None) -> dict:
    return {
        "language": language_name,
        "confidence": confidence,
        "uncertain": not language_name,
        "script": script,
        "candidates": candidates or ([language_name] if language_name else []),
        "detail": "",
    }


def _distinct_stopwords() -> dict[str, frozenset[str]]:
    """Keep only the function words no other candidate also claims.

    Shared words (Spanish/Portuguese "de", Dutch/Turkish "de") prove nothing
    about which language a document is written in.
    """
    result: dict[str, frozenset[str]] = {}
    for name, words in _LATIN_STOPWORDS.items():
        others: set[str] = set()
        for other, other_words in _LATIN_STOPWORDS.items():
            if other != name:
                others |= other_words
        result[name] = frozenset(word for word in words if word not in others)
    return result


_LATIN_DISTINCT = _distinct_stopwords()


def _latin_language_scores(text: str) -> dict[str, dict[str, float]]:
    """Score the Latin-script languages on function words and diacritics."""
    lowered = text.casefold()
    tokens = re.findall(r"[a-z\u00c0-\u024f]+", lowered)
    words = [token for token in tokens if len(token) >= 2]
    singles = [token for token in tokens if len(token) == 1]
    scores: dict[str, dict[str, float]] = {}
    for name, stopwords in _LATIN_STOPWORDS.items():
        distinct = _LATIN_DISTINCT[name]
        distinct_hits = sum(1 for token in words if token in distinct)
        distinct_hits += 0.5 * sum(1 for token in singles if token in distinct)
        shared_hits = sum(1 for token in words if token in stopwords and token not in distinct)
        shared_hits += 0.5 * sum(1 for token in singles if token in stopwords and token not in distinct)
        marks = sum(lowered.count(char) for char in _LATIN_DIACRITICS.get(name, ""))
        scores[name] = {
            "distinct": distinct_hits,
            "stop": distinct_hits + shared_hits,
            "marks": marks,
            "score": 2.0 * distinct_hits + 0.5 * shared_hits + 0.75 * min(marks, 12),
        }
    return scores


def _latin_detection(text: str) -> dict:
    scores = _latin_language_scores(text)
    ranked = sorted(scores.items(), key=lambda item: (-item[1]["score"], item[0]))
    best, best_data = ranked[0]
    runner_distinct = max((data["distinct"] for _, data in ranked[1:]), default=0.0)
    runner_score = ranked[1][1]["score"] if len(ranked) > 1 else 0.0
    # Diacritics alone overlap heavily between neighbouring languages; a real
    # decision needs function words the runner-up cannot also claim, or a
    # clearly dominant score from words the candidates share.
    decisive = (
        best_data["distinct"] >= 2
        and runner_distinct <= max(1.0, best_data["distinct"] * 0.5)
    ) or (
        best_data["score"] >= 5 and runner_score <= best_data["score"] * 0.5
    )
    if decisive:
        strong = (
            best_data["distinct"] >= 3
            and runner_distinct <= best_data["distinct"] * 0.34
        ) or (
            best_data["score"] >= 10 and runner_score <= best_data["score"] * 0.25
        )
        confidence = "high" if strong else "medium"
        return _detection(best, confidence, "latin")
    candidates = [name for name, data in ranked if data["score"] > 0] or [best]
    if len(candidates) < 2:
        candidates = [name for name, _ in ranked[:2]]
    listed = "、".join(candidates[:3])
    detail = (f"正文以拉丁字母为主，但常用词不足以可靠区分{listed}；"
              "请手动确认源语言，程序不会默认当作英语。")
    return {
        "language": "",
        "confidence": "low",
        "uncertain": True,
        "script": "latin",
        "candidates": candidates[:3],
        "detail": detail,
    }


def detect_language(text: str) -> dict:
    """Return the dominant writing system and, when possible, its language.

    The result is deliberately explicit about uncertainty: a document whose
    Latin script cannot be separated into a known language is reported with
    ``uncertain`` and candidate names instead of being called English.
    """
    counts = script_counts(text)
    han, kana, hangul = counts["han"], counts["kana"], counts["hangul"]
    latin, cyrillic = counts["latin"], counts["cyrillic"]
    if kana >= 4 and kana + han >= max(12, latin // 2):
        return _detection("日语", "high", "kana")
    if hangul >= 4 and hangul + han >= max(12, latin // 2):
        return _detection("韩语", "high", "hangul")
    if han >= 4 and han >= max(4, latin // 3):
        return _detection("中文", "high", "han")
    if cyrillic >= 4 and cyrillic >= max(4, latin):
        return _detection("俄语", "medium", "cyrillic")
    for script, name in (("arabic", "阿拉伯语"), ("hebrew", "希伯来语"),
                         ("greek", "希腊语"), ("thai", "泰语"),
                         ("devanagari", "印地语")):
        if counts[script] >= 4 and counts[script] >= max(4, latin // 2):
            return _detection(name, "high", script)
    if latin >= 4:
        return _latin_detection(text)
    if sum(counts.values()) == 0:
        return {
            "language": "", "confidence": "low", "uncertain": True, "script": "unknown",
            "candidates": [],
            "detail": "没有找到可识别的文字，无法自动判断源语言；请手动指定。",
        }
    return {
        "language": "", "confidence": "low", "uncertain": True, "script": "unknown",
        "candidates": [],
        "detail": "正文过短或文字特征不足，无法可靠判断源语言；请手动指定。",
    }


def detect_source_language(text: str) -> str:
    """Detect the source language; raise when the document is ambiguous."""
    result = detect_language(text)
    if result["language"]:
        return result["language"]
    raise LanguageDetectionError(result["detail"], detection=result)


def language_scripts(raw: str) -> tuple[str, ...]:
    return writing_scripts(raw)


def script_conflict(raw_language: str, text: str) -> bool:
    """True when the declared language uses a script the text never shows.

    Same-script languages (English vs. French) are intentionally not treated
    as conflicts: the reader may know the book better than word counting does.
    """
    scripts = language_scripts(raw_language)
    if not scripts:
        return False
    counts = script_counts(text)
    present = {name for name, count in counts.items() if count >= 4}
    if not present:
        return False
    return not (set(scripts) & present)


def script_label(script: str) -> str:
    return _SCRIPT_LABELS.get(script, "原文所用文字")


def task_target_language(root: Path) -> str:
    """Read only the saved target language of a task; '' when unavailable."""
    file = Path(root) / "翻译任务.yaml"
    if not file.is_file():
        return ""
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return ""
    if not isinstance(data, dict):
        return ""
    return language(str((data.get("languages") or {}).get("target") or ""))


def language_family(raw: str) -> str:
    value = language(raw)
    known = declared_family(value)
    if known:
        return known
    # A custom name that embeds a known one (e.g. "俄语方言") still reports the
    # historical family; a genuinely unknown name keeps the latin fallback.
    folded = value.casefold()
    for family, tokens in _LANGUAGE_FAMILIES:
        if any(token in folded for token in tokens):
            return family
    if value:
        return "latin"
    return "unknown"


def output_suffix(target_language: str) -> str:
    """Return the output filename marker matching the actual target language."""
    return catalog_output_suffix(target_language)


def epub_language_tag(target_language: str) -> str:
    """Use a valid known language tag, or honestly mark an unknown custom name."""
    return epub_tag(target_language)


def atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".task-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def task_lock(root: Path):
    with (root / ".translation.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("当前任务正在执行，请等待结束后再准备或启动") from exc
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def inside(root: Path, raw: str) -> Path:
    path = (root / raw).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("任务文件必须位于选定任务目录内")
    return path


def language(raw: str) -> str:
    """Canonical legacy task value for any accepted name.

    IDs, native names, English and Chinese aliases and manual input with a
    parenthetical all resolve here; an unknown custom name is preserved
    verbatim so it keeps its own output suffix.
    """
    return normalize(raw)


def read_glossary(path: Path) -> list[dict]:
    if path.suffix.lower() == ".tsv":
        entries = list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig")), delimiter="\t"))
    else:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        entries = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        raise ValueError("术语表应为 JSON 条目列表或 source/target/note 三列 TSV")
    result, seen = [], {}
    for row in entries:
        if not isinstance(row, dict):
            raise ValueError("术语条目必须包含 source、target")
        src, target = row.get("source"), row.get("target")
        note = row.get("note") or ""
        if not isinstance(src, str) or not isinstance(target, str) or not src.strip() or not target.strip():
            raise ValueError("术语 source、target 均须为非空字符串")
        src, target = src.strip(), target.strip()
        if len(src) > 200 or len(target) > 200 or not isinstance(note, str) or len(note) > 500:
            raise ValueError("术语或说明过长")
        key = src.casefold()
        if key in seen and seen[key] != target:
            raise ValueError(f"术语存在多个译法，请先统一：{src}")
        if key not in seen:
            seen[key] = target
            result.append({"source": src, "target": target, "note": note})
    return result


def validate(root: Path, config: dict) -> dict:
    if not isinstance(config, dict) or config.get("schema_version") != 1:
        raise ValueError("任务配置须使用 schema_version: 1；请运行准备任务规范化")
    langs = config.get("languages") or {}
    for key in ("source", "target"):
        if not isinstance(langs.get(key), str) or not langs[key].strip() or "请填写" in langs[key]:
            raise ValueError(f"缺少有效的 languages.{key}")
    if language(langs["source"]) == AUTO_LANGUAGE:
        raise ValueError("languages.source 仍是自动识别，请回到 Prepare 确认源语言并写入任务")
    if language(langs["target"]) == AUTO_LANGUAGE:
        raise ValueError("目标语言不能是自动识别，请回到 Prepare 选择目标语言")
    source = config.get("source") or {}
    files = source.get("files")
    if not isinstance(files, list) or not files or not all(isinstance(x, str) for x in files):
        raise ValueError('source.files 必须是路径字符串列表，例如 ["原文/book.txt"]；请运行准备任务')
    paths = [inside(root, raw) for raw in files]
    if len(set(paths)) != len(paths):
        raise ValueError("原文路径重复")
    try:
        codecs.lookup(source.get("encoding", "UTF-8"))
    except LookupError as exc:
        raise ValueError("原文编码无效，请运行准备任务转为 UTF-8") from exc
    source_texts = []
    for path in paths:
        if not path.is_file():
            raise ValueError(f"原文不存在：{path.name}")
        if path.suffix.lower() not in (".txt", ".md"):
            raise ValueError("原文须为 TXT/Markdown；RTF 请先通过准备任务自动转换")
        text = path.read_text(encoding=source.get("encoding", "UTF-8"))
        if not text.strip() or text.lstrip().startswith("{\\rtf"):
            raise ValueError("原文为空或仍含 RTF 容器，须先转换")
        source_texts.append(text)
    identity = source.get("identity") or {}
    if identity:
        if not isinstance(identity, dict) or identity.get("schema") != BOOK_IDENTITY_SCHEMA:
            raise ValueError("原文内容身份格式无效，请回到 Prepare 重新生成任务")
        if not valid_sha256(identity.get("sha256")):
            raise ValueError("原文内容哈希无效，请回到 Prepare 重新生成任务")
        assets = identity.get("assets_sha256")
        if (not isinstance(assets, list) or not assets
                or any(not valid_sha256(value) for value in assets)
                or aggregate_sha256(assets) != identity.get("sha256")):
            raise ValueError("原始文件哈希记录无效，请回到 Prepare 重新生成任务")
        if identity.get("text_sha256") != text_sha256(source_texts):
            raise ValueError("原文正文与导入时的内容身份不一致，请新建译本")
    combined = "\n".join(source_texts)
    configured_source = language(langs["source"])
    detection = detect_language(combined)
    if script_conflict(configured_source, combined):
        raise ValueError(
            f"语言方向冲突：正文以{script_label(detection['script'])}为主，"
            f"但 languages.source 写成{configured_source}；请回到 Prepare 重新确认源语言"
        )
    output = config.get("output") or {}
    output_dir = inside(root, output.get("directory") or "译文")
    target = language(langs["target"])
    name = output.get("filename_rule") or f"原文件名.{output_suffix(target)}.txt"
    if not isinstance(name, str) or Path(name).name != name or name in (".", ".."):
        raise ValueError("译文文件名无效")
    suffix_language = {code.casefold(): value for value, code in OUTPUT_SUFFIXES.items()}
    for token in Path(name).name.split("."):
        marker = suffix_language.get(token.casefold())
        if marker and marker != target:
            raise ValueError(
                f"输出文件名标记为 {token}，但目标语言是{target}；请回到 Prepare 修复"
            )
    final = output_dir / name.replace("原文件名", paths[0].stem)
    progress = inside(root, (config.get("progress") or {}).get("file") or "翻译进度.json")
    protected = paths + [root / "翻译任务.yaml"]
    if final.resolve() in protected or progress in protected or final.resolve() == progress:
        raise ValueError("输出、进度与原文/配置路径冲突")
    chunk = config.get("chunking") or {}
    for key, lo, hi in (("max_source_chars", 500, 6000), ("max_segments", 1, 200), ("context_segments", 0, 20), ("parallel_requests", 1, 2)):
        if key in chunk and (type(chunk[key]) is not int or not lo <= chunk[key] <= hi):
            raise ValueError(f"chunking.{key} 应为 {lo}～{hi} 的整数")
    if chunk.get("segment_mode", "auto") not in ("auto", "wrapped_prose", "nonempty_line", "blank_line"):
        raise ValueError("分段模式无效")
    glossary = config.get("glossary") or {"mode": "none"}
    if glossary.get("mode") not in MODES:
        raise ValueError("glossary.mode 必须是 none、fixed 或 auto")
    if glossary["mode"] == "fixed":
        path = inside(root, glossary.get("file") or "术语表.json")
        if not path.is_file() or not read_glossary(path):
            raise ValueError("固定术语模式须选择非空术语表（JSON 或 TSV）")
    return config


def prepare(root: Path, spec: dict) -> dict:
    """Write only this selected root; preserve completed tasks and original input."""
    root = root.resolve()
    with task_lock(root):
        file = root / "翻译任务.yaml"
        old = yaml.safe_load(file.read_text(encoding="utf-8")) if file.exists() else {}
        old = old if isinstance(old, dict) else {}
        output_dir = inside(root, (old.get("output") or {}).get("directory") or "译文")
        state = output_dir / ".hy-direct-state.json"
        if state.exists() and json.loads(state.read_text()).get("chunks"):
            raise ValueError("任务已有译文断点；请另选新任务文件夹，避免改变既有翻译规则")
        config = dict(old)
        config["schema_version"] = 1
        config["task_name"] = spec.get("task_name") or old.get("task_name") or root.name
        langs = old.get("languages") or {}
        raw_files = spec.get("source_files") or (old.get("source") or {}).get("files") or []
        if not raw_files:
            raise ValueError("请提供原文文件路径")
        converted = []
        contents = []
        asset_hashes = []
        for raw in raw_files:
            raw = raw.get("path") if isinstance(raw, dict) else raw
            if not isinstance(raw, str):
                raise ValueError("原文路径无效")
            src = Path(raw).expanduser()
            src = (root / src).resolve() if not src.is_absolute() else src.resolve()
            if not src.is_file():
                raise ValueError(f"原文不存在：{src.name}")
            content = extract_source_text(src, spec.get("source_encoding"))
            if not content.strip() or content.lstrip().startswith("{\\rtf"):
                raise ValueError("未提取到有效正文")
            asset_hashes.append(file_sha256(src))
            tag = hashlib.sha256(content.encode()).hexdigest()[:10]
            stem = src.stem
            if stem.endswith("-" + tag):
                stem = stem[:-(len(tag) + 1)]
            dest = inside(root, f"原文/{stem}-{tag}.txt")
            if not dest.exists():
                atomic(dest, content)
            elif dest.read_text(encoding="utf-8") != content:
                raise ValueError("规范化原文路径已存在且内容不同")
            converted.append(str(dest.relative_to(root)))
            contents.append(content)
            if src.suffix.lower() == '.epub':
                from epub_translation import attach
                attach(root, src)
        combined = "\n".join(contents)
        detection = detect_language(combined)
        source_selection_supplied = ("source_language" in spec
                                     or "source_language_explicit" in spec)
        source_explicit = spec.get("source_language_explicit") is True
        if source_selection_supplied:
            declared_source = language(str(spec.get("source_language") or ""))
            if declared_source == AUTO_LANGUAGE:
                declared_source = ""
            if source_explicit:
                if not declared_source:
                    raise ValueError("已选择手动指定源语言，但没有提供有效的语言名称")
                if script_conflict(declared_source, combined):
                    raise ValueError(
                        f"语言方向冲突：正文没有使用{declared_source}所需的文字，"
                        "请重新选择源语言或改回自动识别"
                    )
                source_language = declared_source
            elif detection["language"]:
                source_language = detection["language"]
            else:
                raise LanguageDetectionError(
                    detection["detail"] or "无法自动识别源语言，请手动指定", detection=detection)
        else:
            # Legacy hint path (prepare_task tool and earlier callers): keep a
            # compatible supplied label, otherwise trust the document.
            declared_source = language(spec.get("source_language") or langs.get("source") or "")
            if declared_source == AUTO_LANGUAGE:
                declared_source = ""
            hint_usable = bool(declared_source) and not script_conflict(declared_source, combined)
            if hint_usable and (detection["uncertain"]
                                or language_family(declared_source) == language_family(detection["language"])):
                source_language = declared_source
            elif detection["language"]:
                source_language = detection["language"]
            elif declared_source:
                source_language = declared_source
            else:
                raise LanguageDetectionError(
                    detection["detail"] or "无法自动识别源语言，请手动指定", detection=detection)
        explicit_target = spec.get("target_language_explicit") is True
        if explicit_target:
            target_language = language(spec.get("target_language") or "")
            if not target_language or target_language == AUTO_LANGUAGE:
                raise ValueError("已声明显式语言方向，但没有提供有效的目标语言")
        else:
            # Workspace default: source comes from the document; target is
            # always zh-CN unless the user explicitly requested otherwise.
            target_language = "简体中文"
        config["languages"] = {"source": source_language, "target": target_language}
        source_hash = aggregate_sha256(asset_hashes)
        config["source"] = {
            "files": converted,
            "encoding": "UTF-8",
            "identity": {
                "schema": BOOK_IDENTITY_SCHEMA,
                "sha256": source_hash,
                "assets_sha256": asset_hashes,
                "text_sha256": text_sha256(contents),
            },
        }
        config["output"] = {"directory": (old.get("output") or {}).get("directory") or "译文",
                            "filename_rule": spec.get("output_name") or f"原文件名.{output_suffix(target_language)}.txt",
                            "overwrite_existing": False}
        trans = dict(old.get("translation") or {})
        if spec.get("style"):
            trans["style"] = spec["style"]
        trans.setdefault("style", "忠实、自然，保持原作叙事视角与人物口吻")
        aliases = {"proper_nouns": "专名译名一致", "names": "人名译名一致", "places": "地名译名一致",
                   "magical_terms": "魔法术语译名一致", "cultural_references": "文化信息与典故"}
        if isinstance(trans.get("preserve"), list):
            trans["preserve"] = [aliases.get(value, value) for value in trans["preserve"]]
        if "requirements" in spec:
            trans["special_requirements"] = spec["requirements"]
        config["translation"] = trans
        config["context"] = old.get("context") or {"background": "", "character_voice": []}
        if spec.get("native_defaults") is True and not old.get("chunking"):
            source_family = language_family(source_language)
            max_chars = 3200 if source_family in ("han", "japanese", "korean") else 4000
            chunk_defaults = {
                "segment_mode": "auto",
                "max_source_chars": max_chars,
                "max_segments": 20,
                "context_segments": 3,
                "parallel_requests": 1,
            }
        else:
            chunk_defaults = {"segment_mode": "auto", "max_source_chars": 4000, "max_segments": 20,
                              "context_segments": 3, "parallel_requests": 2}
        config["chunking"] = {**chunk_defaults, **(old.get("chunking") or {})}
        choice = root / ".translation-options.json"
        options = json.loads(choice.read_text()) if choice.exists() else {}
        mode = spec.get("glossary_mode") or options.get("mode") or (old.get("glossary") or {}).get("mode") or "none"
        config["glossary"] = {"mode": mode, "file": "术语表.json"}
        if mode == "auto" and spec.get("glossary_strategy"):
            if spec["glossary_strategy"] not in {"candidates-v1", "fulltext"}:
                raise ValueError("未知的译名预扫策略")
            config["glossary"]["strategy"] = spec["glossary_strategy"]
        if mode == "fixed":
            raw = spec.get("glossary_file") or options.get("file") or (old.get("glossary") or {}).get("file") or "术语表.json"
            src = Path(raw).expanduser(); src = root / src if not src.is_absolute() else src
            entries = read_glossary(src)
            atomic(inside(root, "术语表.json"), json.dumps({"entries": entries}, ensure_ascii=False, indent=2) + "\n")
        config["progress"] = {"file": "翻译进度.json"}
        validate(root, config)
        task_id = task_identity(root, source_hash)
        atomic(file, yaml.safe_dump(config, allow_unicode=True, sort_keys=False))
        progress = root / "翻译进度.json"
        if not progress.exists():
            atomic(progress, json.dumps({"schema_version": 3, "protocol": "segment-markers-v1", "status": "not_started",
                                         "total_segments": None, "next_segment": 1, "completed_segments": 0, "completed_ranges": [], "updated_at": None}))
        return {"status": "ready", "task_root": str(root), "source_files": converted,
                "book_hash": source_hash, "task_id": task_id,
                "source_language": source_language, "target_language": target_language,
                "source_language_explicit": source_selection_supplied and source_explicit,
                "language_direction": ("manual" if source_explicit else "auto") + "→" + target_language,
                "glossary_mode": mode,
                "next": "在当前会话切换 Build 并发送开始翻译；任务根目录不变"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--task-root", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        root = Path(args.task_root).resolve()
        if args.check:
            validate(root, yaml.safe_load((root / "翻译任务.yaml").read_text(encoding="utf-8")))
            result = {"status": "ready", "task_root": str(root)}
        else:
            import sys
            result = prepare(root, json.load(sys.stdin))
        print(json.dumps(result, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)[:500]}, ensure_ascii=False))
        raise SystemExit(1)
