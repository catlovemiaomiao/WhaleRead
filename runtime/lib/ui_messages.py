"""Stable user-visible application messages, rendered at the UI boundary.

A user-visible state is stored as a **stable code** plus **parameters**, and is
turned into text only when a property getter is read.  Consequences:

* switching the interface language re-renders already-visible state without
  touching the logic that produced it;
* no branch anywhere compares rendered text, in Chinese or in English;
* raw diagnostics stay in their own field and are never translated into
  something that pretends to be original evidence.

The translation source sentences are string literals listed in ``_SOURCES`` so
``lupdate`` can extract them; :func:`render` looks them up at call time.  This
is deliberately **not** a second translation system: it is a thin code → source
literal table that the same Qt ``.ts``/``.qm`` catalogue owns.

Legacy compatibility is explicit and conservative:

* :func:`legacy_code` maps only the exact Chinese sentences earlier builds
  stored, and returns ``""`` for anything else;
* an unknown legacy message is shown as a localized *summary* with its original
  detail preserved verbatim — its category is never guessed.
"""
from __future__ import annotations

import re
from typing import NamedTuple

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication

# One context for every application-state message.
CONTEXT = "ui_messages"


class MessageCode:
    """Stable identifiers.  These strings never reach the user interface."""

    # Source inspection, task preparation and direction
    READY = "ready"
    READY_WITH_SOURCE = "readyWithSource"
    READY_SELECT_SOURCE = "readySelectSource"
    RESUMABLE_TASK = "resumableTask"
    RESUMABLE_PROGRESS = "resumableProgress"
    FINISHED_TASK = "finishedTask"
    FINISHED_READY = "finishedReady"
    NEW_EDITION_READY = "newEditionReady"
    CANNOT_START = "cannotStart"
    SOURCE_MISSING = "sourceMissing"
    SOURCE_CHANGED = "sourceChanged"
    DIRECTION_LOCKED = "directionLocked"
    CHECKPOINT_INCOMPATIBLE = "checkpointIncompatible"
    PREPARE_FAILED = "prepareFailed"

    # Model and service availability
    MODEL_LOCAL_READY = "modelLocalReady"
    MODEL_LOCAL_NOT_LOADED = "modelLocalNotLoaded"
    MODEL_LOCAL_FILE_MISSING = "modelLocalFileMissing"
    MODEL_REMOTE_READY = "modelRemoteReady"
    MODEL_REMOTE_CREDENTIALS_MISSING = "modelRemoteCredentialsMissing"
    MODEL_PROFILE_LOCAL_1_8_LABEL = "modelProfileLocal18Label"
    MODEL_PROFILE_LOCAL_1_8_SHORT = "modelProfileLocal18Short"
    MODEL_PROFILE_LOCAL_1_8_DETAIL = "modelProfileLocal18Detail"
    MODEL_PROFILE_LOCAL_7_LABEL = "modelProfileLocal7Label"
    MODEL_PROFILE_LOCAL_7_SHORT = "modelProfileLocal7Short"
    MODEL_PROFILE_LOCAL_7_DETAIL = "modelProfileLocal7Detail"
    MODEL_PROFILE_DGX_LABEL = "modelProfileDgxLabel"
    MODEL_PROFILE_DGX_SHORT = "modelProfileDgxShort"
    MODEL_PROFILE_DGX_DETAIL = "modelProfileDgxDetail"

    # Translation progress
    STARTING_MODEL = "startingModel"
    MODEL_WARMUP = "modelWarmup"
    TRANSLATING = "translating"
    TRANSLATING_RANGE = "translatingRange"
    STRUCTURE_RETRY = "structureRetry"
    SPLIT_RANGE = "splitRange"
    CHUNK_SAVED = "chunkSaved"
    GLOSSARY_INDEXED = "glossaryIndexed"
    GLOSSARY_INDEXED_LIMITED = "glossaryIndexedLimited"
    GLOSSARY_INDEXED_FULLTEXT = "glossaryIndexedFulltext"
    GLOSSARY_SCANNING = "glossaryScanning"
    GLOSSARY_PROGRESS = "glossaryProgress"
    GLOSSARY_RETRY = "glossaryRetry"
    GLOSSARY_SKIPPED = "glossarySkipped"
    GLOSSARY_READY = "glossaryReady"
    GLOSSARY_READY_PENDING = "glossaryReadyPending"
    GLOSSARY_PAUSED = "glossaryPaused"
    RESUMING = "resuming"
    PAUSING = "pausing"
    PAUSED = "paused"
    PAUSE_REQUESTED = "pauseRequested"
    PAUSE_SAVED = "pauseSaved"
    PAUSE_EPUB_PUBLISHED = "pauseEpubPublished"
    TRANSLATION_STARTED = "translationStarted"
    COMPLETED = "completed"
    COMPLETED_DETAIL = "completedDetail"
    PARTIAL = "partial"
    RANGE_COMPLETED = "rangeCompleted"
    PARTIAL_FAILURES = "partialFailures"

    # Failures
    TRANSLATION_STOPPED = "translationStopped"
    PROCESS_NOT_STARTED = "processNotStarted"
    CREDENTIALS_MISSING = "credentialsMissing"
    REQUEST_TIMEOUT = "requestTimeout"
    HTTP_FAILED = "httpFailed"
    MODEL_FAILED = "modelFailed"
    VALIDATION_FAILED = "validationFailed"
    SAFETY_PAUSED = "safetyPaused"
    FILE_MISSING = "fileMissing"
    FILE_ACCESS_REQUIRED = "fileAccessRequired"
    UNKNOWN_ERROR = "unknownError"
    CACHE_COUNT_FAILED = "cacheCountFailed"
    CACHE_CLEAR_FAILED = "cacheClearFailed"
    READING_UNAVAILABLE = "readingUnavailable"
    READING_SOURCE_UNAVAILABLE = "readingSourceUnavailable"
    READING_TRANSLATION_UNAVAILABLE = "readingTranslationUnavailable"
    EPUB_OPEN_FAILED = "epubOpenFailed"
    EPUB_EXPORT_FAILED = "epubExportFailed"
    BILINGUAL_STANDALONE = "bilingualStandalone"
    BILINGUAL_WAITING = "bilingualWaiting"
    BILINGUAL_NO_TRANSLATION = "bilingualNoTranslation"
    READING_FILE_MOVED = "readingFileMoved"
    READING_EMPTY = "readingEmpty"

    # Reading, shelf and cache
    CACHE_NOT_COUNTED = "cacheNotCounted"
    CACHE_COUNTING = "cacheCounting"
    CACHE_CLEARING = "cacheClearing"
    CACHE_SUMMARY = "cacheSummary"
    CACHE_SUMMARY_ACTIVE = "cacheSummaryActive"
    CACHE_SUMMARY_UNKNOWN = "cacheSummaryUnknown"
    CACHE_SUMMARY_ACTIVE_UNKNOWN = "cacheSummaryActiveUnknown"
    CACHE_CLEARED = "cacheCleared"
    CACHE_CLEARED_EMPTY = "cacheClearedEmpty"
    EPUB_ORIGINAL_READING = "epubOriginalReading"
    TXT_READING = "txtReading"
    TRANSLATED_PERCENT = "translatedPercent"
    SEARCH_EMPTY = "searchEmpty"
    SEARCH_RESULTS = "searchResults"
    SEARCH_POSITION = "searchPosition"
    BOOKMARK_PARAGRAPH = "bookmarkParagraph"
    EPUB_BOOKMARK = "epubBookmark"

    # Source, route and appearance projections
    SOURCE_FORMATS = "sourceFormats"
    SOURCE_INSPECTED = "sourceInspected"
    SOURCE_ROUTE = "sourceRoute"
    SOURCE_ROUTE_AUTO = "sourceRouteAuto"
    LANGUAGE_DIRECTION = "languageDirection"
    LANGUAGE_DIRECTION_AUTO = "languageDirectionAuto"
    LANGUAGE_DIRECTION_DETECTED = "languageDirectionDetected"
    LANGUAGE_SCRIPT_WARNING = "languageScriptWarning"
    LANGUAGE_UNCERTAIN = "languageUncertain"
    LANGUAGE_PENDING = "languagePending"
    TARGET_LANGUAGE_INVALID = "targetLanguageInvalid"
    SOURCE_LANGUAGE_INVALID = "sourceLanguageInvalid"
    DIRECTION_CHECKPOINT_LOCKED = "directionCheckpointLocked"
    SPEED_CALCULATING = "speedCalculating"
    SPEED_RATE = "speedRate"
    PROBE_HELP = "probeHelp"
    PROBE_MODEL_CHANGED = "probeModelChanged"
    PROBE_ENTER_SOURCE = "probeEnterSource"
    PROBE_TOO_LONG = "probeTooLong"
    PROBE_RUNNING = "probeRunning"
    PROBE_FAILED = "probeFailed"
    PROBE_COMPLETE = "probeComplete"
    GLOSSARY_NONE = "glossaryNone"
    GLOSSARY_PREPARE = "glossaryPrepare"
    READER_EMPTY_TITLE = "readerEmptyTitle"
    FONT_SONGTI = "fontSongti"
    FONT_KAITI = "fontKaiti"
    FONT_FANGSONG = "fontFangsong"
    FONT_PINGFANG = "fontPingfang"
    FONT_HIRAGINO = "fontHiragino"
    FONT_GEORGIA = "fontGeorgia"
    DIALOG_PICK_SOURCE = "dialogPickSource"
    DIALOG_SOURCE_FILTER = "dialogSourceFilter"
    DIALOG_ADD_BOOK = "dialogAddBook"
    DIALOG_READING_FILTER = "dialogReadingFilter"
    DIALOG_PICK_GLOSSARY = "dialogPickGlossary"
    DIALOG_GLOSSARY_FILTER = "dialogGlossaryFilter"
    DIALOG_OPEN_BOOK = "dialogOpenBook"
    DIALOG_RESEARCH_PDF = "dialogResearchPdf"

    # Post-edit and research
    POST_EDIT_IDLE = "postEditIdle"
    POST_EDIT_REMEMBERED = "postEditRemembered"
    POST_EDIT_PREFERENCE_UNSAVED = "postEditPreferenceUnsaved"
    POST_EDIT_SELECTION_CHANGED = "postEditSelectionChanged"
    POST_EDIT_ANNOTATION_UNSAVED = "postEditAnnotationUnsaved"
    POST_EDIT_DRAFT_SAVED = "postEditDraftSaved"
    POST_EDIT_NEED_DRAFT = "postEditNeedDraft"
    POST_EDIT_CHECKING = "postEditChecking"
    POST_EDIT_REPREPARE = "postEditReprepare"
    POST_EDIT_SUMMARY = "postEditSummary"
    POST_EDIT_RECORD_UNREADABLE = "postEditRecordUnreadable"
    POST_EDIT_SELECT_NAMES = "postEditSelectNames"
    POST_EDIT_SELECT_EDITS = "postEditSelectEdits"
    POST_EDIT_WAIT_FINISH = "postEditWaitFinish"
    POST_EDIT_UNSUPPORTED_TARGET = "postEditUnsupportedTarget"
    POST_EDIT_UNKNOWN_TARGET = "postEditUnknownTarget"
    POST_EDIT_AUDITING = "postEditAuditing"
    POST_EDIT_PREPARING = "postEditPreparing"
    POST_EDIT_PAUSED = "postEditPaused"
    POST_EDIT_ENDED = "postEditEnded"
    POST_EDIT_EXPORTED = "postEditExported"
    POST_EDIT_ANNOTATIONS_DONE = "postEditAnnotationsDone"
    POST_EDIT_PROCESS_FAILED = "postEditProcessFailed"
    POST_EDIT_APPLIED = "postEditApplied"
    POST_EDIT_REVIEWER_CHANGED = "postEditReviewerChanged"
    POST_EDIT_ANNOTATE_CURRENT = "postEditAnnotateCurrent"
    POST_EDIT_ANNOTATION_SAVED = "postEditAnnotationSaved"
    POST_EDIT_EDIT_SAVED = "postEditEditSaved"
    POST_EDIT_EDIT_UNSAVED = "postEditEditUnsaved"
    POST_EDIT_SUMMARY_UNCERTAIN = "postEditSummaryUncertain"
    POST_EDIT_SUMMARY_SKIP = "postEditSummarySkip"
    POST_EDIT_SUMMARY_USER = "postEditSummaryUser"
    POST_EDIT_SUMMARY_USE = "postEditSummaryUse"
    POST_EDIT_SUMMARY_USE_NO_COUNT = "postEditSummaryUseNoCount"
    POST_EDIT_SUMMARY_PENDING = "postEditSummaryPending"
    POST_EDIT_COVERAGE = "postEditCoverage"
    POST_EDIT_FAILURES = "postEditFailures"
    POST_EDIT_ALREADY_APPLIED = "postEditAlreadyApplied"
    POST_EDIT_PREVIEW_READY = "postEditPreviewReady"
    POST_EDIT_PREVIEW_EMPTY = "postEditPreviewEmpty"
    POST_EDIT_PREVIEW_UNSAFE = "postEditPreviewUnsafe"
    POST_EDIT_RECALCULATING = "postEditRecalculating"
    POST_EDIT_REVIEWING_ANNOTATIONS = "postEditReviewingAnnotations"
    POST_EDIT_BUILDING_PREVIEW = "postEditBuildingPreview"
    POST_EDIT_PUBLISHING = "postEditPublishing"
    POST_EDIT_PUBLISHING_ANNOTATION = "postEditPublishingAnnotation"
    POST_EDIT_RECONCILING = "postEditReconciling"
    POST_EDIT_AUDIT_SCOPE = "postEditAuditScope"
    POST_EDIT_AUDIT_PROGRESS = "postEditAuditProgress"
    POST_EDIT_PREVIEW_PROGRESS = "postEditPreviewProgress"
    POST_EDIT_ANNOTATION_PROGRESS = "postEditAnnotationProgress"
    POST_EDIT_GATE_PROGRESS = "postEditGateProgress"
    POST_EDIT_VERDICT_KEEP = "postEditVerdictKeep"
    POST_EDIT_VERDICT_ISSUE = "postEditVerdictIssue"
    POST_EDIT_VERDICT_UNCERTAIN = "postEditVerdictUncertain"
    POST_EDIT_OLD_REVIEWER = "postEditOldReviewer"
    POST_EDIT_REVIEW_CHANGED = "postEditReviewChanged"
    POST_EDIT_REVIEW_MISSING = "postEditReviewMissing"
    POST_EDIT_PREVIOUS_REVIEW = "postEditPreviousReview"
    RESEARCH_UPGRADING = "researchUpgrading"
    RESEARCH_SOURCE_REPAIRED = "researchSourceRepaired"
    RESEARCH_LAYOUT_RECLASSIFIED = "researchLayoutReclassified"
    RESEARCH_RULES_CHANGED = "researchRulesChanged"
    RESEARCH_NEED_APPROVAL = "researchNeedApproval"
    RESEARCH_SOURCE_SAVED = "researchSourceSaved"
    RESEARCH_TERMS_UPDATED = "researchTermsUpdated"
    RESEARCH_RULES_CONFIRMED = "researchRulesConfirmed"
    RESEARCH_EXPORTED = "researchExported"
    RESEARCH_APPROVED = "researchApproved"
    RESEARCH_CONFIRMED = "researchConfirmed"
    ACTION_INCOMPLETE = "actionIncomplete"
    RESEARCH_IDLE = "researchIdle"
    RESEARCH_READY = "researchReady"
    RESEARCH_PAGE_READY = "researchPageReady"
    RESEARCH_RESTORING = "researchRestoring"
    RESEARCH_RESTORE_FAILED = "researchRestoreFailed"
    RESEARCH_WORKING = "researchWorking"
    RESEARCH_BATCH_PROGRESS = "researchBatchProgress"
    RESEARCH_BATCH_DONE = "researchBatchDone"
    RESEARCH_BATCH_PARTIAL = "researchBatchPartial"
    RESEARCH_FAILED = "researchFailed"
    RESEARCH_PAUSED = "researchPaused"
    RESEARCH_TITLE = "researchTitle"
    RESEARCH_ORIGIN_MIXED = "researchOriginMixed"
    RESEARCH_ORIGIN_NATIVE = "researchOriginNative"
    RESEARCH_ORIGIN_SCAN = "researchOriginScan"
    RESEARCH_PAGE_RESTORED = "researchPageRestored"
    RESEARCH_CONNECTING = "researchConnecting"
    RESEARCH_ANALYZED = "researchAnalyzed"
    RESEARCH_TRANSLATING_BLOCK = "researchTranslatingBlock"
    RESEARCH_RETRYING_BLOCK = "researchRetryingBlock"
    RESEARCH_TRANSLATED = "researchTranslated"
    RESEARCH_TRANSLATED_PARTIAL = "researchTranslatedPartial"
    RESEARCH_DOCUMENT_RESTORED = "researchDocumentRestored"
    RESEARCH_UPGRADED = "researchUpgraded"
    RESEARCH_POSITION_READY = "researchPositionReady"

    # EPUB reading state
    EPUB_STATE_TRANSLATED = "epubTranslated"
    EPUB_STATE_PARTIAL = "epubPartial"
    EPUB_STATE_PENDING = "epubPending"
    EPUB_DEFAULT_SECTION = "epubDefaultSection"


