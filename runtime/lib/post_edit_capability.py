"""One capability definition for the automated post-edit features.

Automatic terminology review and the AI human-annotation review both compare a
translation against its source and both emit revised Chinese text.  In this
release they are supported **only** for Simplified and Traditional Chinese
targets.  That limit is a property of the features, not of the interface, so it
lives here once and every entry point asks the same question:

* the QML card and dialogs,
* the automatic "prepare review after translation" path,
* the ``PostEditController`` slots that spawn the worker,
* the worker/CLI itself.

Nothing in this module reads a display label: the answer comes from the task's
translation target, resolved through the same language catalog the rest of the
application uses.  Ordinary translation, reading, bookmarks, margin notes and a
person editing a passage by hand are deliberately **not** gated here.
"""
from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP

import language_catalog

CONTEXT = "post_edit_capability"

# Stable capability identifiers.  These strings never reach the interface.
ANNOTATION_REVIEW = "annotation_review"
TERM_REVIEW = "term_review"

# Stable worker/UI message codes. The target name stays a parameter so an
# already-visible refusal can be re-rendered after an interface-locale switch.
UNSUPPORTED_CODE = "postEditUnsupportedTarget"
UNKNOWN_CODE = "postEditUnknownTarget"

# The only translation targets the automated review can revise.
SUPPORTED_TARGET_IDS = ("zh-Hans", "zh-Hant")

UNSUPPORTED_REASON = QT_TRANSLATE_NOOP(
    "post_edit_capability",
    "自动校订目前只支持简体中文和繁体中文译本；本书目标是%1，仍可正常翻译、阅读和手动修改译文。")
UNKNOWN_TARGET_REASON = QT_TRANSLATE_NOOP(
    "post_edit_capability",
    "尚不能确认本书的翻译目标语言，自动校订已停用；请先完成或重新准备译本。")


class UnsupportedTarget(ValueError):
    """Raised before any provider request when a capability is unavailable."""

    def __init__(self, message: str, code: str = UNSUPPORTED_CODE) -> None:
        super().__init__(message)
        self.code = code


def target_id(raw_target) -> str:
    """Stable language ID of a translation target, ``""`` when unknown."""
    return language_catalog.language_id(raw_target) or ""


def supports(raw_target, capability: str = ANNOTATION_REVIEW) -> bool:
    """Whether the automated review may run for this translation target."""
    if capability not in (ANNOTATION_REVIEW, TERM_REVIEW):
        return False
    identifier = target_id(raw_target)
    return bool(identifier) and identifier in SUPPORTED_TARGET_IDS


def reason_text(raw_target) -> str:
    """The localized explanation for a blocked target."""
    from PySide6.QtCore import QCoreApplication
    if not target_id(raw_target):
        return QCoreApplication.translate(CONTEXT, UNKNOWN_TARGET_REASON)
    display = _target_display(raw_target)
    template = QCoreApplication.translate(CONTEXT, UNSUPPORTED_REASON)
    return template.replace("%1", display)


def message_code(raw_target) -> str:
    """Stable localized-status code for a refused target."""
    return UNSUPPORTED_CODE if target_id(raw_target) else UNKNOWN_CODE


def message_args(raw_target) -> tuple[str, ...]:
    """Stable parameters for :func:`message_code`."""
    return (_target_display(raw_target),) if target_id(raw_target) else ()


def _target_display(raw_target) -> str:
    """A readable name for the blocked target, preferring its native name."""
    entry = language_catalog.find(raw_target)
    if entry:
        return entry.native_name
    return str(raw_target or "").strip()


def require(raw_target, capability: str = ANNOTATION_REVIEW) -> None:
    """Raise :class:`UnsupportedTarget` unless the capability is available."""
    if not supports(raw_target, capability):
        raise UnsupportedTarget(reason_text(raw_target), message_code(raw_target))
