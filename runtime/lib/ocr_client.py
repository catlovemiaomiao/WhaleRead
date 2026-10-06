"""Bounded page-image requests to a configured private OCR service."""
from __future__ import annotations

import base64
import io
import json
import math
from pathlib import Path
import ssl
import urllib.error
import urllib.parse
import urllib.request
from provider_transport import NoRedirect, ProviderError, normalize_base, is_loopback, load_credential

MAX_RESPONSE = 2 * 1024 * 1024
MAX_REQUEST = 12 * 1024 * 1024
MAX_SIDE = 2400


def validate_result(result, size):
    try:
        if result['schema'] != 'whaleread-ocr-v1' or (result['width'], result['height']) != size:
            raise ValueError()
        blocks = result['parsing_res_list']
        if not isinstance(blocks, list) or not 1 <= len(blocks) <= 2000:
            raise ValueError()
        for block in blocks:
            if not isinstance(block.get('block_label'), str) or len(block['block_label']) > 80:
                raise ValueError()
            text = block.get('block_content', '')
            if not isinstance(text, str) or len(text) > 100000:
                raise ValueError()
            box = block['block_bbox']
            if len(box) != 4 or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in box):
                raise ValueError()
            if not (0 <= box[0] < box[2] <= size[0] and 0 <= box[1] < box[3] <= size[1]):
                raise ValueError()
    except (KeyError, TypeError, ValueError, AttributeError):
        raise ProviderError('invalid_ocr_response') from None
    return result


def recognize(image_path, route, *, timeout=180):
    from PIL import Image
    if not route or not route.get('model'):
        raise ValueError('请在模型与 API 设置中选择 PDF OCR 服务。')
    url = normalize_base(route['api_base']) + '/ocr'
    with Image.open(image_path) as original:
        image = original.convert('RGB')
    image.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    image.save(buf, 'PNG')
    body = json.dumps(dict(model=route['model'], image=base64.b64encode(buf.getvalue()).decode())).encode()
    if len(body) > MAX_REQUEST:
        raise ProviderError('ocr_page_too_large')
    key = load_credential(Path(), route['auth_provider'])
    if '\n' in key or '\r' in key:
        raise ProviderError('invalid_credential')
    headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
    if key:
        headers['Authorization'] = 'Bearer ' + key
    handlers = [NoRedirect()]
    if is_loopback(urllib.parse.urlsplit(url).hostname):
        handlers.append(urllib.request.ProxyHandler({}))
    if url.startswith('https:'):
        import certifi
        handlers.append(urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where())))
    request = urllib.request.Request(url, data=body, headers=headers, method='POST')
    try:
        with urllib.request.build_opener(*handlers).open(request, timeout=timeout) as response:
            declared = response.headers.get('Content-Length')
            if declared and int(declared) > MAX_RESPONSE:
                raise ProviderError('response_too_large')
            raw = response.read(MAX_RESPONSE + 1)
            if len(raw) > MAX_RESPONSE:
                raise ProviderError('response_too_large')
        result = json.loads(raw)
    except urllib.error.HTTPError as exc:
        status = exc.code
        exc.close()
        raise ProviderError('ocr_http_error', retryable=status == 429 or status >= 500, status=status) from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise ProviderError('ocr_connection_failed', retryable=True) from None
    except (ValueError, OverflowError):
        raise ProviderError('invalid_ocr_response') from None
    if not isinstance(result, dict) or result.get('model') != route['model']:
        raise ProviderError('ocr_model_mismatch')
    return validate_result(result, image.size)