class Message(NamedTuple):
    """A stable code plus its parameters and an untranslated diagnostic."""

    code: str
    args: tuple = ()
    detail: str = ""

    def with_detail(self, detail: str) -> "Message":
        return Message(self.code, self.args, str(detail or ""))

    def with_args(self, *args) -> "Message":
        return Message(self.code, tuple(args), self.detail)


# Display summary used when a code is unknown (e.g. a newer worker).
UNKNOWN_SUMMARY = QT_TRANSLATE_NOOP("ui_messages", "操作未完成")

# code -> static source sentence.  Every value is a literal so lupdate can see
# it; placeholders are Qt %1..%n and never string-concatenated fragments.
_SOURCES: dict[str, str] = {
    MessageCode.READY: QT_TRANSLATE_NOOP("ui_messages", "准备就绪"),
    MessageCode.READY_WITH_SOURCE: QT_TRANSLATE_NOOP("ui_messages", "原文已就绪，点击开始后交给 %1。"),
    MessageCode.READY_SELECT_SOURCE:
        QT_TRANSLATE_NOOP("ui_messages", "选择一本书，翻译会交给 %1。"),
    MessageCode.RESUMABLE_TASK: QT_TRANSLATE_NOOP("ui_messages", "发现可继续的任务"),
    MessageCode.RESUMABLE_PROGRESS:
        QT_TRANSLATE_NOOP("ui_messages", "已安全保存 %1%，点击继续即可从断点恢复。"),
    MessageCode.FINISHED_TASK: QT_TRANSLATE_NOOP("ui_messages", "译本已完成"),
    MessageCode.FINISHED_READY:
        QT_TRANSLATE_NOOP("ui_messages", "可直接阅读；需要重译时再读取任务文件。"),
    MessageCode.NEW_EDITION_READY: QT_TRANSLATE_NOOP("ui_messages", "新译本已就绪"),
    MessageCode.CANNOT_START: QT_TRANSLATE_NOOP("ui_messages", "无法开始"),
    MessageCode.SOURCE_MISSING: QT_TRANSLATE_NOOP("ui_messages", "找不到原文文件"),
    MessageCode.SOURCE_CHANGED: QT_TRANSLATE_NOOP("ui_messages", "原文内容与导入时不一致"),
    MessageCode.DIRECTION_LOCKED: QT_TRANSLATE_NOOP("ui_messages", "翻译方向已锁定"),
    MessageCode.CHECKPOINT_INCOMPATIBLE:
        QT_TRANSLATE_NOOP("ui_messages", "已有断点与当前任务设置不兼容"),
    MessageCode.PREPARE_FAILED: QT_TRANSLATE_NOOP("ui_messages", "任务准备未完成"),

    MessageCode.MODEL_LOCAL_READY: QT_TRANSLATE_NOOP("ui_messages", "本地模型已就绪 · 正文不会上传"),
    MessageCode.MODEL_LOCAL_NOT_LOADED:
        QT_TRANSLATE_NOOP("ui_messages", "请启动 Ollama 并载入此模型"),
    MessageCode.MODEL_LOCAL_FILE_MISSING:
        QT_TRANSLATE_NOOP("ui_messages", "未找到本地 GGUF 模型文件"),
    MessageCode.MODEL_REMOTE_READY: QT_TRANSLATE_NOOP("ui_messages", "API 已配置"),
    MessageCode.MODEL_REMOTE_CREDENTIALS_MISSING:
        QT_TRANSLATE_NOOP("ui_messages", "需要配置 API 凭据"),
    MessageCode.MODEL_PROFILE_LOCAL_1_8_LABEL:
        QT_TRANSLATE_NOOP("ui_messages", "本机 · Hy-MT2 1.8B"),
    MessageCode.MODEL_PROFILE_LOCAL_1_8_SHORT:
        QT_TRANSLATE_NOOP("ui_messages", "Hy‑MT2 1.8B · 本机"),
    MessageCode.MODEL_PROFILE_LOCAL_1_8_DETAIL:
        QT_TRANSLATE_NOOP("ui_messages", "Q8_0 · 约 1.78 GiB · 文本不离开本机"),
    MessageCode.MODEL_PROFILE_LOCAL_7_LABEL:
        QT_TRANSLATE_NOOP("ui_messages", "本机 · Hy-MT2 7B"),
    MessageCode.MODEL_PROFILE_LOCAL_7_SHORT:
        QT_TRANSLATE_NOOP("ui_messages", "Hy‑MT2 7B · 本机"),
    MessageCode.MODEL_PROFILE_LOCAL_7_DETAIL:
        QT_TRANSLATE_NOOP("ui_messages", "Q4_K_M · 约 4.31 GiB · 文本不离开本机"),
    MessageCode.MODEL_PROFILE_DGX_LABEL:
        QT_TRANSLATE_NOOP("ui_messages", "网络 API · DGX"),
    MessageCode.MODEL_PROFILE_DGX_SHORT:
        QT_TRANSLATE_NOOP("ui_messages", "Hy‑MT2 30B · DGX"),
    MessageCode.MODEL_PROFILE_DGX_DETAIL:
        QT_TRANSLATE_NOOP("ui_messages", "30B Q6 · 使用现有安全凭据 · 需要网络"),

    MessageCode.STARTING_MODEL: QT_TRANSLATE_NOOP("ui_messages", "正在启动 %1"),
    MessageCode.MODEL_WARMUP:
        QT_TRANSLATE_NOOP("ui_messages", "首次响应可能包含模型热身时间。"),
    MessageCode.TRANSLATING: QT_TRANSLATE_NOOP("ui_messages", "模型正在翻译"),
    MessageCode.TRANSLATING_RANGE:
        QT_TRANSLATE_NOOP("ui_messages", "正在翻译第 %1–%2 段；完成校验后立即保存"),
    MessageCode.STRUCTURE_RETRY:
        QT_TRANSLATE_NOOP("ui_messages", "第 %1–%2 段正在进行结构化重试"),
    MessageCode.SPLIT_RANGE:
        QT_TRANSLATE_NOOP("ui_messages", "第 %1–%2 段映射不稳，已自动拆小继续"),
    MessageCode.CHUNK_SAVED: QT_TRANSLATE_NOOP("ui_messages", "第 %1–%2 段已校验并安全保存"),
    MessageCode.GLOSSARY_INDEXED:
        QT_TRANSLATE_NOOP("ui_messages", "全书已检索，精选 %1 个候选交给翻译模型核对"),
    MessageCode.GLOSSARY_INDEXED_LIMITED:
        QT_TRANSLATE_NOOP("ui_messages", "全书已检索，精选 %1 个候选交给翻译模型核对（另有 %2 个低频候选未纳入）"),
    MessageCode.GLOSSARY_INDEXED_FULLTEXT:
        QT_TRANSLATE_NOOP("ui_messages", "此语言不适用大小写筛选，翻译模型将分批阅读全书提取译名，耗时较长。"),
    MessageCode.GLOSSARY_SCANNING: QT_TRANSLATE_NOOP("ui_messages", "正在整理全书译名"),
    MessageCode.GLOSSARY_PROGRESS:
        QT_TRANSLATE_NOOP("ui_messages", "译名预扫 %1 / %2 批 · 完成后自动开始正文"),
    MessageCode.GLOSSARY_RETRY:
        QT_TRANSLATE_NOOP("ui_messages", "正在单独核对译名：%1；其他有效译名已保存"),
    MessageCode.GLOSSARY_SKIPPED:
        QT_TRANSLATE_NOOP("ui_messages", "%1 暂列待核对，继续整理其他译名"),
    MessageCode.GLOSSARY_READY:
        QT_TRANSLATE_NOOP("ui_messages", "已锁定 %1 个译名 · 只向相关正文注入"),
    MessageCode.GLOSSARY_READY_PENDING:
        QT_TRANSLATE_NOOP("ui_messages", "已锁定 %1 个译名 · 只向相关正文注入；%2 项待核对，未强制采用"),
    MessageCode.GLOSSARY_PAUSED:
        QT_TRANSLATE_NOOP("ui_messages", "译名预扫可继续"),
    MessageCode.RESUMING: QT_TRANSLATE_NOOP("ui_messages", "从上次已经安全保存的位置继续。"),
    MessageCode.PAUSING: QT_TRANSLATE_NOOP("ui_messages", "正在安全暂停…"),
    MessageCode.PAUSED: QT_TRANSLATE_NOOP("ui_messages", "已暂停"),
    MessageCode.PAUSE_REQUESTED:
        QT_TRANSLATE_NOOP("ui_messages", "已完成的译块和预扫批次均已落盘；未完成的请求会在续跑时重试。"),
    MessageCode.PAUSE_SAVED:
        QT_TRANSLATE_NOOP("ui_messages", "已完成内容已安全保存，可从断点继续。"),
    MessageCode.PAUSE_EPUB_PUBLISHED:
        QT_TRANSLATE_NOOP("ui_messages", "完成的译块已安全保存；EPUB 阅读版已合并到最新可验证断点。"),
    MessageCode.TRANSLATION_STARTED:
        QT_TRANSLATE_NOOP("ui_messages", "模型已连接；译块通过校验后立即保存。"),
    MessageCode.COMPLETED: QT_TRANSLATE_NOOP("ui_messages", "翻译完成"),
    MessageCode.COMPLETED_DETAIL:
        QT_TRANSLATE_NOOP("ui_messages", "全部段落已通过完整性检查，译文已落盘。"),
    MessageCode.PARTIAL: QT_TRANSLATE_NOOP("ui_messages", "部分完成"),
    MessageCode.RANGE_COMPLETED:
        QT_TRANSLATE_NOOP("ui_messages", "所选范围已完成；已完成内容均已保存。"),
    MessageCode.PARTIAL_FAILURES:
        QT_TRANSLATE_NOOP("ui_messages", "%1 个译块未完成；已完成内容均已保存。"),

    MessageCode.TRANSLATION_STOPPED: QT_TRANSLATE_NOOP("ui_messages", "翻译停止"),
    MessageCode.PROCESS_NOT_STARTED: QT_TRANSLATE_NOOP("ui_messages", "翻译进程未能启动"),
    MessageCode.CREDENTIALS_MISSING: QT_TRANSLATE_NOOP("ui_messages", "缺少凭据或配置"),
    MessageCode.REQUEST_TIMEOUT: QT_TRANSLATE_NOOP("ui_messages", "请求超时"),
    MessageCode.HTTP_FAILED: QT_TRANSLATE_NOOP("ui_messages", "服务返回错误"),
    MessageCode.MODEL_FAILED: QT_TRANSLATE_NOOP("ui_messages", "模型返回不可用结果"),
    MessageCode.VALIDATION_FAILED: QT_TRANSLATE_NOOP("ui_messages", "语言或段落校验未通过"),
    MessageCode.SAFETY_PAUSED: QT_TRANSLATE_NOOP("ui_messages", "已按安全检查暂停"),
    MessageCode.FILE_MISSING: QT_TRANSLATE_NOOP("ui_messages", "文件不存在"),
    MessageCode.FILE_ACCESS_REQUIRED:
        QT_TRANSLATE_NOOP("ui_messages", "文件授权已失效，请重新选择文件或任务文件夹后再试。"),
    MessageCode.UNKNOWN_ERROR: UNKNOWN_SUMMARY,
    MessageCode.CACHE_COUNT_FAILED: QT_TRANSLATE_NOOP("ui_messages", "缓存统计失败"),
    MessageCode.CACHE_CLEAR_FAILED: QT_TRANSLATE_NOOP("ui_messages", "缓存清理失败"),
    MessageCode.READING_UNAVAILABLE: QT_TRANSLATE_NOOP("ui_messages", "暂时无法打开这本书"),
    MessageCode.READING_SOURCE_UNAVAILABLE:
        QT_TRANSLATE_NOOP("ui_messages", "原文对照暂不可用"),
    MessageCode.READING_TRANSLATION_UNAVAILABLE:
        QT_TRANSLATE_NOOP("ui_messages", "这本书还没有已保存译文"),
    MessageCode.EPUB_OPEN_FAILED: QT_TRANSLATE_NOOP("ui_messages", "无法打开 EPUB 对照"),
    MessageCode.EPUB_EXPORT_FAILED: QT_TRANSLATE_NOOP("ui_messages", "EPUB 阅读版生成失败"),
    MessageCode.BILINGUAL_STANDALONE:
        QT_TRANSLATE_NOOP("ui_messages", "独立 TXT 没有关联原文；可把原文放在旁边另一栏对照。"),
    MessageCode.BILINGUAL_WAITING:
        QT_TRANSLATE_NOOP("ui_messages", "原文对照暂不可用，正在等待可验证的段落映射。"),
    MessageCode.BILINGUAL_NO_TRANSLATION:
        QT_TRANSLATE_NOOP("ui_messages", "尚无可读译文；可切换到此书的翻译任务。"),
    MessageCode.READING_FILE_MOVED:
        QT_TRANSLATE_NOOP("ui_messages", "文件已移动，请重新打开。"),
    MessageCode.READING_EMPTY: QT_TRANSLATE_NOOP("ui_messages", "尚无可读内容。"),

    MessageCode.CACHE_NOT_COUNTED: QT_TRANSLATE_NOOP("ui_messages", "尚未统计"),
    MessageCode.CACHE_COUNTING: QT_TRANSLATE_NOOP("ui_messages", "正在统计…"),
    MessageCode.CACHE_CLEARING: QT_TRANSLATE_NOOP("ui_messages", "正在安全清理…"),
    MessageCode.CACHE_SUMMARY:
        QT_TRANSLATE_NOOP("ui_messages", "实际占用 %1 · %2 个可识别版本"),
    MessageCode.CACHE_SUMMARY_ACTIVE:
        QT_TRANSLATE_NOOP("ui_messages", "实际占用 %1 · %2 个可识别版本 · %3 个在用"),
    MessageCode.CACHE_SUMMARY_UNKNOWN:
        QT_TRANSLATE_NOOP("ui_messages", "实际占用 %1 · %2 个可识别版本 · %3 个来源待确认"),
    MessageCode.CACHE_SUMMARY_ACTIVE_UNKNOWN:
        QT_TRANSLATE_NOOP("ui_messages", "实际占用 %1 · %2 个可识别版本 · %3 个在用 · %4 个来源待确认"),
    MessageCode.CACHE_CLEARED:
        QT_TRANSLATE_NOOP("ui_messages", "已清理 %1；当前阅读保留 %2"),
    MessageCode.CACHE_CLEARED_EMPTY:
        QT_TRANSLATE_NOOP("ui_messages", "已清理 %1；缓存已清空"),
    MessageCode.EPUB_ORIGINAL_READING: QT_TRANSLATE_NOOP("ui_messages", "EPUB 原版阅读"),
    MessageCode.TXT_READING: QT_TRANSLATE_NOOP("ui_messages", "TXT 阅读"),
    MessageCode.TRANSLATED_PERCENT: QT_TRANSLATE_NOOP("ui_messages", "已译 %1%"),
    MessageCode.SEARCH_EMPTY: QT_TRANSLATE_NOOP("ui_messages", "没有匹配的段落"),
    MessageCode.SEARCH_RESULTS: QT_TRANSLATE_NOOP("ui_messages", "找到 %1 处匹配"),
    MessageCode.SEARCH_POSITION: QT_TRANSLATE_NOOP("ui_messages", "%1 / %2 段"),
    MessageCode.BOOKMARK_PARAGRAPH: QT_TRANSLATE_NOOP("ui_messages", "第 %1 段 · %2"),
    MessageCode.EPUB_BOOKMARK: QT_TRANSLATE_NOOP("ui_messages", "%1 · %2%"),

    MessageCode.SOURCE_FORMATS:
        QT_TRANSLATE_NOOP("ui_messages", "支持 TXT、Markdown、RTF、Word、EPUB 与带文字层的 PDF"),
    MessageCode.SOURCE_INSPECTED:
        QT_TRANSLATE_NOOP("ui_messages", "%1 · %2 字符 · 将自动保留章节与段落"),
    MessageCode.SOURCE_ROUTE: QT_TRANSLATE_NOOP("ui_messages", "%1 · %2 · 输出%3"),
    MessageCode.SOURCE_ROUTE_AUTO:
        QT_TRANSLATE_NOOP("ui_messages", "%1 · 自动识别语言 · 输出%2"),
    MessageCode.LANGUAGE_DIRECTION: QT_TRANSLATE_NOOP("ui_messages", "%1 → %2"),
    MessageCode.LANGUAGE_DIRECTION_AUTO: QT_TRANSLATE_NOOP("ui_messages", "自动识别 → %1"),
    MessageCode.LANGUAGE_DIRECTION_DETECTED:
        QT_TRANSLATE_NOOP("ui_messages", "%1 → %2（自动识别）"),
    MessageCode.LANGUAGE_SCRIPT_WARNING:
        QT_TRANSLATE_NOOP("ui_messages", "注意：正文看起来是%1，与你手动选择的%2文字系统不同；开始翻译前会再次校验。"),
    MessageCode.LANGUAGE_UNCERTAIN:
        QT_TRANSLATE_NOOP("ui_messages", "无法可靠自动识别源语言；请手动指定。"),
    MessageCode.LANGUAGE_PENDING: QT_TRANSLATE_NOOP("ui_messages", "待确认"),
    MessageCode.TARGET_LANGUAGE_INVALID:
        QT_TRANSLATE_NOOP("ui_messages", "请选择有效的目标语言"),
    MessageCode.SOURCE_LANGUAGE_INVALID:
        QT_TRANSLATE_NOOP("ui_messages", "请输入有效的源语言，或改回自动识别"),
    MessageCode.DIRECTION_CHECKPOINT_LOCKED:
        QT_TRANSLATE_NOOP("ui_messages", "这个译本已经有安全断点，不能原地修改语言方向。请点“新建另一译本”，按新方向重新开始；原译文和断点都会保留。"),
    MessageCode.SPEED_CALCULATING: QT_TRANSLATE_NOOP("ui_messages", "计算中"),
    MessageCode.SPEED_RATE: QT_TRANSLATE_NOOP("ui_messages", "%1 字符/分钟"),
    MessageCode.PROBE_HELP:
        QT_TRANSLATE_NOOP("ui_messages", "可在这里先试一小段，不会创建正式译本。"),
    MessageCode.PROBE_MODEL_CHANGED:
        QT_TRANSLATE_NOOP("ui_messages", "模型已切换，可以试译同一段进行比较。"),
    MessageCode.PROBE_ENTER_SOURCE: QT_TRANSLATE_NOOP("ui_messages", "先输入一小段原文。"),
    MessageCode.PROBE_TOO_LONG:
        QT_TRANSLATE_NOOP("ui_messages", "试译最多 3000 个字符；正式翻译会自动分块。"),
    MessageCode.PROBE_RUNNING:
        QT_TRANSLATE_NOOP("ui_messages", "%1 正在试译…首次载入会稍慢。"),
    MessageCode.PROBE_FAILED:
        QT_TRANSLATE_NOOP("ui_messages", "试译失败，请刷新模型状态后重试"),
    MessageCode.PROBE_COMPLETE: QT_TRANSLATE_NOOP("ui_messages", "试译完成 · %1 秒"),
    MessageCode.GLOSSARY_NONE: QT_TRANSLATE_NOOP("ui_messages", "不使用术语表"),
    MessageCode.GLOSSARY_PREPARE:
        QT_TRANSLATE_NOOP("ui_messages", "开始后先整理译名，再自动翻译正文"),
    MessageCode.READER_EMPTY_TITLE: QT_TRANSLATE_NOOP("ui_messages", "选择一本书或参考文件"),
    MessageCode.FONT_SONGTI: QT_TRANSLATE_NOOP("ui_messages", "书卷宋体"),
    MessageCode.FONT_KAITI: QT_TRANSLATE_NOOP("ui_messages", "手写楷体"),
    MessageCode.FONT_FANGSONG: QT_TRANSLATE_NOOP("ui_messages", "雅致仿宋"),
    MessageCode.FONT_PINGFANG: QT_TRANSLATE_NOOP("ui_messages", "清晰苹方"),
    MessageCode.FONT_HIRAGINO: QT_TRANSLATE_NOOP("ui_messages", "冬青黑体"),
    MessageCode.FONT_GEORGIA: QT_TRANSLATE_NOOP("ui_messages", "经典英文"),
    MessageCode.DIALOG_PICK_SOURCE: QT_TRANSLATE_NOOP("ui_messages", "选择小说原文"),
    MessageCode.DIALOG_SOURCE_FILTER:
        QT_TRANSLATE_NOOP("ui_messages", "书籍文件 (*.txt *.md *.rtf *.doc *.docx *.epub *.pdf)"),
    MessageCode.DIALOG_ADD_BOOK: QT_TRANSLATE_NOOP("ui_messages", "添加图书到书架"),
    MessageCode.DIALOG_READING_FILTER:
        QT_TRANSLATE_NOOP("ui_messages", "可阅读图书 (*.epub *.txt *.md *.partial);;EPUB 电子书 (*.epub);;TXT 文本 (*.txt *.md *.partial)"),
    MessageCode.DIALOG_PICK_GLOSSARY: QT_TRANSLATE_NOOP("ui_messages", "选择术语表"),
    MessageCode.DIALOG_GLOSSARY_FILTER:
        QT_TRANSLATE_NOOP("ui_messages", "术语表 (*.json *.tsv)"),
    MessageCode.DIALOG_OPEN_BOOK: QT_TRANSLATE_NOOP("ui_messages", "打开图书阅读"),
    MessageCode.DIALOG_RESEARCH_PDF: QT_TRANSLATE_NOOP("ui_messages", "导入科研 PDF"),

    MessageCode.POST_EDIT_IDLE:
        QT_TRANSLATE_NOOP("ui_messages", "翻译完成后，对照原文检查译名；建议由你决定是否采用。"),
    MessageCode.POST_EDIT_REMEMBERED:
        QT_TRANSLATE_NOOP("ui_messages", "已记住你的译名，正在自动重算全书对应段落。"),
    MessageCode.POST_EDIT_PREFERENCE_UNSAVED:
        QT_TRANSLATE_NOOP("ui_messages", "译名偏好未保存"),
    MessageCode.POST_EDIT_SELECTION_CHANGED:
        QT_TRANSLATE_NOOP("ui_messages", "译名选择已改变，正在自动重算全书对应段落。"),
    MessageCode.POST_EDIT_ANNOTATION_UNSAVED:
        QT_TRANSLATE_NOOP("ui_messages", "标注状态未保存"),
    MessageCode.POST_EDIT_DRAFT_SAVED:
        QT_TRANSLATE_NOOP("ui_messages", "人工修改稿已保存，尚未改动阅读内容。"),
    MessageCode.POST_EDIT_NEED_DRAFT:
        QT_TRANSLATE_NOOP("ui_messages", "先点“修改译文”保存完整段落，再应用。"),
    MessageCode.POST_EDIT_CHECKING:
        QT_TRANSLATE_NOOP("ui_messages", "对照当前译本检查。未完本时仅检查连续可读的已译部分。"),
    MessageCode.POST_EDIT_REPREPARE:
        QT_TRANSLATE_NOOP("ui_messages", "结构复查规则已升级，请重新准备校订。人工标注可单独复查。"),
    MessageCode.POST_EDIT_SUMMARY:
        QT_TRANSLATE_NOOP("ui_messages", "汇总 %1 个名称；%2 项拿不准，默认保持原译。"),
    MessageCode.POST_EDIT_RECORD_UNREADABLE:
        QT_TRANSLATE_NOOP("ui_messages", "无法读取校对记录"),
    MessageCode.POST_EDIT_SELECT_NAMES:
        QT_TRANSLATE_NOOP("ui_messages", "先勾选需要统一的译名，再生成预览。"),
    MessageCode.POST_EDIT_SELECT_EDITS:
        QT_TRANSLATE_NOOP("ui_messages", "先勾选确认采用的段落。"),
    MessageCode.POST_EDIT_WAIT_FINISH:
        QT_TRANSLATE_NOOP("ui_messages", "请等翻译完成，或暂停后再检查已译内容。"),
    MessageCode.POST_EDIT_UNSUPPORTED_TARGET:
        QT_TRANSLATE_NOOP("ui_messages", "自动校订目前只支持简体中文和繁体中文译本；本书目标是%1，仍可正常翻译、阅读和手动修改译文。"),
    MessageCode.POST_EDIT_UNKNOWN_TARGET:
        QT_TRANSLATE_NOOP("ui_messages", "尚不能确认本书的翻译目标语言，自动校订已停用；请先完成或重新准备译本。"),
    MessageCode.POST_EDIT_AUDITING:
        QT_TRANSLATE_NOOP("ui_messages", "校阅模型正在识别实体与语境…"),
    MessageCode.POST_EDIT_PREPARING:
        QT_TRANSLATE_NOOP("ui_messages", "正在复查标注、识别实体并比较译法…"),
    MessageCode.POST_EDIT_PAUSED:
        QT_TRANSLATE_NOOP("ui_messages", "校对已暂停，已完成检查和预览可继续。"),
    MessageCode.POST_EDIT_ENDED:
        QT_TRANSLATE_NOOP("ui_messages", "校对进程已结束，可从已保存部分继续。"),
    MessageCode.POST_EDIT_EXPORTED:
        QT_TRANSLATE_NOOP("ui_messages", "独立校订版已生成，正在阅读栏打开。"),
    MessageCode.POST_EDIT_ANNOTATIONS_DONE:
        QT_TRANSLATE_NOOP("ui_messages", "人工标注复查结束，%1 项仍待处理。请查看“人工标注”。"),
    MessageCode.POST_EDIT_APPLIED:
        QT_TRANSLATE_NOOP("ui_messages", "当前阅读版已更新，正在阅读栏热加载。"),
    MessageCode.POST_EDIT_PROCESS_FAILED:
        QT_TRANSLATE_NOOP("ui_messages", "无法启动校对进程，请重新打开应用。"),
    MessageCode.POST_EDIT_REVIEWER_CHANGED:
        QT_TRANSLATE_NOOP("ui_messages", "人工标注将交给 %1 复核；已有结果保留，点击重新复查可比较。"),
    MessageCode.POST_EDIT_ANNOTATE_CURRENT:
        QT_TRANSLATE_NOOP("ui_messages", "请在当前译文或本书校订版中标注；校对运行时请稍候。"),
    MessageCode.POST_EDIT_ANNOTATION_SAVED:
        QT_TRANSLATE_NOOP("ui_messages", "已保存标注，可在“译后校对 → 人工标注”中复查。"),
    MessageCode.POST_EDIT_EDIT_SAVED:
        QT_TRANSLATE_NOOP("ui_messages", "修改稿已保存，可直接应用到当前阅读版。"),
    MessageCode.POST_EDIT_EDIT_UNSAVED: QT_TRANSLATE_NOOP("ui_messages", "修改稿未保存"),
    MessageCode.POST_EDIT_SUMMARY_UNCERTAIN:
        QT_TRANSLATE_NOOP("ui_messages", "暂不能确定唯一译名，默认保持原译；无需为了继续而强行选择。"),
    MessageCode.POST_EDIT_SUMMARY_SKIP:
        QT_TRANSLATE_NOOP("ui_messages", "汇总后暂不建议统一，保持原译。"),
    MessageCode.POST_EDIT_SUMMARY_USER:
        QT_TRANSLATE_NOOP("ui_messages", "采用你指定的译名「%1」，不会被模型建议覆盖。"),
    MessageCode.POST_EDIT_SUMMARY_USE:
        QT_TRANSLATE_NOOP("ui_messages", "汇总后建议采用「%1」，在本次对应段落中已有 %2 段使用。"),
    MessageCode.POST_EDIT_SUMMARY_USE_NO_COUNT:
        QT_TRANSLATE_NOOP("ui_messages", "汇总后建议采用「%1」。"),
    MessageCode.POST_EDIT_SUMMARY_PENDING:
        QT_TRANSLATE_NOOP("ui_messages", "待汇总比较；可查看原译语境。"),
    MessageCode.POST_EDIT_COVERAGE:
        QT_TRANSLATE_NOOP("ui_messages", "覆盖已译 %1 / %2 段；结构复查放行 %3 项，分流 %4 项；每项最多六处语境。另有 %5 个候选未纳入。"),
    MessageCode.POST_EDIT_FAILURES:
        QT_TRANSLATE_NOOP("ui_messages", "%1 项检查未完成，可重试。"),
    MessageCode.POST_EDIT_ALREADY_APPLIED:
        QT_TRANSLATE_NOOP("ui_messages", "上次选择已应用到阅读版。"),
    MessageCode.POST_EDIT_PREVIEW_READY:
        QT_TRANSLATE_NOOP("ui_messages", "已准备 %1 段纯译名改动，等你确认应用。"),
    MessageCode.POST_EDIT_PREVIEW_EMPTY:
        QT_TRANSLATE_NOOP("ui_messages", "当前选择已在阅读版中，无需再改。"),
    MessageCode.POST_EDIT_PREVIEW_UNSAFE:
        QT_TRANSLATE_NOOP("ui_messages", "%1 段有额外改写、%2 段未通过检查，默认不采用。"),
    MessageCode.POST_EDIT_RECALCULATING:
        QT_TRANSLATE_NOOP("ui_messages", "译名方案已变化，正在自动重算可应用段落。"),
    MessageCode.POST_EDIT_REVIEWING_ANNOTATIONS:
        QT_TRANSLATE_NOOP("ui_messages", "%1 正在复查人工标注…"),
    MessageCode.POST_EDIT_BUILDING_PREVIEW:
        QT_TRANSLATE_NOOP("ui_messages", "正在准备所选段落的校订预览…"),
    MessageCode.POST_EDIT_PUBLISHING:
        QT_TRANSLATE_NOOP("ui_messages", "正在应用确认的校订…"),
    MessageCode.POST_EDIT_PUBLISHING_ANNOTATION:
        QT_TRANSLATE_NOOP("ui_messages", "正在应用人工修改稿…"),
    MessageCode.POST_EDIT_RECONCILING:
        QT_TRANSLATE_NOOP("ui_messages", "正在汇总比较译名 %1 / %2 组"),
    MessageCode.POST_EDIT_AUDIT_SCOPE:
        QT_TRANSLATE_NOOP("ui_messages", "对照检查 %1 个重复名称；每项抽查最多六处语境"),
    MessageCode.POST_EDIT_AUDIT_PROGRESS:
        QT_TRANSLATE_NOOP("ui_messages", "已检查 %1 / %2 个名称 · %3 项建议"),
    MessageCode.POST_EDIT_PREVIEW_PROGRESS:
        QT_TRANSLATE_NOOP("ui_messages", "校订预览 %1 / %2 段；尚未应用"),
    MessageCode.POST_EDIT_ANNOTATION_PROGRESS:
        QT_TRANSLATE_NOOP("ui_messages", "%1 正在复查人工标注 %2 / %3"),
    MessageCode.POST_EDIT_GATE_PROGRESS:
        QT_TRANSLATE_NOOP("ui_messages", "校阅模型正在识别实体与语境 · 已完成 %1 / %2 项"),
    MessageCode.POST_EDIT_VERDICT_KEEP: QT_TRANSLATE_NOOP("ui_messages", "建议保留"),
    MessageCode.POST_EDIT_VERDICT_ISSUE: QT_TRANSLATE_NOOP("ui_messages", "建议修改"),
    MessageCode.POST_EDIT_VERDICT_UNCERTAIN: QT_TRANSLATE_NOOP("ui_messages", "待人工确认"),
    MessageCode.POST_EDIT_OLD_REVIEWER: QT_TRANSLATE_NOOP("ui_messages", "Qwen 27B（旧记录）"),
    MessageCode.POST_EDIT_REVIEW_CHANGED:
        QT_TRANSLATE_NOOP("ui_messages", "本次与上次结论不同。"),
    MessageCode.POST_EDIT_REVIEW_MISSING:
        QT_TRANSLATE_NOOP("ui_messages", "本次尚无有效复核结论。"),
    MessageCode.POST_EDIT_PREVIOUS_REVIEW:
        QT_TRANSLATE_NOOP("ui_messages", "上次 %1：%2。"),
    MessageCode.RESEARCH_UPGRADING:
        QT_TRANSLATE_NOOP("ui_messages", "正在后台升级本页译文纸张；窗口可以继续使用…"),
    MessageCode.RESEARCH_SOURCE_REPAIRED:
        QT_TRANSLATE_NOOP("ui_messages", "已补回图片中的符号候选。请在“段落校对”中核对原文后重译；旧译稿已备份。"),
    MessageCode.RESEARCH_LAYOUT_RECLASSIFIED:
        QT_TRANSLATE_NOOP("ui_messages", "已识别出含行内公式的正文。请在“段落校对”中核对后确认；纯公式和图表仍保留原图。"),
    MessageCode.RESEARCH_RULES_CHANGED:
        QT_TRANSLATE_NOOP("ui_messages", "已修正标题与公式保护规则；受影响旧译稿已备份，可点击“翻译本页”重译。"),
    MessageCode.RESEARCH_NEED_APPROVAL:
        QT_TRANSLATE_NOOP("ui_messages", "请先对照原 PDF 核对本页文字，再点击确认。"),
    MessageCode.RESEARCH_SOURCE_SAVED:
        QT_TRANSLATE_NOOP("ui_messages", "原文已保存；请确认本页后翻译。"),
    MessageCode.RESEARCH_TERMS_UPDATED:
        QT_TRANSLATE_NOOP("ui_messages", "保留词已更新，仅用于后续翻译；已有译文保持不变。"),
    MessageCode.RESEARCH_RULES_CONFIRMED:
        QT_TRANSLATE_NOOP("ui_messages", "规则已确认，用于后续翻译；已有译文未改动。"),
    MessageCode.RESEARCH_CONFIRMED:
        QT_TRANSLATE_NOOP("ui_messages", "本页原文已确认，可以翻译。"),
    MessageCode.RESEARCH_APPROVED:
        QT_TRANSLATE_NOOP("ui_messages", "本页原文已确认（含 %1 段人工修正），可以翻译。"),
    MessageCode.RESEARCH_EXPORTED:
        QT_TRANSLATE_NOOP("ui_messages", "已导出已解析页的双语预览，未翻译段落明确标记。"),
    MessageCode.ACTION_INCOMPLETE:
        QT_TRANSLATE_NOOP("ui_messages", "本次操作未完成"),
    MessageCode.RESEARCH_IDLE:
        QT_TRANSLATE_NOOP("ui_messages", "导入论文后按页解析。公式和图表保留原图；扫描文字核对后再翻译。"),
    MessageCode.RESEARCH_READY: QT_TRANSLATE_NOOP("ui_messages", "原 PDF 已就绪。点击“解析本页”开始。"),
    MessageCode.RESEARCH_PAGE_READY:
        QT_TRANSLATE_NOOP("ui_messages", "可解析本页，或继续翻译已保存的段落。"),
    MessageCode.RESEARCH_RESTORING:
        QT_TRANSLATE_NOOP("ui_messages", "正在后台恢复上次科研文档…"),
    MessageCode.RESEARCH_RESTORE_FAILED:
        QT_TRANSLATE_NOOP("ui_messages", "上次科研文档无法恢复，请重新导入原 PDF。"),
    MessageCode.RESEARCH_WORKING: QT_TRANSLATE_NOOP("ui_messages", "正在处理本页…"),
    MessageCode.RESEARCH_BATCH_PROGRESS: QT_TRANSLATE_NOOP("ui_messages", "批量处理第 %1 / %2 页…"),
    MessageCode.RESEARCH_BATCH_DONE: QT_TRANSLATE_NOOP("ui_messages", "批量完成，共 %1 页。译文已保存，尚未人工核对的页面仍保留待核对标记。"),
    MessageCode.RESEARCH_BATCH_PARTIAL: QT_TRANSLATE_NOOP("ui_messages", "批量处理结束，%1 页仍需检查或补齐。已完成段落已保存。"),
    MessageCode.RESEARCH_FAILED: QT_TRANSLATE_NOOP("ui_messages", "处理失败"),
    MessageCode.RESEARCH_PAUSED:
        QT_TRANSLATE_NOOP("ui_messages", "已请求暂停，已完成段落保留。"),
    MessageCode.RESEARCH_TITLE: QT_TRANSLATE_NOOP("ui_messages", "科研模式 · 实验版"),
    MessageCode.RESEARCH_ORIGIN_MIXED:
        QT_TRANSLATE_NOOP("ui_messages", "混合文字与符号图片 · 请核对"),
    MessageCode.RESEARCH_ORIGIN_NATIVE:
        QT_TRANSLATE_NOOP("ui_messages", "原生文本 + 版面解析"),
    MessageCode.RESEARCH_ORIGIN_SCAN:
        QT_TRANSLATE_NOOP("ui_messages", "扫描识别 · 请核对"),
    MessageCode.RESEARCH_PAGE_RESTORED:
        QT_TRANSLATE_NOOP("ui_messages", "本页已解析，已恢复保存的结果。"),
    MessageCode.RESEARCH_CONNECTING:
        QT_TRANSLATE_NOOP("ui_messages", "连接 DGX 并识别本页版面…"),
    MessageCode.RESEARCH_ANALYZED:
        QT_TRANSLATE_NOOP("ui_messages", "本页解析完成。扫描文字请先对照原页核对。"),
    MessageCode.RESEARCH_TRANSLATING_BLOCK:
        QT_TRANSLATE_NOOP("ui_messages", "翻译本页第 %1/%2 段；公式、图表保留原图…"),
    MessageCode.RESEARCH_RETRYING_BLOCK:
        QT_TRANSLATE_NOOP("ui_messages", "本段校验未通过，正在重试…"),
    MessageCode.RESEARCH_TRANSLATED:
        QT_TRANSLATE_NOOP("ui_messages", "本页翻译完成，已保存。"),
    MessageCode.RESEARCH_TRANSLATED_PARTIAL:
        QT_TRANSLATE_NOOP("ui_messages", "本页翻译结束，%1 段术语校验未通过；已完成段落已保存，可单独检查原文后重试。"),
    MessageCode.RESEARCH_DOCUMENT_RESTORED:
        QT_TRANSLATE_NOOP("ui_messages", "上次科研文档已恢复。"),
    MessageCode.RESEARCH_UPGRADED:
        QT_TRANSLATE_NOOP("ui_messages", "本页译文纸张升级完成；旧数据已备份。"),
    MessageCode.RESEARCH_POSITION_READY:
        QT_TRANSLATE_NOOP("ui_messages", "原位对照已就绪。未译区域保留原文，点击段落可查看全文。"),

    MessageCode.EPUB_STATE_TRANSLATED: QT_TRANSLATE_NOOP("ui_messages", "已译"),
    MessageCode.EPUB_STATE_PARTIAL: QT_TRANSLATE_NOOP("ui_messages", "部分已译"),
    MessageCode.EPUB_STATE_PENDING: QT_TRANSLATE_NOOP("ui_messages", "待译"),
    MessageCode.EPUB_DEFAULT_SECTION: QT_TRANSLATE_NOOP("ui_messages", "第 %1 节"),
}

