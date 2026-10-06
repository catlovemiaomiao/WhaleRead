"""Qt bridge for review, paragraph previews, and explicitly accepted editions."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from PySide6.QtCore import QObject, QProcess, QTimer, Property, Signal, Slot
import answer_language
from glossary import fingerprint
from task_config import atomic, task_target_language
import annotations as human_notes
import ui_messages
import post_edit_capability as capability
from worker_context import prepare_worker

REVISION = 'post-edit-v5'
PREVIEW_REVISION = 'name-plan-v2'


class PostEditController(QObject):
    changed = Signal()
    editionApplied = Signal()
    annotationsChanged = Signal(str)

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.process = None
        self.project = ''
        self._issues = []
        self._edits = []
        self._status = ui_messages.Message(ui_messages.MessageCode.POST_EDIT_IDLE)
        self._coverage = ''
        self._output = ''
        self._buffer = ''
        self._action = ''
        self._done = False
        self._commit_close_scheduled = False
        self._stopping = False
        self._review_after = False
        self._export_copy = bool(owner.settings.value('review/export_copy', False, type=bool))
        self._focus = ''
        self._annotations = []
        self._annotation_message = ui_messages.Message('')
        self._live_sync_signatures = {}
        self._auto_preview_scheduled = False
        owner.locale.changed.connect(self.changed.emit)
        self._annotation_reviewer = 'hy'  # Legacy test/controller compatibility.

    @Property('QVariantList', notify=changed)
    def annotations(self):
        # Re-project labels at read time so an in-place locale switch updates an
        # already loaded review without touching the stored annotation ledger.
        return self._decorate_annotations(self._annotations)

    @staticmethod
    def _decorate_annotations(items):
        result = []
        for row in items:
            reviewer = row.get('reviewer') or {}
            history = row.get('review_history') or []
            previous = history[-1] if history else {}
            old_result, current_result = previous.get('result') or {}, row.get('result') or {}
            differs = old_result.get('verdict') and current_result.get('verdict') and old_result['verdict'] != current_result['verdict']
            labels = {
                'keep': ui_messages.render(ui_messages.Message(
                    ui_messages.MessageCode.POST_EDIT_VERDICT_KEEP)),
                'issue': ui_messages.render(ui_messages.Message(
                    ui_messages.MessageCode.POST_EDIT_VERDICT_ISSUE)),
                'uncertain': ui_messages.render(ui_messages.Message(
                    ui_messages.MessageCode.POST_EDIT_VERDICT_UNCERTAIN)),
            }
            prior_label = ((previous.get('reviewer') or {}).get('label') or
                           ui_messages.render(ui_messages.Message(
                               ui_messages.MessageCode.POST_EDIT_OLD_REVIEWER)))
            comparison = ''
            if old_result and (differs or not current_result):
                comparison = ui_messages.render(ui_messages.Message(
                    ui_messages.MessageCode.POST_EDIT_REVIEW_CHANGED if differs else
                    ui_messages.MessageCode.POST_EDIT_REVIEW_MISSING))
                comparison += ui_messages.render(ui_messages.Message(
                    ui_messages.MessageCode.POST_EDIT_PREVIOUS_REVIEW,
                    (prior_label, labels.get(old_result.get('verdict'),
                                             labels['uncertain']))))
            original = str(row.get('translation') or '')
            verified_draft = human_notes.default_draft(row)
            candidate = row.get('unverified_candidate') if isinstance(row.get('unverified_candidate'), dict) else {}
            candidate_draft = str(candidate.get('suggested_text') or '').strip()
            candidate_valid = bool(
                candidate_draft and candidate_draft != original.strip()
                and candidate.get('translation_fingerprint') == fingerprint(original)
            )
            default_draft = verified_draft
            if default_draft.strip() == original.strip() and candidate_valid:
                default_draft = candidate_draft
            error_code = human_notes.row_error_code(row)
            result.append({**row, 'default_draft': default_draft,
                           'unverified_draft': candidate_draft if candidate_valid else '',
                           'unverified_reason': str(candidate.get('reason') or '') if candidate_valid else '',
                           'unverified_suggestion': str(candidate.get('suggestion') or '') if candidate_valid else '',
                           'has_unverified_candidate': candidate_valid,
                           'category_id': human_notes.row_category_id(row),
                           'category_label': human_notes.row_category_label(row),
                           'error_code': error_code,
                           'error_bucket': human_notes.error_bucket(error_code),
                           'reviewer_label': reviewer.get('label') or (
                               ui_messages.render(ui_messages.Message(
                                   ui_messages.MessageCode.POST_EDIT_OLD_REVIEWER))
                               if row.get('result') else ''),
                           'previous_review_text': comparison})
        return result

    @Property('QVariantList', notify=changed)
    def categoryOptions(self):  # noqa: N802
        """Structured review-category picker: stable value plus display label."""
        return self.owner.locale.localize_labels(human_notes.category_options(), 'annotations')

    @Property(str, notify=changed)
    def annotationReviewer(self):
        return self.owner.providers.selected('review') if hasattr(self.owner, 'providers') else self._annotation_reviewer

    @annotationReviewer.setter
    def annotationReviewer(self, value):
        if hasattr(self.owner, 'providers') and not self.busy:
            if not self.owner.providers.select('review', 'local_7b' if value == 'hy' else value):
                return
            self._annotation_message = ui_messages.Message(
                ui_messages.MessageCode.POST_EDIT_REVIEWER_CHANGED,
                (self._annotation_reviewer_message(),))
            self.notify()

    @Property(str, notify=changed)
    def annotationReviewerLabel(self):
        value = self._annotation_reviewer_message()
        return ui_messages.render(value) if isinstance(value, ui_messages.Message) else value

    def _annotation_reviewer_message(self):
        if hasattr(self.owner, 'providers'):
            try:
                return self.owner.review_route()['label']
            except ValueError:
                return self.owner.providers.message
        return self.owner._model_display_message()

    @Property(str, notify=changed)
    def annotationMessage(self):
        return ui_messages.render_value(self._annotation_message)

    @Property(str, notify=changed)
    def annotationMessageDetail(self):  # noqa: N802
        """Untranslated annotation diagnostic, kept out of the summary."""
        return (ui_messages.detail_text(self._annotation_message)
                if isinstance(self._annotation_message, ui_messages.Message) else '')

    @Property(bool, notify=changed)
    def canAnnotate(self):
        if not self.owner.readingJobPath or self.busy:
            return False
        path = self.owner.readingPath
        return bool(path) and Path(path).resolve().is_relative_to(Path(self.owner.readingJobPath).resolve())

    def _annotation_context(self, book):
        """Return the verified rows/anchors behind TXT or an EPUB rendering."""
        column = self.owner._active_column()
        if column is None:
            raise ValueError('当前阅读栏已经关闭。')
        path = Path(self.owner.readingPath)
        if not path.exists() and str(path).endswith('.partial'):
            path = Path(str(path)[:-8])
        if column.epubTranslated:
            import post_edit
            rows, _, anchors = human_notes.verified_alignment(
                Path(self.owner.readingJobPath), path, book, post_edit.engine)
            return path, rows, anchors
        _, anchors = column._alignment()
        return path, list(self.owner.reader.rows), anchors

    @Slot(str, str, str, result='QVariantMap')
    def locateEpubQuote(self, source, quote, ids):
        try:
            if not self.canAnnotate or not quote.strip():
                raise ValueError('请先选中译文中的文字，再划线校阅。')
            sys.path.insert(0, str(self.owner.root / 'runtime/tools'))
            import post_edit
            book = post_edit.snapshot(Path(self.owner.readingJobPath))
            numbers = [int(n) for n in ids.split()] if ids else [i for i, s in enumerate(book['segments'], 1) if s.strip() == source.strip()]
            if any(n < 1 or n > len(book['segments']) for n in numbers) or len(set(numbers)) != len(numbers):
                raise ValueError('段落标识无效，请更新当前译本后重选。')
            if not numbers or (not ids and len(numbers) != 1) or '\n'.join(book['segments'][n - 1] for n in numbers).strip() != source.strip():
                raise ValueError('所选原文无法唯一定位，请只选择一个段落。')
            _, reader_rows, anchors = self._annotation_context(book)
            matches = []
            for row, anchor in enumerate(anchors):
                if anchor['segment'] not in numbers:
                    continue
                text = reader_rows[row]
                start = text.find(quote)
                if start >= 0:
                    if text.find(quote, start + 1) >= 0:
                        raise ValueError('这句话在段内重复，请多选几个字以便准确定位。')
                    matches.append(dict(row=row, start=len(text[:start].encode('utf-16-le')) // 2,
                                        end=len(text[:start + len(quote)].encode('utf-16-le')) // 2, quote=quote))
            if len(matches) != 1:
                raise ValueError('所选文字跨段、已改变或不能唯一定位，请重新选择一个段落。')
            self._annotation_message = ui_messages.Message('')
            self.changed.emit()
            return matches[0]
        except (OSError, ValueError, KeyError, IndexError, RuntimeError) as exc:
            self._annotation_message = ui_messages.Message('', (), str(exc))
            self.changed.emit()
            return {}

    @Slot(str, result='QVariantList')
    def epubMarks(self, job):
        # A column owns its marks even while another column is active.
        if not job or not any(c.readingJobPath == job for c in self.owner._reading_columns):
            return []
        try:
            return [dict(source=r['source_text'], quote=r['quote'], translation=r['translation'])
                    for r in human_notes.load(Path(job))['items']]
        except (OSError, ValueError, KeyError):
            return []

    @Slot(int, int, int, str, str, str, result=bool)
    def addAnnotation(self, row, start, end, quote, note, category):
        if not self.canAnnotate:
            self._annotation_message = ui_messages.Message(
                ui_messages.MessageCode.POST_EDIT_ANNOTATE_CURRENT)
            self.changed.emit()
            return False
        try:
            sys.path.insert(0, str(self.owner.root / 'runtime/tools'))
            import post_edit
            root = Path(self.owner.readingJobPath)
            book = post_edit.snapshot(root)
            path, reader_rows, _ = self._annotation_context(book)
            if not 0 <= row < len(reader_rows):
                raise ValueError('所选阅读段落已改变')
            # Qt uses UTF-16 indices; Python uses Unicode code points.
            text = reader_rows[row]
            encoded = text.encode('utf-16-le')
            start = len(encoded[:start * 2].decode('utf-16-le'))
            end = len(encoded[:end * 2].decode('utf-16-le'))
            human_notes.add(root, path, book, post_edit.engine, reader_rows,
                            row, start, end, quote, note, category)
            self._annotations = self._decorate_annotations(human_notes.load(root)['items'])
            self._annotation_message = ui_messages.Message(
                ui_messages.MessageCode.POST_EDIT_ANNOTATION_SAVED)
        except (OSError, ValueError, KeyError, IndexError) as exc:
            self._annotation_message = ui_messages.Message(
                ui_messages.MessageCode.POST_EDIT_ANNOTATION_UNSAVED, (), str(exc))
            self.notify()
            return False
        self.annotationsChanged.emit(str(root))
        self.notify()
        return True

    @Slot(str, bool)
    def resolveAnnotation(self, identity, resolved):
        if self.busy or not self.owner._job_path:
            return
        try:
            root = Path(self.owner._job_path)
            doc = human_notes.load(root)
            for row in doc['items']:
                if row['id'] == identity:
                    row['status'] = 'resolved' if resolved else 'pending'
                    if not resolved:
                        row.pop('review_stamp', None)
                        row['result'] = None
                        row.pop('error', None)
                        row.pop('error_code', None)
            human_notes.save(root, doc)
            self._annotations = self._decorate_annotations(doc['items'])
            self.annotationsChanged.emit(str(root))
        except (OSError, ValueError) as exc:
            self._status = ui_messages.Message(
                ui_messages.MessageCode.POST_EDIT_ANNOTATION_UNSAVED, (), str(exc))
        self.notify()

    @Slot(str)
    def reviewAnnotation(self, identity):
        self._start('annotations', [identity] if identity else [])

    @Slot(str, str, result=bool)
    def saveAnnotationEdit(self, identity, after):
        if self.busy or not self.owner._job_path:
            return False
        try:
            sys.path.insert(0, str(self.owner.root / 'runtime/tools'))
            import post_edit
            root = Path(self.owner._job_path)
            human_notes.save_edit(root, identity, after, post_edit.snapshot(root), post_edit.engine)
            self._annotations = self._decorate_annotations(human_notes.load(root)['items'])
            self._annotation_message = ui_messages.Message(
                ui_messages.MessageCode.POST_EDIT_EDIT_SAVED)
            self._status = ui_messages.Message(ui_messages.MessageCode.POST_EDIT_DRAFT_SAVED)
        except (OSError, ValueError, RuntimeError, KeyError, IndexError) as exc:
            self._annotation_message = ui_messages.Message(
                ui_messages.MessageCode.POST_EDIT_EDIT_UNSAVED, (), str(exc))
            self._status = self._annotation_message
            self.notify()
            return False
        self.notify()
        return True

    @Slot(str)
    def publishAnnotation(self, identity):
        row = next((item for item in self._annotations if item.get('id') == identity), None)
        if not row or not str(row.get('proposed_text') or '').strip():
            self._status = ui_messages.Message(ui_messages.MessageCode.POST_EDIT_NEED_DRAFT)
            self.notify()
            return
        self._start('annotation_publish', [identity])

    @Property(bool, notify=changed)
    def busy(self):
        return self.process is not None

    @Property(bool, notify=changed)
    def reviewAfter(self):
        return self._review_after

    @reviewAfter.setter
    def reviewAfter(self, value):
        self._review_after = bool(value)
        self.changed.emit()

    @Property(bool, notify=changed)
    def exportCopy(self):
        return self._export_copy

    @exportCopy.setter
    def exportCopy(self, value):
        value = bool(value)
        if value != self._export_copy and not self.busy:
            self._export_copy = value
            self.owner.settings.setValue('review/export_copy', value)
            self.changed.emit()

    @Property(str, notify=changed)
    def focusTerm(self):
        return self._focus

    @focusTerm.setter
    def focusTerm(self, value):
        self._focus = value[:100]

    @Property('QVariantList', notify=changed)
    def issues(self):
        return [
            {**row, 'display_reason': self.summary(
                row, row.get('user_preference_value', ''))}
            for row in self._issues
        ]

    @Property('QVariantList', notify=changed)
    def edits(self):
        return self._edits

    @Property(int, notify=changed)
    def readyCount(self):
        return sum(bool(r.get('accepted')) for r in self._edits)

    @Property(str, notify=changed)
    def statusCode(self):  # noqa: N802
        # ``Message`` is a NamedTuple, so test it before the generic sequence
        # branch; otherwise its code/args/detail fields are mistaken for three
        # independent status items.
        values = ([self._status] if isinstance(self._status, ui_messages.Message)
                  else self._status if isinstance(self._status, (list, tuple))
                  else [self._status])
        return next((value.code for value in values
                     if isinstance(value, ui_messages.Message) and value.code), '')

    @Property(str, notify=changed)
    def statusDetail(self):  # noqa: N802
        """The untranslated diagnostic behind the current status."""
        values = ([self._status] if isinstance(self._status, ui_messages.Message)
                  else self._status if isinstance(self._status, (list, tuple))
                  else [self._status])
        return '\n'.join(filter(None, (
            ui_messages.detail_text(value) for value in values
            if isinstance(value, ui_messages.Message)
        )))

    @Property(str, notify=changed)
    def status(self):
        # The status is a localized summary. Raw diagnostics have their own
        # ``statusDetail`` property and QML label.
        return ui_messages.render_value(self._status)

    @Property(str, notify=changed)
    def coverage(self):
        return ui_messages.render_value(self._coverage)

    @Property(str, notify=changed)
    def output(self):
        return self._output

    @Property(bool, notify=changed)
    def canRun(self):
        return bool(self.owner._job_path) and self.owner._progress > 0 and not self.owner._running and not self.busy

    def notify(self):
        self.changed.emit()
        self.owner.changed.emit()

    @staticmethod
    def summary(row, preference=''):
        # The model's free-form explanation can contradict its structured choice.
        # Display only facts enforced by the program; keep raw reasoning in the report.
        if row.get('decision') == 'uncertain':
            return ui_messages.render(ui_messages.Message(
                ui_messages.MessageCode.POST_EDIT_SUMMARY_UNCERTAIN))
        if row.get('decision') == 'skip':
            return ui_messages.render(ui_messages.Message(
                ui_messages.MessageCode.POST_EDIT_SUMMARY_SKIP))
        if preference:
            return ui_messages.render(ui_messages.Message(
                ui_messages.MessageCode.POST_EDIT_SUMMARY_USER, (preference,)))
        if row.get('decision') == 'use':
            chosen = row['preferred']
            count = row.get('variant_counts', {}).get(chosen)
            if count is not None:
                return ui_messages.render(ui_messages.Message(
                    ui_messages.MessageCode.POST_EDIT_SUMMARY_USE, (chosen, count)))
            return ui_messages.render(ui_messages.Message(
                ui_messages.MessageCode.POST_EDIT_SUMMARY_USE_NO_COUNT, (chosen,)))
        return ui_messages.render(ui_messages.Message(
            ui_messages.MessageCode.POST_EDIT_SUMMARY_PENDING))

    def _append_status(self, message):
        if not message:
            return
        if isinstance(self._status, list):
            self._status.append(message)
        elif self._status:
            self._status = [self._status, message]
        else:
            self._status = message

    @Slot()
    def load(self):
        if self.busy:
            return
        project = self.owner._job_path
        if project != self.project:
            self._issues, self._edits, self._coverage, self._output = [], [], '', ''
            self._live_sync_signatures = {}
            self._status = ui_messages.Message(ui_messages.MessageCode.POST_EDIT_CHECKING)
        self.project = project
        directory = Path(project) / '译后校对'
        try:
            self._annotations = self._decorate_annotations(human_notes.load(Path(project))['items']) if project else []
            report, preferences = {}, {}
            report_path = directory / '检查报告.json'
            if report_path.is_file():
                report = json.loads(report_path.read_text())
                if report.get('revision') != REVISION:
                    self._issues, self._edits, self._coverage = [], [], ''
                    self._status = ui_messages.Message(ui_messages.MessageCode.POST_EDIT_REPREPARE)
                    self.notify()
                    return
                self._focus = report.get('focus', '')
                preferences = json.loads((directory / '偏好译名.json').read_text()) if (directory / '偏好译名.json').is_file() else {}
                self._issues = [{**r, 'preferred': preferences.get(r['source']) or r['preferred'],
                                 'user_preference_value': preferences.get(r['source'], ''),
                                 'display_reason': self.summary(r, preferences.get(r['source'], '')),
                                 'selected': r.get('decision') == 'use'} for r in report.get('issues', [])]
                c = report.get('coverage', {})
                self._coverage = ui_messages.Message(
                    ui_messages.MessageCode.POST_EDIT_COVERAGE,
                    (c.get('translated_segments', 0), c.get('total_segments', 0),
                     c.get('gate_accepted', 0), c.get('gate_filtered', 0),
                     c.get('omitted_candidates', 0)))
                uncertain = sum(r.get('decision', 'uncertain') == 'uncertain' for r in self._issues)
                self._status = ui_messages.Message(
                    ui_messages.MessageCode.POST_EDIT_SUMMARY, (len(self._issues), uncertain))
                if report.get('failures'):
                    self._append_status(ui_messages.Message(
                        ui_messages.MessageCode.POST_EDIT_FAILURES,
                        (len(report['failures']),)))
            draft_path = directory / '校订预览.json'
            if draft_path.is_file():
                draft = json.loads(draft_path.read_text())
                signature = fingerprint({'stamp': report.get('stamp'), 'issues': report.get('issues', [])}) if report_path.is_file() else ''
                if (report_path.is_file() and draft.get('preview_revision') == PREVIEW_REVISION
                        and draft.get('plan_stamp') == signature
                        and draft.get('preferences_stamp') == fingerprint(preferences)):
                    if draft.get('status') == 'applied':
                        self._edits = []
                        self._append_status(ui_messages.Message(
                            ui_messages.MessageCode.POST_EDIT_ALREADY_APPLIED))
                    else:
                        self._edits = [{**r, 'accepted': bool(r.get('safe'))} for r in draft.get('edits', [])]
                        unsafe = len(self._edits) - self.readyCount
                        if self._edits:
                            self._append_status(ui_messages.Message(
                                ui_messages.MessageCode.POST_EDIT_PREVIEW_READY,
                                (self.readyCount,)))
                        else:
                            self._append_status(ui_messages.Message(
                                ui_messages.MessageCode.POST_EDIT_PREVIEW_EMPTY))
                        if unsafe or draft.get('failures'):
                            self._append_status(ui_messages.Message(
                                ui_messages.MessageCode.POST_EDIT_PREVIEW_UNSAFE,
                                (unsafe, len(draft.get('failures', {})))))
                else:
                    self._edits = []
                    if any(r.get('selected') for r in self._issues):
                        self._append_status(ui_messages.Message(
                            ui_messages.MessageCode.POST_EDIT_RECALCULATING))
                        self._schedule_preview()
            latest = directory / '最新校订.json'
            if latest.is_file():
                data = json.loads(latest.read_text())
                output = Path(data.get('output', '')).resolve()
                if output.parent == directory.resolve() and output.is_file():
                    self._output = str(output)
        except (OSError, ValueError, KeyError) as exc:
            self._status = ui_messages.Message(
                ui_messages.MessageCode.POST_EDIT_RECORD_UNREADABLE, (), str(exc))
        self.notify()

    def syncCurrentReading(self, raw_path):  # noqa: N802
        """Keep the mutable current-reading edition aligned with translation progress."""
        if self.busy or not self.owner.readingJobPath or not self.owner.readingMachinePath:
            return False
        return self.syncCurrentReadingFor(
            self.owner.readingJobPath, raw_path, self.owner.readingMachinePath)

    def syncCurrentReadingFor(self, raw_root, raw_path, raw_machine):  # noqa: N802
        """Synchronize one explicit column without changing the active task."""
        if self.busy or not raw_root or not raw_machine:
            return False
        root = Path(raw_root).resolve()
        path = Path(raw_path).resolve()
        if path.parent != (root / '译后校对').resolve() or not path.name.endswith('.当前阅读版.txt'):
            return False
        machine = Path(raw_machine)
        if not machine.is_file() and str(machine).endswith('.partial'):
            machine = Path(str(machine)[:-8])
        if not machine.is_file() or not path.is_file():
            return False
        try:
            current_stat, machine_stat = path.stat(), machine.stat()
            signature = (
                str(root), str(path), current_stat.st_mtime_ns, current_stat.st_size,
                str(machine), machine_stat.st_mtime_ns, machine_stat.st_size,
            )
            key = (str(root), str(path))
            if signature == self._live_sync_signatures.get(key):
                return False
            sys.path.insert(0, str(self.owner.root / 'runtime/tools'))
            import post_edit
            changed = human_notes.sync_current_edition(
                root, path, post_edit.snapshot(root), post_edit.engine
            )
            current_stat = path.stat()
            self._live_sync_signatures[key] = (
                str(root), str(path), current_stat.st_mtime_ns, current_stat.st_size,
                str(machine), machine_stat.st_mtime_ns, machine_stat.st_size,
            )
            return changed
        except (OSError, ValueError, RuntimeError, KeyError, IndexError):
            # Translation state and its visible file are each atomically saved,
            # but the UI can observe the short interval between those commits.
            # Retry on the next file signature without overwriting unverified text.
            return False

    @Slot(str, bool)
    def selectIssue(self, identity, value):
        if not self.busy:
            for row in self._issues:
                if row['id'] == identity and row.get('selected') != bool(value):
                    row['selected'] = bool(value)
                    self._edits = []
                    self._status = ui_messages.Message(
                        ui_messages.MessageCode.POST_EDIT_SELECTION_CHANGED)
                    self.notify()
                    self._schedule_preview()

    def _save_preference(self, row, value):
        """Persist one preferred name; return True only when it was written.

        The auto-preview decision follows this result, never the message text:
        a translated or reworded status line must not stop the recalculation.
        """
        path = Path(self.owner._job_path) / '译后校对/偏好译名.json'
        try:
            preferences = json.loads(path.read_text()) if path.is_file() else {}
            preferences[row['source']] = value
            atomic(path, json.dumps(preferences, ensure_ascii=False, indent=2))
        except (OSError, ValueError) as exc:
            self._status = ui_messages.Message(
                ui_messages.MessageCode.POST_EDIT_PREFERENCE_UNSAVED, (), str(exc))
            return False
        row['selected'] = True
        row['user_preference_value'] = value
        self._status = ui_messages.Message(ui_messages.MessageCode.POST_EDIT_REMEMBERED)
        return True

    @Slot(str, str)
    def setPreferred(self, identity, value):
        if not self.busy:
            for row in self._issues:
                if row['id'] == identity and value.strip() != row['preferred']:
                    value = value.strip()[:60]
                    if not value:
                        return
                    row['preferred'] = value
                    saved = self._save_preference(row, value)
                    self._edits = []
                    self.notify()
                    if saved:
                        self._schedule_preview()

    def _schedule_preview(self):
        if self._auto_preview_scheduled:
            return
        self._auto_preview_scheduled = True
        QTimer.singleShot(0, self._run_scheduled_preview)

    def _run_scheduled_preview(self):
        self._auto_preview_scheduled = False
        if not self.busy:
            self.preview()

    @Slot(int, bool)
    def selectEdit(self, number, value):
        if not self.busy:
            for row in self._edits:
                if row['id'] == number:
                    row['accepted'] = bool(value)
                    self.changed.emit()

    @Slot()
    def audit(self):
        self._start('audit')

    @Slot()
    def prepare(self):
        self._start('prepare')

    @Slot()
    def preview(self):
        choices = [{'id': r['id'], 'preferred': r['preferred']} for r in self._issues if r.get('selected')]
        if not choices:
            self._status = ui_messages.Message(ui_messages.MessageCode.POST_EDIT_SELECT_NAMES)
            self.notify()
            return
        self._start('preview', choices)

    @Slot()
    def publish(self):
        ids = [r['id'] for r in self._edits if r.get('accepted')]
        if not ids:
            self._status = ui_messages.Message(ui_messages.MessageCode.POST_EDIT_SELECT_EDITS)
            self.notify()
            return
        self._start('publish', ids)

    @Slot()
    def readOutput(self):
        if self._output:
            self.owner.openReadingFile(self._output)

    @Property(str, notify=changed)
    def targetLanguage(self):  # noqa: N802
        """The translation target of the current book, as a stored value."""
        project = str(getattr(self.owner, '_job_path', '') or '')
        if project and (Path(project) / '翻译任务.yaml').is_file():
            return task_target_language(Path(project))
        return str(getattr(self.owner, '_target_language', '') or '')

    @Property(bool, notify=changed)
    def capabilitySupported(self):  # noqa: N802
        """Whether automated review may run for this book's target language."""
        return capability.supports(self.targetLanguage)

    @Property(str, notify=changed)
    def capabilityReason(self):  # noqa: N802
        """Localized explanation when automated review is unavailable."""
        return '' if capability.supports(self.targetLanguage) else capability.reason_text(
            self.targetLanguage)

    # Actions that ask a provider to compare a translation with its source.
    AUTOMATED_ACTIONS = ('audit', 'prepare', 'annotations')
    def _start(self, action, selection=None):
        if not self.canRun:
            self._status = ui_messages.Message(ui_messages.MessageCode.POST_EDIT_WAIT_FINISH)
            self.notify()
            return
        # The capability gate runs before the worker is spawned, so an
        # unsupported target produces no provider request at all.
        if action in self.AUTOMATED_ACTIONS and not capability.supports(self.targetLanguage):
            target = self.targetLanguage
            self._status = ui_messages.Message(
                capability.message_code(target), capability.message_args(target))
            self.notify()
            return
        # Constants live in the running launcher (renamed launcher.py in the App).
        try:
            route = (self.owner.review_route() if action in self.AUTOMATED_ACTIONS
                     else self.owner.providers.route('local_7b'))
            if action in self.AUTOMATED_ACTIONS:
                self.owner.providers.require_consent('review', route['id'])
        except Exception as exc:
            self._status = str(exc)
            self.notify()
            return
        self.project = self.owner._job_path
        request_answer_locale = answer_language.resolve(
            answer_language.stored(self.owner.settings), self.owner.locale.effective)
        process = QProcess(self)
        arguments = [action,
                     '--task-root', self.project, '--api-base', str(route['api_base']),
                     '--model', str(route['model']), '--auth-provider', str(route['auth_provider']),
                     '--review-api-base', str(route['api_base']),
                     '--annotation-reviewer', route['id'], '--reviewer-label', route['label'],
                     '--answer-locale', request_answer_locale,
                     '--auth-path', '',
                     '--parent-pid', str(os.getpid()), '--focus', self._focus,
                     '--selection', json.dumps(selection or [], ensure_ascii=False)]
        if action in ('publish', 'annotation_publish') and self._export_copy:
            arguments.append('--export-copy')
        try:
            prepare_worker(process, self.owner, 'post_edit.py', arguments,
                           route=route if action in self.AUTOMATED_ACTIONS else None,
                           feature='review' if action in self.AUTOMATED_ACTIONS else None)
        except Exception as exc:
            self._status = str(exc)
            process.deleteLater()
            self.notify()
            return
        process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        process.readyReadStandardOutput.connect(self._read)
        process.finished.connect(self._finished)
        process.errorOccurred.connect(self._error)
        self.process, self._action, self._buffer = process, action, ''
        self._done = self._stopping = self._commit_close_scheduled = False
        self._status = {'audit': ui_messages.Message(ui_messages.MessageCode.POST_EDIT_AUDITING),
                        'prepare': ui_messages.Message(ui_messages.MessageCode.POST_EDIT_PREPARING),
                        'annotations': ui_messages.Message(
                            ui_messages.MessageCode.POST_EDIT_REVIEWING_ANNOTATIONS,
                            (self._annotation_reviewer_message(),)),
                        'preview': ui_messages.Message(
                            ui_messages.MessageCode.POST_EDIT_BUILDING_PREVIEW),
                        'publish': ui_messages.Message(
                            ui_messages.MessageCode.POST_EDIT_PUBLISHING),
                        'annotation_publish': ui_messages.Message(
                            ui_messages.MessageCode.POST_EDIT_PUBLISHING_ANNOTATION)}[action]
        process.start()
        self.notify()

    def _read(self):
        if not self.process:
            return
        self._buffer += bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')
        while '\n' in self._buffer:
            line, self._buffer = self._buffer.split('\n', 1)
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') in ('status', 'error'):
                code = ui_messages.code_from_worker(event)
                if code:
                    args = event.get('message_args') or []
                    if not isinstance(args, (list, tuple)):
                        args = []
                    detail = str(event.get('detail') or '')[:500]
                    self._status = ui_messages.Message(code, tuple(args), detail)
                else:
                    # Older status lines remain verbatim.  Legacy errors use
                    # the conservative adapter, which recognizes only exact
                    # application-owned markers and otherwise keeps the raw
                    # detail behind a localized unknown-error summary.
                    raw = str(event.get('detail') or event.get('message') or '')[:500]
                    self._status = (ui_messages.message_from_worker_error(raw)
                                    if event.get('type') == 'error' else raw)
            if event.get('type') == 'done':
                self._done = True
                if event.get('output'):
                    self._output = event['output']
                # The worker emits done only after its files are atomically committed and
                # the task lock is released.  Do not leave the UI pending forever if a
                # provider/runtime keeps a tail resource alive after that point.
                if not self._commit_close_scheduled:
                    self._commit_close_scheduled = True
                    QTimer.singleShot(250, lambda p=self.process: self._close_committed_process(p))
            self.notify()

    def _close_committed_process(self, process):
        try:
            if self.process is process and self._done and process.state() != QProcess.ProcessState.NotRunning:
                process.terminate()
                QTimer.singleShot(1500, lambda p=process: self._kill(p))
        except RuntimeError:
            pass

    def _finished(self, code, _status):
        self._read()
        process, self.process = self.process, None
        action = self._action
        message = self._status
        self.load()
        if action in ('annotations', 'publish', 'annotation_publish') and self.project:
            self.annotationsChanged.emit(str(self.project))
        if self._stopping:
            self._status = ui_messages.Message(ui_messages.MessageCode.POST_EDIT_PAUSED)
        elif not self._done:
            if code == 0:
                self._status = ui_messages.Message(ui_messages.MessageCode.POST_EDIT_ENDED)
            elif isinstance(message, ui_messages.Message):
                # Keep a structured worker failure intact. If no event arrived,
                # the empty sentinel becomes an honest unknown failure.
                self._status = (message if message.code or message.detail else
                                ui_messages.Message(ui_messages.MessageCode.UNKNOWN_ERROR))
            else:
                self._status = ui_messages.message_from_worker_error(message)
        elif action in ('publish', 'annotation_publish'):
            self._status = (ui_messages.Message(ui_messages.MessageCode.POST_EDIT_EXPORTED) if self._export_copy
                            else ui_messages.Message(
                                ui_messages.MessageCode.POST_EDIT_APPLIED))
            QTimer.singleShot(0, self._open_applied_output)
        elif action == 'annotations':
            failed = sum(r.get('status') in ('failed', 'stale', 'pending') for r in self._annotations)
            self._status = ui_messages.Message(ui_messages.MessageCode.POST_EDIT_ANNOTATIONS_DONE, (failed,))
        if process:
            process.deleteLater()
        self.notify()

    def _open_applied_output(self):
        if self._output and self.owner.openReadingFile(self._output):
            self.editionApplied.emit()

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            process, self.process = self.process, None
            self._status = ui_messages.Message(ui_messages.MessageCode.POST_EDIT_PROCESS_FAILED)
            if process:
                process.deleteLater()
            self.notify()

    @Slot()
    def stop(self):
        if self.process:
            if self._done:
                self._close_committed_process(self.process)
                return
            self._stopping = True
            process = self.process
            process.terminate()
            QTimer.singleShot(3000, lambda p=process: self._kill(p))

    @staticmethod
    def _kill(process):
        try:
            if process.state() != QProcess.ProcessState.NotRunning:
                process.kill()
        except RuntimeError:
            pass

    def shutdown(self):
        if self.process:
            process = self.process
            self._stopping = True
            process.terminate()
            if not process.waitForFinished(2500):
                process.kill()
                process.waitForFinished(1000)
