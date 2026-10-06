from __future__ import annotations
import json
from pathlib import Path
import sys
from functools import wraps
from PySide6.QtCore import QObject, QProcess, QTimer, QUrl, Property, Signal, Slot
from PySide6.QtGui import QDesktopServices
from apple_services import choose_open_file
from research import LAYOUT_REVISION, import_pdf, render_page, load, save, page_dir, edit_source, export_html, reading_text
from research_position import position_path
from research_terms import candidates
import ui_messages
from worker_context import prepare_worker


def guarded(function):
    @wraps(function)
    def invoke(self, *args, **kwargs):
        try:
            return function(self, *args, **kwargs)
        except Exception as exc:
            self.message = ui_messages.Message(
                ui_messages.MessageCode.ACTION_INCOMPLETE, (), str(exc)[:500])
            self.changed.emit()
    return invoke


class ResearchController(QObject):
    changed = Signal()

    def __init__(self, owner, *, restore=True):
        super().__init__(owner)
        self.owner = owner
        self.job = None
        self.manifest = {}
        self.data = {}
        self.position = {}
        self.term_candidates = []
        self._position_scheduled = False
        self._position_failed = ''
        self.process = None
        self._action = ''
        self.active_routes = {}
        self._paused = False
        self._position_message = None
        self._closed = False
        # True while the visible message is still the generic "working" text,
        # so a failed worker may replace it with the raw failure detail.  A
        # worker status line (or a start/pause notice) clears it, and no branch
        # compares localized message text.
        self._generic_status = False
        self._upgrade_scheduled = False
        self._restore_scheduled = False
        self.message = ui_messages.Message(ui_messages.MessageCode.RESEARCH_IDLE)
        self.image = ''
        self._buffer = ''
        self.library = getattr(owner, 'data_root', owner.workspace.parent) / '鲸读科研任务-v1'
        previous = owner.settings.value('research/last_document', '', type=str)
        if restore and previous:
            self.job = Path(previous)
            self._restore_scheduled = True
            self.message = ui_messages.Message(ui_messages.MessageCode.RESEARCH_RESTORING)
            QTimer.singleShot(0, self._restore_previous)

    def message_text(self) -> str:
        """The visible status rendered in the current locale."""
        if isinstance(self.message, ui_messages.Message):
            return ui_messages.render(self.message)
        return str(self.message or "")

    def message_detail(self) -> str:
        """The untranslated diagnostic behind the status, when there is one."""
        return (ui_messages.detail_text(self.message)
                if isinstance(self.message, ui_messages.Message) else "")

    @Property('QVariantMap', notify=changed)
    def state(self):
        blocks = []
        for block in self.data.get('blocks', []):
            item = dict(block)
            item['display_translation'] = reading_text(item.get('translation', ''))
            blocks.append(item)
        prose = [block for block in blocks if not block.get('visual')]
        witness_conflicts = sum(block.get('witness', {}).get('status') in {'check', 'review'}
                                and not block.get('reviewed') for block in prose)
        title = (self.manifest.get('title') or ui_messages.render(
            ui_messages.Message(ui_messages.MessageCode.RESEARCH_TITLE)))
        origin_code = {
            'mixed': ui_messages.MessageCode.RESEARCH_ORIGIN_MIXED,
            'native': ui_messages.MessageCode.RESEARCH_ORIGIN_NATIVE,
        }.get(self.data.get('origin'), ui_messages.MessageCode.RESEARCH_ORIGIN_SCAN)
        origin = (ui_messages.render(ui_messages.Message(origin_code)) if self.data else '')
        return dict(title=title,
            page=self.manifest.get('page', 1), pages=self.manifest.get('page_count', 0),
            busy=self.process is not None, status=self.message_text(), image=self.image,
            batchMode=self.owner.settings.value('research/batch_mode', False, type=bool),
            batchDraft=self.data.get('translation_mode') == 'batch',
            batch=self.manifest.get('batch', {}),
            routes=self.manifest.get('routes', {}),
            statusCode=(self.message.code if isinstance(self.message, ui_messages.Message) else ''),
            statusDetail=self.message_detail(),
            blocks=blocks, hasPage=bool(self.data), position=self.position,
            termCandidates=self.term_candidates, termRules=self.manifest.get('term_rules', []),
            approved=self.data.get('approved', False), terms=self.manifest.get('terms', ''),
            pendingBlocks=sum(not block.get('translation') for block in prose),
            translatedBlocks=sum(bool(block.get('translation')) for block in prose),
            correctedBlocks=sum(bool(block.get('reviewed')) for block in prose),
            witnessConflicts=witness_conflicts,
            witnessChecked=sum(block.get('witness', {}).get('status') in {'agree', 'check', 'review'} for block in prose),
            pageWidth=self.data.get('width', 612), pageHeight=self.data.get('height', 792),
            origin=origin)

    def _open(self, job):
        self.job = job
        self.manifest = load(job / 'document.json')
        self.owner.settings.setValue('research/last_document', str(job))
        self._reload()

    def _reload(self):
        self.position = {}
        if self.job:
            self.term_candidates = candidates(self.job, self.manifest['page'], self.manifest.get('term_rules', []))
            page = self.manifest['page']
            self.image = render_page(self.job, page).as_uri()
            path = page_dir(self.job, page) / 'page.json'
            self.data = load(path) if path.exists() else {}
            if self.data and self.data.get('revision') == LAYOUT_REVISION:
                positioned = position_path(self.job, page, self.data)
                if positioned.exists():
                    self.position = load(positioned)
                elif not self.process and not self._position_scheduled and str(positioned) != self._position_failed:
                    self._position_scheduled = True
                    QTimer.singleShot(0, self._prepare_position)
            if self.data and self.data.get('revision') != LAYOUT_REVISION and not self.process and not self._upgrade_scheduled:
                self._upgrade_scheduled = True
                self.message = ui_messages.Message(ui_messages.MessageCode.RESEARCH_UPGRADING)
                QTimer.singleShot(0, self._upgrade_current)
            if self.data.get('source_repaired') and not self.data.get('approved'):
                self.message = ui_messages.Message(ui_messages.MessageCode.RESEARCH_SOURCE_REPAIRED)
            elif self.data.get('layout_reclassified') and not self.data.get('approved'):
                self.message = ui_messages.Message(ui_messages.MessageCode.RESEARCH_LAYOUT_RECLASSIFIED)
            elif self.data.get('translation_rules_changed'):
                self.message = ui_messages.Message(ui_messages.MessageCode.RESEARCH_RULES_CHANGED)
        self.changed.emit()

    @Slot()
    def _prepare_position(self):
        self._position_scheduled = False
        if self.job and self.data and not self.process:
            self.run('position')

    @Slot()
    def _restore_previous(self):
        self._restore_scheduled = False
        if not self.process and self.job:
            self.run('restore')

    @Slot()
    def _upgrade_current(self):
        self._upgrade_scheduled = False
        if self.process or not self.job:
            return
        path = page_dir(self.job, self.manifest['page']) / 'page.json'
        if path.exists() and load(path).get('revision') != LAYOUT_REVISION:
            self.run('upgrade')

    @Slot()
    def pick(self):
        if self.process:
            return
        path, _ = choose_open_file(
            None,
            ui_messages.render(ui_messages.Message(ui_messages.MessageCode.DIALOG_RESEARCH_PDF)),
            str(self.library.parent / '鲸读科研'), 'PDF (*.pdf)')
        if path:
            try:
                self.openPath(str(self.owner.file_access.selected(path, read_only=True)))
            except OSError as exc:
                self.message = ui_messages.message_from_worker_error(exc)
                self.changed.emit()

    @Slot(str)
    def openPath(self, path):
        if self.process:
            return
        try:
            self._open(import_pdf(path, self.library))
            self.message = ui_messages.Message(ui_messages.MessageCode.RESEARCH_READY)
        except Exception as exc:
            self.message = ui_messages.Message('', (), str(exc)[:500])
        self.changed.emit()

    @Slot(int)
    @guarded
    def navigate(self, number):
        if self.process or not self.job:
            return
        self.manifest['page'] = max(1, min(number, self.manifest['page_count']))
        save(self.job / 'document.json', self.manifest)
        self.message = ui_messages.Message(ui_messages.MessageCode.RESEARCH_PAGE_READY)
        self._reload()

    @Slot(str)
    @guarded
    def run(self, action):
        if self._closed or self.process or not self.job or action not in {'analyze', 'translate', 'batch', 'upgrade', 'restore', 'position'}:
            return
        if action == 'translate' and not self.data.get('approved'):
            self.message = ui_messages.Message(ui_messages.MessageCode.RESEARCH_NEED_APPROVAL)
            self.changed.emit()
            return
        self.manifest = load(self.job / 'document.json')
        features = []
        if action in {'translate', 'batch'}:
            features.append('translation')
        if action == 'analyze' or (action == 'batch' and any(
                not (page_dir(self.job, n) / 'page.json').exists()
                for n in range(1, self.manifest['page_count'] + 1))):
            features.append('ocr')
        routes = {}
        for feature in features:
            binding = self.manifest.get('routes', {}).get(feature)
            if binding:
                selected = self.owner.providers.route(binding['id'])
                if any(selected[k] != binding[k] for k in ('api_base', 'model', 'auth_provider')):
                    raise ValueError(self.owner.providers.mismatched_route())
            else:
                selected = self.owner.providers.feature_route(feature)
            routes[feature] = selected
        self.process = QProcess(self)
        if action == 'position':
            self._position_message = self.message
        self._action = action
        self.active_routes = routes
        self._paused = False
        self._buffer = ''
        number = 0 if action == 'restore' else self.manifest['page']
        route = routes.get('translation', {})
        ocr_route = routes.get('ocr', {})
        try:
            prepare_worker(self.process, self.owner, 'research_worker.py',
                [action, str(self.job), str(number), '--route', json.dumps(route),
                 '--ocr-route', json.dumps(ocr_route)], additional_routes=routes.items())
        except Exception:
            self.process.deleteLater()
            self.process = None
            self.active_routes = {}
            self._action = ''
            raise
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.finished.connect(self._done)
        self.process.errorOccurred.connect(self._error)
        # A failure may replace this placeholder with the raw worker detail, but
        # only while the placeholder is still the visible message.  ``restore``
        # keeps its own recovery message, exactly as before.
        self._generic_status = action != 'restore'
        self.message = ui_messages.Message(ui_messages.MessageCode.RESEARCH_RESTORING
                                             if action == 'restore'
                                             else ui_messages.MessageCode.RESEARCH_WORKING)
        self.process.start()
        self.changed.emit()

    def _read(self):
        if not self.process:
            return
        self._buffer += bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')
        while '\n' in self._buffer:
            line, self._buffer = self._buffer.split('\n', 1)
            try:
                payload = json.loads(line)
            except ValueError:
                continue
            status = payload.get('status') if isinstance(payload, dict) else None
            code = ui_messages.code_from_worker(payload) if isinstance(payload, dict) else ''
            if code:
                args = payload.get('message_args') or []
                if not isinstance(args, (list, tuple)):
                    args = []
                self.message = ui_messages.Message(
                    code, tuple(args), str(payload.get('detail') or '')[:500])
                self._generic_status = False
            elif isinstance(status, str) and status:
                self.message = status
                self._generic_status = False
        if self._action == 'batch' and self.job:
            manifest = load(self.job / 'document.json')
            changed_page = self.manifest.get('page') != manifest.get('page')
            self.manifest = manifest
            if changed_page:
                self._reload()
        self.changed.emit()

    def _error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._generic_status = False
            self.message = ui_messages.Message(ui_messages.MessageCode.PROCESS_NOT_STARTED)
            self._done(1)

    @guarded
    def _done(self, code=0, *_):
        action = self._action
        if action == 'position' and code and self.job and self.data:
            self._position_failed = str(position_path(self.job, self.manifest['page'], self.data))
        if self.process:
            self._read()
            detail = bytes(self.process.readAllStandardError()).decode('utf-8', errors='replace')
            # The placeholder is a state, not a localized string: a worker that
            # already reported its own status keeps it, and rewording or
            # translating the UI can never change whether the detail is shown.
            if code and self._generic_status:
                self.message = ui_messages.Message(
                    ui_messages.MessageCode.RESEARCH_FAILED, (), detail[-350:])
            self._generic_status = False
            self.process.deleteLater()
            self.process = None
            self._action = ''
            self.active_routes = {}
        if action == 'restore':
            if code:
                self.job = None
                self.manifest = {}
                self.data = {}
                self.image = ''
                self.message = ui_messages.Message(
                    ui_messages.MessageCode.RESEARCH_RESTORE_FAILED)
                self.changed.emit()
                return
        if self.job:
            self.manifest = load(self.job / 'document.json')
            if action == 'batch' and code and self.manifest.get('batch', {}).get('state') == 'running':
                self.manifest['batch']['state'] = 'paused' if self._paused else 'interrupted'
                save(self.job / 'document.json', self.manifest)
        if action == 'position' and not code and self._position_message is not None:
            self.message = self._position_message
            self._position_message = None
        self._reload()

    @Slot(bool)
    def setBatchMode(self, enabled):
        if not self.process:
            self.owner.settings.setValue('research/batch_mode', enabled)
            self.changed.emit()

    @Slot()
    def stop(self):
        if self.process:
            self._paused = True
            self.process.terminate()
            process = self.process
            QTimer.singleShot(3000, lambda: process.kill() if self.process is process else None)
            # The user's pause notice must survive the terminated process.
            self._generic_status = False
            self.message = ui_messages.Message(ui_messages.MessageCode.RESEARCH_PAUSED)
            self.changed.emit()

    def shutdown(self):
        self._closed = True
        if self.process:
            self.process.terminate()
            self.process.waitForFinished(3000)

    @Slot(int, str)
    def saveSource(self, index, text):
        if self.process or not self.data:
            return
        try:
            edit_source(self.job, self.manifest['page'], index, text)
            self.message = ui_messages.Message(ui_messages.MessageCode.RESEARCH_SOURCE_SAVED)
            self._reload()
        except Exception as exc:
            self.message = ui_messages.Message('', (), str(exc)[:500])
            self.changed.emit()

    @Slot()
    @guarded
    def approve(self):
        if not self.process and self.data:
            self.data['approved'] = True
            save(page_dir(self.job, self.manifest['page']) / 'page.json', self.data)
            corrected = sum(bool(block.get('reviewed')) for block in self.data.get('blocks', [])
                            if not block.get('visual'))
            self.message = ui_messages.Message(
                ui_messages.MessageCode.RESEARCH_APPROVED, (corrected,)) if corrected else \
                ui_messages.Message(ui_messages.MessageCode.RESEARCH_CONFIRMED)
            self._reload()

    @Slot(str)
    @guarded
    def saveTerms(self, text):
        if self.process or not self.job:
            return
        if self.manifest.get('terms') == text:
            return
        self.manifest['terms'] = text
        save(self.job / 'document.json', self.manifest)
        self.message = ui_messages.Message(ui_messages.MessageCode.RESEARCH_TERMS_UPDATED)
        self._reload()

    @Slot(str, str, str, int)
    @guarded
    def confirmTerm(self, term, mode, target, page):
        if self.process or not self.job:
            return
        term, target = term.strip(), target.strip()
        if not term or mode not in {'translate', 'fixed', 'keep'} or page < 0 or page > self.manifest['page_count']:
            raise ValueError('请填写有效术语和处理方式。')
        if mode == 'fixed' and not target:
            raise ValueError('固定译名不能为空。')
        rules = list(self.manifest.get('term_rules', []))
        self.manifest.setdefault('term_rule_history', []).append(rules)
        rules = [r for r in rules if not (r['term'] == term and r['page'] == page)]
        rules.append(dict(term=term, mode=mode, target=target if mode == 'fixed' else '', page=page))
        self.manifest['term_rules'] = rules
        save(self.job / 'document.json', self.manifest)
        self.message = ui_messages.Message(ui_messages.MessageCode.RESEARCH_RULES_CONFIRMED)
        self._reload()

    @Slot(str, int)
    @guarded
    def removeTerm(self, term, page):
        if self.process or not self.job:
            return
        rules = list(self.manifest.get('term_rules', []))
        self.manifest.setdefault('term_rule_history', []).append(rules)
        self.manifest['term_rules'] = [r for r in rules if not (r['term'] == term and r['page'] == page)]
        save(self.job / 'document.json', self.manifest)
        self._reload()

    @Slot()
    def confirmAbbreviations(self):
        for item in list(self.term_candidates):
            if item['page'] == 0:
                self.confirmTerm(item['term'], 'keep', '', 0)

    @Slot()
    def export(self):
        if self.job and not self.process:
            try:
                target = export_html(self.job)
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
                self.message = ui_messages.Message(ui_messages.MessageCode.RESEARCH_EXPORTED)
            except Exception as exc:
                self.message = ui_messages.Message('', (), str(exc)[:500])
            self.changed.emit()