# Semantic translation-state values stored by new EPUB metadata.  Older caches
# stored the Chinese display words directly.
EPUB_STATE_SEMANTIC = {
    "translated": MessageCode.EPUB_STATE_TRANSLATED,
    "partial": MessageCode.EPUB_STATE_PARTIAL,
    "pending": MessageCode.EPUB_STATE_PENDING,
}
EPUB_STATE_LEGACY = {
    "已译": "translated",
    "部分已译": "partial",
    "待译": "pending",
}

# Exact Chinese sentences earlier builds stored in Python state.  Only an
# identical sentence is mapped; anything else keeps its text as a detail.
_LEGACY_EXACT = {
    "准备就绪": MessageCode.READY,
    "翻译完成": MessageCode.COMPLETED,
    "部分完成": MessageCode.PARTIAL,
    "翻译停止": MessageCode.TRANSLATION_STOPPED,
    "正在安全暂停…": MessageCode.PAUSING,
    "尚未统计": MessageCode.CACHE_NOT_COUNTED,
    "正在统计…": MessageCode.CACHE_COUNTING,
    "正在安全清理…": MessageCode.CACHE_CLEARING,
    "已译": MessageCode.EPUB_STATE_TRANSLATED,
    "部分已译": MessageCode.EPUB_STATE_PARTIAL,
    "待译": MessageCode.EPUB_STATE_PENDING,
    "本地模型已就绪 · 正文不会上传": MessageCode.MODEL_LOCAL_READY,
    "已找到 GGUF，但 Ollama 尚未载入此模型": MessageCode.MODEL_LOCAL_NOT_LOADED,
    "未找到本地 GGUF 模型文件": MessageCode.MODEL_LOCAL_FILE_MISSING,
    "DGX API 已配置": MessageCode.MODEL_REMOTE_READY,
    "需要配置 DGX API 凭据": MessageCode.MODEL_REMOTE_CREDENTIALS_MISSING,
    "导入论文后按页解析。公式和图表保留原图；扫描文字核对后再翻译。": MessageCode.RESEARCH_IDLE,
    "正在后台恢复上次科研文档…": MessageCode.RESEARCH_RESTORING,
    "上次科研文档无法恢复，请重新导入原 PDF。": MessageCode.RESEARCH_RESTORE_FAILED,
    "正在处理本页…": MessageCode.RESEARCH_WORKING,
    "已请求暂停，已完成段落保留。": MessageCode.RESEARCH_PAUSED,
    "翻译完成后，对照原文检查译名；建议由你决定是否采用。": MessageCode.POST_EDIT_IDLE,
    "独立 TXT 没有关联原文；可把原文放在旁边另一栏对照。": MessageCode.BILINGUAL_STANDALONE,
    "原文对照暂不可用，正在等待可验证的段落映射。": MessageCode.BILINGUAL_WAITING,
    "尚无可读译文；可切换到此书的翻译任务。": MessageCode.BILINGUAL_NO_TRANSLATION,
    "文件已移动，请重新打开。": MessageCode.READING_FILE_MOVED,
    "尚无可读内容。": MessageCode.READING_EMPTY,
}

