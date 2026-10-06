"""Read-only reading questions with explicit original-language context."""
import json
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit
import answer_language
import ui_messages
from PySide6.QtCore import QObject, QProcess, QTimer, Property, Signal, Slot
from provider_transport import use_deepseek_nonthinking
from worker_context import prepare_worker


def context_window(segments, index):
    if not 0 <= index < len(segments):
        raise ValueError('无法定位原文段落')
    return '\n\n'.join(f'[{"当前段" if i == index else "原文段 " + str(i+1)}]\n{segments[i]}'
                       for i in range(max(0, index-2), min(len(segments), index+3)))


class AskAI(QObject):
    changed = Signal()
    opened = Signal()

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.context = self.quote = self.title = self.answer = self.error = ''
        self.notice = self._question = ''
        self.config_message = ''
        self.origin = {}
        self.answer_id = self.saved_answer_id = ''
        self.answer_truncated = False
        self.note_notice = ''
        self.task_dismissed = False
        self.session_id = 0
        self.process = None
        self.history = []
        self.output = bytearray()
        self.deadline = QTimer(self)
        self.deadline.setSingleShot(True)
        self.deadline.timeout.connect(self._timeout)
        # The answer language of the request in flight, frozen when it starts.
        # While a request runs this value does not follow the interface, and the
        # saved answer keeps it as metadata rather than being rewritten.
        self._frozen_answer_locale = ''
        self._answer_locale = ''

    @Property(str, notify=changed)
    def answerLocale(self):  # noqa: N802
        """Stored answer-language preference: follow_ui / zh-CN / en."""
        return answer_language.stored(self.owner.settings)

    @Property(str, notify=changed)
    def effectiveAnswerLocale(self):  # noqa: N802
        """The answer language in force: the frozen one while a request runs."""
        if self._frozen_answer_locale:
            return self._frozen_answer_locale
        locale = getattr(getattr(self.owner, 'locale', None), 'effective', answer_language.CHINESE)
        return answer_language.resolve(self.answerLocale, locale)

    @Property(str, notify=changed)
    def answerLocaleLabel(self):  # noqa: N802
        return answer_language.label(self.answerLocale)

    @Property(str, notify=changed)
    def effectiveAnswerLocaleLabel(self):  # noqa: N802
        """Localized label for the concrete language frozen for this request."""
        return answer_language.effective_label(self.effectiveAnswerLocale)

    @Property('QVariantList', notify=changed)
    def answerLocaleOptions(self):  # noqa: N802
        return answer_language.options()

    @Slot(str, result=bool)
    def setAnswerLocale(self, value):  # noqa: N802
        """Persist the answer language; never re-requests or rewrites anything."""
        answer_language.store(self.owner.settings, value)
        self.owner.settings.sync()
        self.changed.emit()
        return True

    @Property('QVariantMap', notify=changed)
    def state(self):
        s = self.owner.settings
        try:
            route = self.owner.providers.feature_route('ask')
        except ValueError:
            route = {}
        note_saved = bool(self.answer_id and self.saved_answer_id == self.answer_id)
        can_save_note = bool(self.process is None and self.answer_id and self.answer
                             and not self.answer_truncated
                             and self._question and self.origin and not note_saved)
        task_visible = bool(not self.task_dismissed and self._question
                            and (self.process is not None or self.answer or self.error))
        task_status = ('回答中…' if self.process is not None else
                       '回答未完整' if self.answer_truncated else
                       '已回答' if self.answer else '请求未完成')
        return dict(context=self.context, quote=self.quote, title=self.title, answer=self.answer,
                    error=self.error, notice=self.notice, question=self._question, sessionId=self.session_id,
                    configMessage=self.config_message,
                    canSaveToNote=can_save_note, noteSaved=note_saved,
                    noteMessage=self.note_notice,
                    noteTarget=str(self.origin.get('title') or ''),
                    truncated=self.answer_truncated,
                    taskVisible=task_visible, taskStatus=task_status,
                    taskQuestion=self._question, taskTitle=self.title,
                    answerLocale=self._answer_locale,
                    effectiveAnswerLocale=self.effectiveAnswerLocale,
                    busy=self.process is not None,
                    provider='custom',
                    endpoint=route.get('api_base', ''), model=route.get('model', ''),
                    profile=route.get('id', ''), destination=route.get('api_base', ''))

    def _column(self, column_id):
        for column in getattr(self.owner, '_reading_columns', []):
            if column.columnId == column_id:
                return column
            if column._epub_peer and column._epub_peer.columnId == column_id:
                return column._epub_peer
        raise ValueError('原阅读栏已经关闭，无法确认段落归属。')

    @staticmethod
    def _origin(column, anchor, excerpt):
        root = column._group_parent or column
        if not root._reader_identity or root._epub_anchor_signature(anchor) is None:
            raise ValueError('这处选文没有稳定的段落锚点。')
        return {
            'reader_identity': root._reader_identity,
            'document_id': root._document_id(),
            'anchor': dict(anchor),
            'excerpt': str(excerpt or '')[:240],
            'edition': str(anchor.get('edition') or 'translation'),
            'title': root.readerTitle,
        }

    def _open_original(self, quote, context, title, origin=None):
        if self.process:
            self.reopen()
            return
        origin = dict(origin or {})
        if ((quote, context, title) == (self.quote, self.context, self.title)
                and self.context and origin == self.origin):
            self.reopen()
            return
        self.session_id += 1
        self.notice = self._question = ''
        self.quote, self.context, self.title = quote, context, title
        self.answer = self.error = ''
        self._answer_locale = ''
        self.origin = origin
        self.answer_id = self.saved_answer_id = ''
        self.answer_truncated = False
        self.note_notice = ''
        self.task_dismissed = False
        self.history = []
        if len(context) > 24000 or not context.strip():
            self.context = ''
            self.error = '原文上下文为空或超过 24000 字符，未发送。请缩小范围。'
        self.changed.emit()
        self.opened.emit()

    @Slot(str, int, str)
    def openSelection(self, column_id, row, quote):
        if self.process:
            self.reopen()
            return
        try:
            column = self._column(column_id)
            _, anchors = column._alignment()
            if not column.readingJobPath or not 0 <= row < len(anchors):
                raise ValueError('没有可验证的原文映射；不能用译文冒充原文。请打开关联译本或 EPUB 原版。')
            if quote not in column.reader.rows[row]:
                raise ValueError('选文已变化，请重新选择')
            sys.path.insert(0, str(self.owner.root/'runtime/tools'))
            import post_edit
            book = post_edit.snapshot(Path(column.readingJobPath))
            anchor = column._row_anchor(row)
            origin = self._origin(column, anchor, column.reader.rows[row])
            self._open_original(quote, context_window(book['segments'], anchors[row]['segment']-1),
                                column.readerTitle, origin)
        except Exception as exc:
            self.session_id += 1
            self.notice = self._question = ''
            self.context = self.answer = ''
            self._answer_locale = ''
            self.origin = {}
            self.answer_id = self.saved_answer_id = ''
            self.answer_truncated = False
            self.note_notice = ''
            self.history = []
            self.quote = quote
            self.error = str(exc)
            self.changed.emit()
            self.opened.emit()

    @Slot(str, int, str)
    def openSourceSelection(self, column_id, row, quote):  # noqa: N802
        if self.process:
            self.reopen()
            return
        try:
            column = self._column(column_id)
            sources, anchors = column._alignment()
            if (not column.readingJobPath or not 0 <= row < len(anchors)
                    or not 0 <= row < len(sources) or quote not in sources[row]):
                raise ValueError('原文段落映射已经变化，请重新选择。')
            sys.path.insert(0, str(self.owner.root/'runtime/tools'))
            import post_edit
            book = post_edit.snapshot(Path(column.readingJobPath))
            anchor = column._row_anchor(row)
            origin = self._origin(column, anchor, sources[row])
            self._open_original(quote, context_window(book['segments'], anchors[row]['segment']-1),
                                column.readerTitle, origin)
        except Exception as exc:
            self.session_id += 1
            self.notice = self._question = ''
            self.context = self.answer = ''
            self._answer_locale = ''
            self.origin = {}
            self.answer_id = self.saved_answer_id = ''
            self.answer_truncated = False
            self.note_notice = ''
            self.history = []
            self.quote = quote
            self.error = str(exc)
            self.changed.emit()
            self.opened.emit()

    @Slot(str, 'QVariantMap', str, str, str)
    def openAnchored(self, column_id, raw_anchor, quote, context, title):  # noqa: N802
        if self.process:
            self.reopen()
            return
        try:
            column = self._column(column_id)
            anchor = dict(raw_anchor or {})
            origin = self._origin(column, anchor, anchor.get('source') or quote)
            self._open_original(quote, context, title, origin)
        except Exception as exc:
            self.session_id += 1
            self.notice = self._question = ''
            self.context = self.answer = ''
            self._answer_locale = ''
            self.origin = {}
            self.answer_id = self.saved_answer_id = ''
            self.answer_truncated = False
            self.note_notice = ''
            self.history = []
            self.quote = quote
            self.error = str(exc)
            self.changed.emit()
            self.opened.emit()

    @Slot(str, str, str)
    def openOriginal(self, quote, context, title):
        self._open_original(quote, context, title)

    @Slot()
    def reopen(self):
        self.task_dismissed = False
        self.notice = ('正在回答上一处选文，已为你找回当前问题。可以继续阅读，或取消请求后重新选词。'
                       if self.process else '')
        self.changed.emit()
        self.opened.emit()

    @Slot(str, result=bool)
    def selectProvider(self, provider):  # noqa: N802
        if self.process:
            self.config_message = '当前问答还在进行，请等回答结束后再切换。'
            self.changed.emit()
            return False
        if provider != 'custom':
            return False
        s = self.owner.settings
        pid = self.owner.providers.selected('ask')
        if not self.owner.providers.contains(pid):
            self.config_message = '自定义问答接口还没配置完整。'
            self.changed.emit()
            return False
        self.owner.providers.select('ask', pid)
        s.setValue('ask/provider', 'custom')
        s.sync()
        self.config_message = '已切换为已保存的自定义问答接口，下一次发送生效。'
        self.changed.emit()
        return True

    @Slot(str, str, str, str, result=bool)
    def configure(self, provider, endpoint, model, key):
        if self.process:
            self.config_message = '当前问答还在进行，请先取消请求或等待回答结束，再保存接口设置。'
            self.changed.emit()
            return False
        if provider != 'custom':
            return False
        s = self.owner.settings
        pid = self.owner.providers.save(self.owner.providers.selected('ask'), model,
                                        endpoint, model, key, True)
        if not pid:
            self.config_message = self.owner.providers.message
            self.changed.emit()
            return False
        self.owner.providers.select('ask', pid)
        s.setValue('ask/provider', provider)
        s.sync()
        self.config_message = '问 AI 接口已保存，只影响之后的阅读问答；翻译模型未改变。'
        self.changed.emit()
        return True

    @Slot(str)
    def ask(self, question):
        question = question.strip()
        if self.process or not self.context or not question:
            return
        if len(question) > 3000 or len(self.history) >= 12:
            self._question = question
            self.answer = ''
            self.answer_id = ''
            self.answer_truncated = False
            self.note_notice = ''
            self.task_dismissed = False
            self.error = '问题过长或本次已达 6 轮，请重新选文开启新问答。'
            self.changed.emit()
            return
        self._question = question
        self.answer = ''
        self._answer_locale = ''
        self.error = ''
        self.notice = ''
        self.answer_id = ''
        self.answer_truncated = False
        self.note_notice = ''
        self.task_dismissed = False
        try:
            s = self.owner.settings
            route = self.owner.providers.feature_route('ask')
            self.owner.providers.require_consent('ask', route['id'])
            endpoint, model = route['api_base'], route['model']
            key = self.owner.providers.key(route['id'])
            # Freeze the answer language for this request and its automatic
            # continuation.  A later interface switch must not re-language an
            # answer that is already being written.
            self._frozen_answer_locale = answer_language.resolve(
                answer_language.stored(s), self.owner.locale.effective)
            system = answer_language.system_prompt(self._frozen_answer_locale)
            # The source context is passed through verbatim: the answer language
            # changes the explanation, never the evidence.
            messages = [{'role':'system', 'content':system}, {'role':'user', 'content':json.dumps(dict(book=self.title, selected=self.quote, original_context=self.context), ensure_ascii=False)}]
            messages += self.history + [{'role':'user', 'content':question.strip()}]
            payload = dict(endpoint=endpoint, model=model, key=key, messages=messages,
                           answer_locale=self._frozen_answer_locale,
                           continuation_prompt=answer_language.continuation_prompt(
                               self._frozen_answer_locale),
                           truncated_suffix=answer_language.truncated_suffix(
                               self._frozen_answer_locale),
                           disable_thinking=use_deepseek_nonthinking(endpoint, model))
            process = QProcess(self)
            self.output = bytearray()
            try:
                prepare_worker(process, self.owner, 'ask_ai_request.py', [],
                               input_text=json.dumps(payload, ensure_ascii=False))
            except Exception:
                process.deleteLater()
                raise
            self.process = process
            process.readyReadStandardOutput.connect(lambda: self._read(process))
            process.finished.connect(lambda code, status: self._done(process, code))
            process.errorOccurred.connect(lambda e: self._done(process, 1) if e == QProcess.ProcessError.FailedToStart else None)
            # A response that reaches the provider's 4096-token ceiling gets
            # one automatic continuation request in the worker.
            self.deadline.start(190000)
            process.start()
        except Exception as exc:
            self.error = str(exc)
            # A failure before QProcess owns the request must not leave a stale
            # frozen locale visible after the request has already ended.
            if self.process is None:
                self._frozen_answer_locale = ''
        self.changed.emit()

    def _read(self, process):
        if self.process is process:
            self.output.extend(bytes(process.readAllStandardOutput()))

    def _done(self, process, code=0):
        if self.process is not process:
            process.deleteLater()
            return
        self.deadline.stop()
        self._read(process)
        process.deleteLater()
        self.process = None
        self.notice = ''
        try:
            result = json.loads(self.output)
            if code or result.get('error'):
                raise ValueError(result.get('error', '问答请求失败'))
            self.answer = result['answer']
            self.answer_truncated = bool(result.get('truncated'))
            self._answer_locale = (self._frozen_answer_locale
                                   or answer_language.resolve(answer_language.stored(
                                       self.owner.settings), self.owner.locale.effective))
            self.history += [{'role':'user','content':self._question}, {'role':'assistant','content':self.answer}]
            self.answer_id = uuid.uuid4().hex
        except Exception as exc:
            self._error = ui_messages.message_from_worker_error(str(exc))
            self.error = ui_messages.render(self._error)
        finally:
            # The request is over: the next one resolves its own language.
            self._frozen_answer_locale = ''
        self.changed.emit()

    @Slot(result=bool)
    def saveAnswerToNote(self):  # noqa: N802
        state = self.state
        if not state['canSaveToNote']:
            if self.answer and not self.origin:
                self.note_notice = '这次问答没有绑定到稳定段落，不能自动写入随笔。'
                self.changed.emit()
            return False
        try:
            result = self.owner.appendAskAnswerToParagraphNote(
                self.origin, self._question, self.answer, self.answer_id,
                self._answer_locale)
        except (AttributeError, TypeError, ValueError) as exc:
            result = {'ok': False, 'message': str(exc) or '随笔写入失败。'}
        self.note_notice = str(result.get('message') or '')
        if result.get('ok'):
            self.saved_answer_id = self.answer_id
        self.changed.emit()
        return bool(result.get('ok'))

    @Slot()
    def dismissTask(self):  # noqa: N802
        if self.process is None:
            self.task_dismissed = True
            self.changed.emit()

    @Slot()
    def cancel(self):
        self._stop('已取消本次提问。可以重试，或重新选词；正文未修改。')

    def _timeout(self):
        self._stop('等待回答或自动续答超过 190 秒，已结束本次请求。你可以重试；正文未修改。')

    def _stop(self, message):
        if self.process:
            process, self.process = self.process, None
            self.deadline.stop()
            self.notice = ''
            self._frozen_answer_locale = ''
            process.kill()
            self.error = message
            self.changed.emit()

    def shutdown(self):
        if self.process:
            process = self.process
            process.kill()
            process.waitForFinished(1500)
