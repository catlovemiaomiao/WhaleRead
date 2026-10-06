"""Bounded Chat Completions requests shared by every AI feature."""
from __future__ import annotations

import ipaddress
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_worker_credentials = {}


class ProviderError(RuntimeError):
    def __init__(self, code, *, retryable=False, status=None):
        self.code, self.retryable, self.status = code, retryable, status
        super().__init__(f'API request failed: {code}' + (f' (HTTP {status})' if status else ''))


def is_loopback(host):
    if (host or '').lower() == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host or '').is_loopback
    except ValueError:
        return False


def normalize_base(value):
    value = str(value)
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('Invalid API address')
    value = value.strip().rstrip('/')
    if any(ord(c) < 33 for c in value) or '\\' in value:
        raise ValueError('Invalid API address')
    parts = urllib.parse.urlsplit(value)
    if (parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username is not None
            or parts.password is not None or parts.query or parts.fragment):
        raise ValueError('API address must not contain credentials, query or fragment')
    try:
        parts.port
    except ValueError as exc:
        raise ValueError('Invalid API port') from exc
    if parts.scheme == 'http' and not is_loopback(parts.hostname):
        raise ValueError('Remote API requires HTTPS')
    path = parts.path.rstrip('/')
    if path.endswith('/chat/completions'):
        path = path[:-len('/chat/completions')]
    if not path:
        path = '/v1'
    if any(p in {'.', '..'} for p in urllib.parse.unquote(path).split('/')):
        raise ValueError('Invalid API path')
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc.lower(), path, '', ''))


def completion_url(value):
    return normalize_base(value) + '/chat/completions'


def install_worker_credentials(credentials):
    global _worker_credentials
    if not isinstance(credentials, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                                for k, v in credentials.items()):
        raise ValueError('Invalid worker credentials')
    _worker_credentials = dict(credentials)


def load_credential(path, provider='none'):
    if provider in _worker_credentials:
        return _worker_credentials[provider]
    if provider in {'none', 'local', 'ollama'}:
        return ''
    # Application profile credentials must arrive through the anonymous pipe.
    if provider.startswith('profile:'):
        raise ProviderError('missing_credential_context')
    # Backwards-compatible, explicit CLI only. The GUI never uses this route.
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        entry = data.get(provider) or {}
        key = entry.get('key') if entry.get('type') == 'api' else ''
        if not key:
            raise ValueError()
        return str(key)
    except (OSError, ValueError, AttributeError):
        raise ProviderError('missing_credential') from None


def use_deepseek_nonthinking(endpoint, model):
    host = (urllib.parse.urlsplit(endpoint).hostname or '').lower()
    return host == 'api.deepseek.com' and str(model).lower().startswith(
        ('deepseek-v4', 'deepseek-flash'))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ProviderError('redirect_blocked', status=code)


def chat_request(endpoint, model, key, messages, *, timeout=90, temperature=.2,
                 top_p=None, disable_thinking=None):
    url = completion_url(endpoint)
    if not str(model).strip():
        raise ProviderError('missing_model')
    if not isinstance(key, str) or '\n' in key or '\r' in key:
        raise ProviderError('invalid_credential')
    payload = dict(model=model, messages=messages, temperature=temperature,
                   max_tokens=4096, stream=False)
    if top_p is not None:
        payload['top_p'] = top_p
    if disable_thinking is True or (disable_thinking is None and use_deepseek_nonthinking(url, model)):
        payload['thinking'] = {'type': 'disabled'}
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                                 headers=headers, method='POST')
    handlers = [NoRedirect()]
    if url.startswith('https:'):
        # The application must verify TLS even on a Mac without Homebrew or
        # the developer's OpenSSL installation. This CA file ships in the app.
        import certifi
        handlers.append(urllib.request.HTTPSHandler(
            context=ssl.create_default_context(cafile=certifi.where())))
    start = time.monotonic()
    try:
        with urllib.request.build_opener(*handlers).open(req, timeout=timeout) as response:
            declared = response.headers.get('Content-Length')
            if declared and int(declared) > MAX_RESPONSE_BYTES:
                raise ProviderError('response_too_large')
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ProviderError('response_too_large')
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise ProviderError('http_error', retryable=status == 429 or 500 <= status < 600,
                            status=status) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderError('connection_failed', retryable=True) from None
    except (ValueError, OverflowError):
        raise ProviderError('invalid_response') from None
    try:
        result = json.loads(raw)
        choice = result['choices'][0]
        content = choice['message']['content']
        if not isinstance(content, str) or not content.strip():
            raise ValueError()
        if choice.get('finish_reason') not in {None, 'stop', 'length', 'eos'}:
            raise ProviderError('response_refused')
    except (ValueError, KeyError, IndexError, TypeError):
        raise ProviderError('invalid_response') from None
    return content.strip(), choice.get('finish_reason'), time.monotonic() - start