# Ordered marker -> code.  Longest first so a specific sentence wins over a
# broader one.  A marker only counts when it appears in the exact sentence this
# application produced; anything else is left as an unknown detail.
_LEGACY_PREFIXES = (
    ("OpenCode 认证存储中缺少", MessageCode.CREDENTIALS_MISSING),
    ("没有可翻译的非空段落", MessageCode.SOURCE_CHANGED),
    ("原文正文与导入时的内容身份不一致", MessageCode.SOURCE_CHANGED),
    ("原文不存在", MessageCode.FILE_MISSING),
    ("原文已被修改", MessageCode.SOURCE_CHANGED),
    ("方向已锁定", MessageCode.DIRECTION_LOCKED),
    ("已有译文断点", MessageCode.DIRECTION_LOCKED),
    ("翻译模型或服务已改变", MessageCode.CHECKPOINT_INCOMPATIBLE),
    ("翻译模型已改变", MessageCode.CHECKPOINT_INCOMPATIBLE),
    ("翻译规则或术语模式已改变", MessageCode.CHECKPOINT_INCOMPATIBLE),
    ("术语表与既有断点不同", MessageCode.CHECKPOINT_INCOMPATIBLE),
    ("旧版、未逐段验证的直译断点", MessageCode.CHECKPOINT_INCOMPATIBLE),
    ("timed out", MessageCode.REQUEST_TIMEOUT),
    ("超时", MessageCode.REQUEST_TIMEOUT),
    ("HTTP Error", MessageCode.HTTP_FAILED),
    ("URLError", MessageCode.HTTP_FAILED),
    ("语言方向冲突", MessageCode.VALIDATION_FAILED),
    ("语言门禁", MessageCode.VALIDATION_FAILED),
)


