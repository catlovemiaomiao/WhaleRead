"""Executable security boundaries, with synthetic credentials and loopback servers."""
import json
import io
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'runtime/lib'), str(ROOT / 'runtime/tools')]
from provider_transport import (chat_request, completion_url, normalize_base, ProviderError,
                                MAX_RESPONSE_BYTES, install_worker_credentials, load_credential)
from credential_store import MemoryCredentialStore, CredentialError
from provider_profiles import ProviderProfiles
from file_access import FileAccessError
import worker_context
from PySide6.QtCore import QSettings
import translate_range as engine


class Server:
    def __init__(self, status=200, body=None, redirect=None):
        self.requests = []
        owner = self
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                data = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.requests.append((self.path, self.headers.get('Authorization', ''), data))
                self.send_response(status)
                if redirect:
                    self.send_header('Location', redirect)
                raw = body if isinstance(body, bytes) else json.dumps(body or {
                    'choices': [{'message': {'content': 'complete reply'}, 'finish_reason': 'stop'}]}).encode()
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass
            def log_message(self, *args):
                pass
        self.server = HTTPServer(('127.0.0.1', 0), Handler)
        self.base = f'http://127.0.0.1:{self.server.server_port}/v1'
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class TransportTests(unittest.TestCase):
    def test_file_authorization_bootstrap_failure_emits_redacted_error_before_any_request(self):
        body = json.dumps(dict(credentials={'profile:qa': 'fictional-private-key'},
                               grants=['fictional-bookmark'])).encode()
        source = io.TextIOWrapper(io.BytesIO(body))
        output = io.StringIO()
        with patch('worker_context.sys.stdin', source), patch('worker_context.sys.stdout', output), \
                patch('file_access.worker_leases', side_effect=FileAccessError('private-path fictional-private-key')), \
                patch('worker_context.runpy.run_path') as run:
            with self.assertRaises(SystemExit) as stopped:
                worker_context.execute_worker(ROOT, 'probe_model.py', [])
        install_worker_credentials({})
        self.assertEqual(stopped.exception.code, 1)
        event = json.loads(output.getvalue())
        self.assertEqual(event['message_code'], 'fileAccessRequired')
        self.assertNotIn('private', output.getvalue())
        run.assert_not_called()

    def request(self, server):
        return chat_request(server.base, 'synthetic-model', 'fictional-credential',
                            [{'role': 'user', 'content': 'fictional book excerpt'}], timeout=3)

    def test_normalizes_root_base_and_complete_endpoint(self):
        self.assertEqual(normalize_base('https://API.DeepSeek.com/'), 'https://api.deepseek.com/v1')
        for value in ('https://example.test/v1', 'https://example.test/v1/chat/completions/'):
            self.assertEqual(completion_url(value), 'https://example.test/v1/chat/completions')
        self.assertEqual(completion_url('http://[::1]:9999/v1'), 'http://[::1]:9999/v1/chat/completions')

    def test_rejects_remote_cleartext_and_credential_urls(self):
        for value in ('http://192.168.1.4/v1', 'http://cloud.test/v1', 'https://user:secret@cloud.test/v1',
                      'https://cloud.test/v1?key=secret', 'https://cloud.test/v1#secret',
                      'https://cloud.test/a/../v1', 'https://cloud.test/%2e%2e/v1',
                      'https://cloud.test:bad/v1', 'https://cloud.test/v1\n'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_base(value)

    def test_sends_only_to_normalized_destination(self):
        with Server() as server:
            text, finish, _ = self.request(server)
            self.assertEqual((text, finish), ('complete reply', 'stop'))
            self.assertEqual(server.requests[0][0:2], ('/v1/chat/completions', 'Bearer fictional-credential'))

    def test_redirect_never_delivers_to_second_host(self):
        with Server() as recipient, Server(status=307, redirect=recipient.base + '/chat/completions') as server:
            with self.assertRaises(ProviderError) as raised:
                self.request(server)
            self.assertEqual(raised.exception.code, 'redirect_blocked')
            self.assertFalse(recipient.requests)

    def test_401_not_retried_and_error_does_not_echo_remote_body(self):
        with Server(status=401, body=b'fictional-credential and fictional book excerpt') as server:
            with patch('translate_range.time.sleep'), self.assertRaises(ProviderError) as raised:
                engine.translate_with_retry(server.base, 'test', 'fictional-credential', 'excerpt', 3, lambda x: x)
            self.assertEqual(len(server.requests), 1)
            self.assertFalse(raised.exception.retryable)
            self.assertNotIn('fictional', str(raised.exception))

    def test_429_bounded_retry(self):
        with Server(status=429) as server:
            with patch('translate_range.time.sleep'), self.assertRaises(ProviderError) as raised:
                engine.translate_with_retry(server.base, 'test', 'fictional-credential', 'excerpt', 3, lambda x: x)
            self.assertEqual(len(server.requests), 3)
            self.assertTrue(raised.exception.retryable)

    def test_oversized_response_and_invalid_json_rejected(self):
        for body, code in ((b'x' * (MAX_RESPONSE_BYTES + 1), 'response_too_large'),
                           (b'not json', 'invalid_response')):
            with Server(body=body) as server, self.assertRaises(ProviderError) as raised:
                self.request(server)
            self.assertEqual(raised.exception.code, code)

    def test_truncation_does_not_create_acceptable_translation(self):
        with Server(body={'choices': [{'message': {'content': 'partial'}, 'finish_reason': 'length'}]}) as server:
            with self.assertRaises(RuntimeError):
                engine.translate_once(server.base, 'test', '', 'excerpt', 3)

    def test_context_credentials_precede_cli_and_cannot_fall_back_to_file(self):
        try:
            install_worker_credentials({'profile:synthetic': 'fictional-pipe-key'})
            self.assertEqual(load_credential(Path('/does/not/exist'), 'profile:synthetic'), 'fictional-pipe-key')
            with self.assertRaises(ProviderError):
                load_credential(Path('/does/not/exist'), 'profile:missing')
        finally:
            install_worker_credentials({})

    def test_worker_pipe_path_preserves_auth_without_secret_file_or_argv(self):
        with tempfile.TemporaryDirectory() as raw, Server() as server:
            args = [sys.executable, '-B', str(ROOT / 'runtime/tools/worker_entry.py'), 'probe_model.py',
                    '--api-base', server.base, '--model', 'synthetic-model',
                    '--auth-path', '', '--auth-provider', 'profile:synthetic']
            body = json.dumps({'credentials': {'profile:synthetic': 'fictional-pipe-key'},
                               'input': 'The harbor was quiet that morning.'})
            result = subprocess.run(args, input=body, text=True, capture_output=True, cwd=raw, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(server.requests[0][1], 'Bearer fictional-pipe-key')
            self.assertNotIn('fictional-pipe-key', result.stdout + result.stderr + ' '.join(args))
            self.assertEqual(list(Path(raw).iterdir()), [])


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = QSettings(str(Path(self.temp.name) / 'settings.ini'), QSettings.IniFormat)
        self.keys = MemoryCredentialStore()
        self.models = ProviderProfiles(self.settings, credential_store=self.keys)

    def save(self, **overrides):
        values = dict(pid='', label='Synthetic Cloud', endpoint='https://example.test/v1',
                      model='test-model', key='fictional-key', auth_required=True)
        values.update(overrides)
        return self.models.save(**values)

    def test_keys_never_enter_settings_and_restore_keeps_feature_choices(self):
        pid = self.save()
        self.assertTrue(pid)
        self.models.select('translation', pid)
        self.models.select('ask', pid)
        self.assertEqual(self.models.selected('review'), 'local_7b')
        self.assertNotIn('fictional-key', Path(self.settings.fileName()).read_text())
        restored = ProviderProfiles(QSettings(self.settings.fileName(), QSettings.IniFormat), credential_store=self.keys)
        self.assertEqual(restored.key(pid), 'fictional-key')
        self.assertEqual(restored.selected('translation'), pid)
        self.assertEqual(restored.selected('ask'), pid)

    def test_invalid_profile_between_valid_profiles_does_not_discard_later_routes(self):
        first = self.save()
        second = self.save(model='second-model')
        rows = json.loads(self.settings.value('providers/profiles_v1'))
        rows.insert(1, dict(rows[0], id='user_' + 'a' * 32, api_base='http://remote.test/v1'))
        self.settings.setValue('providers/profiles_v1', json.dumps(rows))
        restored = ProviderProfiles(self.settings, credential_store=self.keys)
        self.assertTrue(restored.contains(first))
        self.assertTrue(restored.contains(second))
        self.assertFalse(restored.contains('user_' + 'a' * 32))
        self.assertEqual(restored.key(second), 'fictional-key')

    def test_sharing_permission_separate_for_each_feature_and_revocable(self):
        pid = self.save()
        with self.assertRaises(ValueError):
            self.models.require_consent('translation', pid)
        self.models.setConsent('translation', pid, True)
        self.models.require_consent('translation', pid)
        with self.assertRaises(ValueError):
            self.models.require_consent('ask', pid)
        self.models.setConsent('translation', pid, False)
        self.assertFalse(self.models.consented('translation', pid))

    def test_custom_loopback_routes_require_consent_for_every_feature(self):
        for endpoint in ('http://127.0.0.1:18082/v1', 'http://localhost:19000/v1',
                         'http://[::1]:19000/v1'):
            with self.subTest(endpoint=endpoint):
                pid = self.save(endpoint=endpoint, key='', auth_required=False)
                self.assertTrue(self.models.route(pid)['local'])
                self.assertFalse(self.models.route(pid)['builtin'])
                for feature in ('translation', 'review', 'ask', 'ocr'):
                    self.assertTrue(self.models.select(feature, pid))
                    self.assertFalse(self.models.consented(feature, pid))
                    with self.assertRaises(ValueError):
                        self.models.require_consent(feature, pid)
                self.models.setConsent('translation', pid, True)
                self.models.require_consent('translation', pid)
                for feature in ('review', 'ask', 'ocr'):
                    self.assertFalse(self.models.consented(feature, pid))
                restored = ProviderProfiles(QSettings(self.settings.fileName(), QSettings.IniFormat),
                                            credential_store=self.keys)
                self.assertTrue(restored.consented('translation', pid))
                restored.setConsent('translation', pid, False)
                reopened = ProviderProfiles(QSettings(self.settings.fileName(), QSettings.IniFormat),
                                            credential_store=self.keys)
                self.assertFalse(reopened.consented('translation', pid))

    def test_unconsented_loopback_cannot_prepare_a_worker_or_read_credentials(self):
        pid = self.save(endpoint='http://127.0.0.1:19000/v1')
        owner = Mock(root=ROOT, providers=self.models, file_access=None)
        for feature, worker in (('translation', 'translate_range.py'), ('review', 'post_edit.py'),
                                ('ask', 'ask_ai_request.py'), ('ocr', 'research_worker.py')):
            with self.subTest(feature=feature):
                process = Mock()
                with patch.object(self.models, 'key', wraps=self.models.key) as key:
                    with self.assertRaises(ValueError):
                        worker_context.prepare_worker(process, owner, worker, [],
                            route=self.models.route(pid), feature=feature)
                    key.assert_not_called()
                process.setProgram.assert_not_called()
                process.setArguments.assert_not_called()
                process.started.connect.assert_not_called()
                self.models.setConsent(feature, pid, True)
                worker_context.prepare_worker(process, owner, worker, [],
                    route=self.models.route(pid), feature=feature)
                process.started.connect.call_args.args[0]()
                body = json.loads(process.write.call_args.args[0])
                self.assertEqual(body['credentials'], {'profile:' + pid: 'fictional-key'})
                self.assertNotIn('fictional-key', str(process.setArguments.call_args))
                self.models.setConsent(feature, pid, False)
                with self.assertRaises(ValueError):
                    worker_context.prepare_worker(Mock(), owner, worker, [],
                        route=self.models.route(pid), feature=feature)

    def test_changed_model_gets_new_identity_and_old_route_survives(self):
        old = self.save()
        self.models.setConsent('translation', old, True)
        new = self.save(pid=old, model='another-model', key='')
        self.assertNotEqual(new, old)
        self.assertEqual(self.models.route(old)['model'], 'test-model')
        self.assertEqual(self.models.key(new), 'fictional-key')
        self.assertFalse(self.models.consented('translation', new))

    def test_changed_destination_cannot_reuse_key(self):
        old = self.save()
        self.assertEqual(self.save(pid=old, endpoint='https://other.test/v1', key=''), '')
        self.assertEqual(self.models.route(old)['api_base'], 'https://example.test/v1')

    def test_missing_key_fails_closed_and_keychain_failure_leaves_settings(self):
        self.assertEqual(self.save(key=''), '')
        before = list(self.models.profiles)
        with patch.object(self.keys, 'put', side_effect=CredentialError('OSStatus -1')):
            self.assertEqual(self.save(), '')
        self.assertEqual(self.models.profiles, before)

    def test_busy_feature_cannot_select_or_edit_its_profile(self):
        pid = self.save()
        self.models.select('ask', pid)
        self.models.locked = lambda feature: feature == 'ask'
        self.assertFalse(self.models.select('ask', 'local_7b'))
        self.assertEqual(self.save(pid=pid, label='renamed'), '')
        self.assertEqual(self.models.route(pid)['label'], 'Synthetic Cloud')

    def test_preset_and_missing_routes_are_not_silently_replaced(self):
        for pid in ('local_7b', 'local_1_8b'):
            with self.subTest(pid=pid):
                original = self.models.route(pid)
                self.assertEqual(self.save(pid=pid), '')
                self.assertFalse(self.models.forgetKey(pid))
                self.assertEqual(self.models.route(pid), original)
                self.assertTrue(self.models.consented('translation', pid))
        with self.assertRaises(ValueError):
            self.models.route('missing-task-profile')

    def test_preview_18b_choice_restores_without_custom_profile_or_key(self):
        self.settings.setValue('translation/profile', 'local_1_8b')
        self.settings.sync()
        restored = ProviderProfiles(QSettings(self.settings.fileName(), QSettings.IniFormat),
                                    credential_store=self.keys)
        self.assertEqual(restored.feature_route('translation')['model'], 'jingdu-hy-mt2:1.8b-q8')
        self.assertEqual(restored.feature_route('review')['model'], 'jingdu-hy-mt2:7b-q4')
        self.assertTrue(restored.route('local_1_8b')['builtin'])
        self.assertTrue(restored.ready('local_1_8b'))
        self.assertEqual(restored.key('local_1_8b'), '')
        for feature in ('review', 'ask'):
            self.assertTrue(restored.select(feature, 'local_1_8b'))
        pid = self.save()
        persisted = json.loads(self.settings.value('providers/profiles_v1'))
        self.assertEqual([p['id'] for p in persisted], [pid])
        again = ProviderProfiles(QSettings(self.settings.fileName(), QSettings.IniFormat),
                                 credential_store=self.keys)
        for feature in ('translation', 'review', 'ask'):
            self.assertEqual(again.feature_route(feature)['model'], 'jingdu-hy-mt2:1.8b-q8')

    def test_saved_profile_rows_cannot_replace_builtin_18b_route(self):
        tampered = dict(self.models.route('local_1_8b'), api_base='https://untrusted.example/v1',
                        model='wrong-model', key_saved=True, auth_required=True)
        self.settings.setValue('providers/profiles_v1', json.dumps([tampered]))
        restored = ProviderProfiles(self.settings, credential_store=self.keys)
        self.assertEqual(restored.route('local_1_8b')['api_base'], 'http://127.0.0.1:11434/v1')
        self.assertEqual(restored.key('local_1_8b'), '')


if __name__ == '__main__':
    unittest.main()
