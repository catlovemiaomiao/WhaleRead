import json
import os
import subprocess
import sys
import threading
import unittest
import tempfile
from unittest.mock import Mock, patch
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'runtime/lib'))
from ask_ai import AskAI, context_window, use_deepseek_nonthinking
from provider_profiles import ProviderProfiles
from credential_store import MemoryCredentialStore
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QObject, QSettings


class AskTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def make_assistant(self, root):
        owner = QObject()
        owner.root = ROOT
        owner.settings = QSettings(str(root/'settings.ini'), QSettings.IniFormat)
        owner.providers = ProviderProfiles(owner.settings, credential_store=MemoryCredentialStore())
        return owner, AskAI(owner)

    def test_reopen_pending_preserves_question_and_context(self):
        with tempfile.TemporaryDirectory() as raw:
            owner, ai = self.make_assistant(Path(raw))
            ai.openOriginal('Biscuit', 'original context', 'book')
            session = ai.session_id
            ai.process = Mock()
            ai._question = '为什么这样翻译？'
            opened = Mock(); ai.opened.connect(opened)
            ai.openOriginal('new quote', 'new context', 'other book')
            self.assertEqual(opened.call_count, 1)
            self.assertEqual((ai.quote, ai.context, ai._question, ai.session_id), ('Biscuit', 'original context', '为什么这样翻译？', session))
            ai.openSelection('unused-column', 99, 'new quote')
            self.assertEqual(opened.call_count, 2)
            self.assertIn('正在回答', ai.notice)
            ai.process = None

    def test_cancel_and_timeout_unlock_and_ignore_late_completion(self):
        with tempfile.TemporaryDirectory() as raw:
            owner, ai = self.make_assistant(Path(raw))
            old = Mock(); ai.process = old; ai._frozen_answer_locale = 'en'; ai.cancel()
            self.assertIsNone(ai.process); old.kill.assert_called_once()
            self.assertEqual('', ai._frozen_answer_locale)
            current = Mock(); ai.process = current; ai.output = bytearray(b'new')
            ai._done(old, 1)
            self.assertIs(ai.process, current)
            self.assertEqual(ai.output, b'new')
            ai._timeout()
            self.assertIsNone(ai.process)
            self.assertIn('190 秒', ai.error)

    def test_finished_answer_survives_reopening_same_selection(self):
        with tempfile.TemporaryDirectory() as raw:
            owner, ai = self.make_assistant(Path(raw))
            ai.openOriginal('word', 'context', 'book')
            ai.answer = '解释'; ai.history = [{'role':'assistant','content':'解释'}]
            ai.openOriginal('word', 'context', 'book')
            self.assertEqual(ai.answer, '解释'); self.assertEqual(len(ai.history), 1)
            ai.openOriginal('other word', 'context', 'book')
            self.assertEqual(ai.answer, ''); self.assertEqual(ai.history, [])

    def test_reader_task_keeps_question_accessible_until_explicitly_cleared(self):
        with tempfile.TemporaryDirectory() as raw:
            _, ai = self.make_assistant(Path(raw))
            ai.openOriginal('选文', '[当前段]\nOriginal context.', '测试图书')
            pending = Mock()
            ai.process = pending
            ai._question = '这处是什么意思？'
            self.assertTrue(ai.state['taskVisible'])
            self.assertTrue(ai.state['busy'])
            self.assertEqual(ai.state['taskStatus'], '回答中…')

            # A running request cannot be accidentally hidden; cancel keeps a
            # recoverable result row until the reader explicitly clears it.
            ai.dismissTask()
            self.assertTrue(ai.state['taskVisible'])
            ai.cancel()
            pending.kill.assert_called_once()
            self.assertTrue(ai.state['taskVisible'])
            self.assertEqual(ai.state['taskStatus'], '请求未完成')
            ai.dismissTask()
            self.assertFalse(ai.state['taskVisible'])

            ai.answer = '上一次的回答'
            ai.error = ''
            ai.task_dismissed = False
            opened = Mock()
            ai.opened.connect(opened)
            ai.reopen()
            self.assertTrue(ai.state['taskVisible'])
            self.assertEqual(ai.state['taskStatus'], '已回答')
            self.assertEqual(ai.state['taskQuestion'], '这处是什么意思？')
            opened.assert_called_once()

    def test_custom_api_settings_normalize_and_keep_separate(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            owner, ai = self.make_assistant(root)
            owner.settings.setValue('model/profile', 'translation-unchanged')
            ai.answer = 'keep this answer'
            with patch('ask_ai.Path.home', return_value=root):
                self.assertTrue(ai.configure('custom', 'https://example.test/v1/chat/completions/', 'test-model', 'test-only-key'))
                self.assertEqual(ai.state['endpoint'], 'https://example.test/v1')
                self.assertEqual(ai.answer, 'keep this answer')
                self.assertFalse((root/'Library/Application Support/WhaleRead/ask-key.json').exists())
                self.assertEqual(owner.providers.key(owner.providers.selected('ask')), 'test-only-key')
                self.assertTrue(ai.configure('custom', 'https://example.test/v1', 'other-model', ''))
                self.assertEqual(owner.providers.key(owner.providers.selected('ask')), 'test-only-key')
                self.assertFalse(ai.configure('custom', 'https://other.test/v1', 'other-model', ''))
                self.assertEqual(ai.state['endpoint'], 'https://example.test/v1')
                self.assertFalse(ai.configure('custom', 'https://user:password@example.test/v1', 'model', ''))
            self.assertEqual(owner.settings.value('model/profile'), 'translation-unchanged')

    def test_public_build_keeps_only_the_user_configured_provider(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            owner, ai = self.make_assistant(root)
            with patch('ask_ai.Path.home', return_value=root):
                self.assertTrue(ai.configure('custom', 'https://api.deepseek.com', 'deepseek-v4-flash-vision-exp', 'test-only-key'))
            ai.openOriginal('选文', '[当前段]\nOriginal context.', '书')
            self.assertEqual(ai.state['provider'], 'custom')
            self.assertEqual(ai.state['destination'], 'https://api.deepseek.com/v1')
            self.assertFalse(ai.selectProvider('dgx'))
            self.assertEqual(ai.state['provider'], 'custom')
            self.assertEqual(ai.state['destination'], 'https://api.deepseek.com/v1')
            saved = QSettings(owner.settings.fileName(), QSettings.IniFormat)
            self.assertEqual(saved.value('ask/provider'), 'custom')
            self.assertTrue(ai.selectProvider('custom'))
            self.assertEqual(ai.state['destination'], 'https://api.deepseek.com/v1')

    def test_custom_provider_requires_a_saved_route(self):
        with tempfile.TemporaryDirectory() as raw:
            _, ai = self.make_assistant(Path(raw))
            self.assertFalse(ai.selectProvider('custom'))
            self.assertEqual(ai.state['provider'], 'custom')
            self.assertIn('还没配置完整', ai.state['configMessage'])

    def test_context_bounds(self):
        self.assertEqual(context_window(['a'], 0), '[当前段]\na')
        text = context_window(list('abcdefg'), 3)
        self.assertEqual(text.count('['), 5)
        self.assertIn('[当前段]\nd', text)
        with self.assertRaises(ValueError):
            context_window([], 0)

    def test_only_official_deepseek_v4_uses_nonthinking_mode(self):
        self.assertTrue(use_deepseek_nonthinking('https://api.deepseek.com', 'deepseek-v4-flash-vision-exp'))
        self.assertTrue(use_deepseek_nonthinking('https://api.deepseek.com/v1', 'deepseek-v4-pro'))
        self.assertFalse(use_deepseek_nonthinking('https://example.test/v1', 'deepseek-v4-pro'))
        self.assertFalse(use_deepseek_nonthinking('https://deepseek.com.example.test', 'deepseek-v4-pro'))
        self.assertFalse(use_deepseek_nonthinking('https://api.deepseek.com', 'other-compatible-model'))

    def test_worker(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                assert self.path == '/v1/chat/completions'
                assert payload['messages'][0]['content'] == 'original'
                assert payload['max_tokens'] == 4096
                assert 'thinking' not in payload
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'choices':[{'message':{'content':'解释'}, 'finish_reason':'stop'}]}).encode())
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            p = subprocess.run([sys.executable, str(ROOT/'runtime/tools/ask_ai_request.py')], input=json.dumps(dict(endpoint=f'http://127.0.0.1:{server.server_port}/v1', key='', model='test', messages=[dict(role='user',content='original')])), capture_output=True, text=True, timeout=10)
            self.assertEqual(p.returncode, 0, p.stdout)
            self.assertEqual(json.loads(p.stdout)['answer'], '解释')
        finally:
            server.shutdown()
            server.server_close()

    def test_worker_disables_deepseek_thinking_when_requested(self):
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                assert payload['thinking'] == {'type': 'disabled'}
                assert payload['max_tokens'] == 4096
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({'choices':[{'message':{'content':'简洁完整的解释。'}, 'finish_reason':'stop'}]}).encode())
            def log_message(self, *args):
                pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = dict(endpoint=f'http://127.0.0.1:{server.server_port}/v1', key='', model='test',
                           messages=[dict(role='user', content='original')], disable_thinking=True)
            p = subprocess.run([sys.executable, str(ROOT/'runtime/tools/ask_ai_request.py')], input=json.dumps(request), capture_output=True, text=True, timeout=10)
            self.assertEqual(p.returncode, 0, p.stdout)
            self.assertEqual(json.loads(p.stdout), {'answer':'简洁完整的解释。', 'truncated':False})
        finally:
            server.shutdown()
            server.server_close()

    def test_worker_continues_a_length_limited_answer(self):
        class Handler(BaseHTTPRequestHandler):
            calls = 0

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                type(self).calls += 1
                if self.calls == 1:
                    content, finish = '前半段，继续', 'length'
                else:
                    self.assert_continuation(payload)
                    content, finish = '继续完成。', 'stop'
                self.send_response(200)
                self.end_headers()
                self.wfile.write(json.dumps({
                    'choices': [{'message': {'content': content}, 'finish_reason': finish}]
                }).encode())

            @staticmethod
            def assert_continuation(payload):
                assert payload['messages'][-2] == {'role': 'assistant', 'content': '前半段，继续'}
                assert payload['messages'][-1]['role'] == 'user'
                assert '从截断处继续' in payload['messages'][-1]['content']

            def log_message(self, *args):
                pass

        server = HTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            request = dict(endpoint=f'http://127.0.0.1:{server.server_port}/v1', key='',
                           model='test', messages=[dict(role='user', content='original')])
            result = subprocess.run(
                [sys.executable, str(ROOT/'runtime/tools/ask_ai_request.py')],
                input=json.dumps(request), capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(json.loads(result.stdout), {
                'answer': '前半段，继续完成。', 'truncated': False})
            self.assertEqual(Handler.calls, 2)
        finally:
            server.shutdown()
            server.server_close()

    def test_incomplete_answer_cannot_be_appended_to_notes(self):
        with tempfile.TemporaryDirectory() as raw:
            _, ai = self.make_assistant(Path(raw))
            ai._question = '请完整解释'
            ai.answer = '被截断的回答'
            ai.answer_id = 'partial-answer'
            ai.answer_truncated = True
            ai.origin = {'document_id': 'stable'}
            self.assertFalse(ai.state['canSaveToNote'])
            self.assertEqual(ai.state['taskStatus'], '回答未完整')