def source_for(code: str) -> str:
    """The static source sentence of a code, or ``""`` when unknown."""
    return _SOURCES.get(str(code or ""), "")


def render(message: Message) -> str:
    """Render a message in the current locale.

    An unknown code falls back to a localized summary, and the caller keeps the
    detail separately.
    """
    if not isinstance(message, Message):
        message = Message(str(message or ""))
    # ``Message("")`` is the explicit no-message sentinel used by controller
    # properties such as ``errorMessage``.  Treating it as an unknown code
    # would manufacture an error summary and open the modal error dialog at
    # startup, which also steals all reader/editor keyboard input.
    if not message.code and not message.detail:
        return ""
    source = source_for(message.code)
    if not source:
        return QCoreApplication.translate(CONTEXT, UNKNOWN_SUMMARY)
    text = QCoreApplication.translate(CONTEXT, source)

    def parameter(match):
        index = int(match.group(1)) - 1
        if not 0 <= index < len(message.args):
            return match.group(0)
        value = message.args[index]
        # A parameter may itself be an application-owned label (for example
        # the active model name). Keep that as a nested message so an existing
        # status can be re-rendered after a locale switch instead of retaining
        # the language that happened to be active when the status was created.
        return render(value) if isinstance(value, Message) else str(value)

    # Substitute once against the translated template. A literal ``%2`` in a
    # user/model parameter must never be interpreted as another placeholder.
    return re.sub(r"%([1-9][0-9]*)", parameter, text)


def detail_text(message: Message) -> str:
    """The untranslated diagnostic, returned verbatim."""
    return str(message.detail or "")


def summary_with_detail(message: Message) -> str:
    """Localized summary plus the original detail when a code is unknown."""
    text = render(message)
    if source_for(message.code) or not message.detail:
        return text
    return f"{text}：{message.detail}"


def render_value(value, *, include_unknown_detail: bool = False) -> str:
    """Render a message, a sequence of messages, or a legacy plain string.

    Lists are used for compound status lines whose independently meaningful
    parts must survive a locale switch.  Plain strings remain a compatibility
    path for old worker events and user/content text.
    """
    if isinstance(value, Message):
        return summary_with_detail(value) if include_unknown_detail else render(value)
    if isinstance(value, (list, tuple)):
        return " ".join(filter(None, (
            render_value(item, include_unknown_detail=include_unknown_detail)
            for item in value
        )))
    return str(value or "")


def legacy_code(raw) -> str:
    """Map an exact sentence an earlier build stored to its stable code.

    Returns ``""`` for anything unrecognized: the category is never guessed.
    """
    text = str(raw or "").strip()
    if not text:
        return ""
    if text in _LEGACY_EXACT:
        return _LEGACY_EXACT[text]
    for marker, code in _LEGACY_PREFIXES:
        if text.startswith(marker) or marker in text:
            return code
    return ""


def legacy_message(raw) -> Message:
    """Conservatively read a legacy stored sentence as a message."""
    text = str(raw or "")
    code = legacy_code(text)
    if code:
        return Message(code, (), text if code == MessageCode.UNKNOWN_ERROR else "")
    if not text:
        return Message("")
    # Unknown: a localized summary at the UI boundary, detail preserved.
    return Message("", (), text)


def code_from_worker(event: dict, *, field: str = "message_code") -> str:
    """Read a structured code from a worker event, otherwise ``""``."""
    if not isinstance(event, dict):
        return ""
    return str(event.get(field) or "")


def message_from_worker_error(error, code: str = "") -> Message:
    """Build a message for a worker failure.

    A worker that reports a structured ``message_code`` is trusted.  Otherwise
    the raw error is classified only when it exactly matches a known sentence or
    a marker this application itself produced; an unrecognized error keeps its
    detail and is shown behind a localized summary.
    """
    detail = str(error or "")[:1000]
    known = str(code or "") or legacy_code(detail)
    if known:
        return Message(known, (), detail)
    return Message("", (), detail)


def epub_state_code(value) -> str:
    """Semantic code for a stored EPUB translation state.

    New metadata stores semantic values (``translated``/``partial``/``pending``);
    old caches stored the Chinese display words.  Both projections are
    read-only, and an unrecognized value is treated as "no state".
    """
    text = str(value or "").strip()
    if not text:
        return ""
    semantic = EPUB_STATE_SEMANTIC.get(text)
    if semantic:
        return semantic
    legacy = EPUB_STATE_LEGACY.get(text)
    if legacy:
        return EPUB_STATE_SEMANTIC[legacy]
    return ""


def epub_state_value(mapped_count: int, pending_count: int) -> str:
    """The semantic value new EPUB metadata stores."""
    mapped = max(0, int(mapped_count or 0))
    pending = max(0, int(pending_count or 0))
    if not mapped:
        return ""
    if pending >= mapped:
        return "pending"
    return "partial" if pending else "translated"
