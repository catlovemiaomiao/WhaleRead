"""Checkpoint, consent and page-boundary regressions for remote research OCR."""
import importlib.util
import json
from pathlib import Path
import sys
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime/lib'))
sys.path.insert(0, str(ROOT / 'runtime/tools'))
from research import import_pdf, save, load, page_dir, LAYOUT_REVISION, export_html
import research_worker as worker
from ocr_client import validate_result
from provider_transport import ProviderError

ROUTE = dict(id='local_test', api_base='http://127.0.0.1:1/v1', model='test-model', auth_provider='none')
OCR = dict(id='ocr_test', api_base='http://127.0.0.1:2/v1', model='pp-ocrv6-small', auth_provider='none')


def document(tmp_path):
    from PIL import Image
    source = tmp_path / 'fictional.pdf'
    Image.new('RGB', (100, 140), 'white').save(source, 'PDF', save_all=True,
                                            append_images=[Image.new('RGB', (100, 140), 'white')])
    job = import_pdf(source, tmp_path / 'library')
    for n in (1, 2):
        save(page_dir(job, n) / 'page.json', dict(revision=LAYOUT_REVISION, approved=False,
            number=n, blocks=[dict(id=0, source=f'Example paragraph on page {n}.',
                label='text', translation='', visual=False)]))
    return job


def success(*args):
    prompt = args[3]
    protected_source = prompt.split('\n\n')[-1]
    return '示例译文：' + protected_source, .1


def test_pause_resume_saves_completed_block_and_never_approves(tmp_path):
    job = document(tmp_path)
    calls = []
    def interrupt_after_page(*args):
        calls.append(args)
        if len(calls) == 2:
            raise RuntimeError('offline')
        return success(*args)
    with patch.object(worker, 'translate_once', side_effect=interrupt_after_page):
        with pytest.raises(RuntimeError):
            worker.batch(job, ROUTE, OCR)
    first = (page_dir(job, 1) / 'page.json').read_bytes()
    assert load(job / 'document.json')['batch']['state'] == 'interrupted'
    assert not load(page_dir(job, 1) / 'page.json')['approved']
    with patch.object(worker, 'translate_once', side_effect=success) as request:
        assert worker.batch(job, ROUTE, OCR) == 0
    assert request.call_count == 1
    assert (page_dir(job, 1) / 'page.json').read_bytes() == first
    assert load(job / 'document.json')['batch']['state'] == 'done'
    assert all(not load(page_dir(job, n) / 'page.json')['approved'] for n in (1, 2))
    assert '尚未人工核对' in export_html(job).read_text()


def test_single_page_still_requires_approval(tmp_path):
    job = document(tmp_path)
    with patch.object(worker, 'translate_once') as request, pytest.raises(ValueError, match='先核对'):
        worker.translate(job, 1, ROUTE)
    request.assert_not_called()


def test_batch_translation_is_visible_without_manual_approval(tmp_path):
    from research_position import render_position
    job = document(tmp_path)
    data = load(page_dir(job, 1) / 'page.json')
    data.update(translation_mode='batch')
    data['blocks'][0].update(bbox=[.1,.1,.8,.2], translation='批量译文')
    result = load(render_position(job, 1, data))
    assert result['blocks'][0]['translated']
    assert not data['approved']


def test_interrupted_render_never_exposes_partial_page_and_repairs_old_cache(tmp_path):
    from research import render_page
    from PIL import Image
    job = document(tmp_path)
    def interrupted(_pixmap, path):
        Path(path).write_bytes(b'incomplete png')
        raise OSError('interrupted render')
    with patch('pdf_backend.Pixmap.save', interrupted), pytest.raises(OSError):
        render_page(job, 1)
    cached = page_dir(job, 1) / 'original.png'
    assert not cached.exists()
    cached.write_bytes(b'old interrupted cache')
    assert render_page(job, 1) == cached
    with Image.open(cached) as image:
        image.verify()


def test_route_change_cannot_mix_models_on_resume(tmp_path):
    job = document(tmp_path)
    worker.bind_route(job, 'translation', ROUTE)
    with patch.object(worker, 'translate_once') as request, pytest.raises(ValueError, match='模型断点'):
        worker.batch(job, dict(ROUTE, model='different'), OCR)
    request.assert_not_called()


def test_unrecognized_page_is_not_a_complete_translation(tmp_path):
    job = document(tmp_path)
    data = load(page_dir(job, 1) / 'page.json')
    data.update(ocr_unrecognized=True, blocks=[dict(visual=True, translation='', source='', image='')])
    save(page_dir(job, 1) / 'page.json', data)
    with patch.object(worker, 'translate_once', side_effect=success):
        assert worker.batch(job, ROUTE, OCR) == 1
    assert load(job / 'document.json')['batch']['pages']['1'] == 'needs_review'


@pytest.mark.parametrize('box', [[0, 0, 101, 100], [0, 0, float('nan'), 100], [20, 0, 10, 30], [True, 0, 20, 30]])
def test_invalid_ocr_geometry_rejected(box):
    result = dict(schema='whaleread-ocr-v1', width=100, height=140,
                  parsing_res_list=[dict(block_label='text', block_bbox=box, block_content='a')])
    with pytest.raises(ProviderError):
        validate_result(result, (100, 140))


def test_layout_assigns_every_line_once_and_preserves_visuals():
    spec = importlib.util.spec_from_file_location('ocr_server_test', ROOT / 'deployment/ocr_service/server.py')
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    regions = [dict(label='text', box=[0, 0, 80, 30], score=.9, order=1),
               dict(label='table', box=[0, 50, 80, 80], score=.9, order=2)]
    lines = [dict(text=text, confidence=.9, polygon=[[2,y],[20,y],[20,y+5],[2,y+5]])
             for text,y in [('first',2),('table cell',55),('orphan',110)]]
    result = server.assemble(regions, lines, 100, 140)
    text = '\n'.join(b['block_content'] for b in result['parsing_res_list'])
    assert all(text.count(word) == 1 for word in ('first', 'table cell', 'orphan'))
    assert any(b['block_label'] == 'table' for b in result['parsing_res_list'])
    assert result['unmatched_lines'] == 1
    math = server.protect_orphan_math([
        dict(block_content='E = mc', block_bbox=[10,30,100,60], confidence=.9),
        dict(block_content='2', block_bbox=[100,15,113,33], confidence=.8),
        dict(block_content='Separate prose', block_bbox=[10,200,200,230], confidence=.9)])
    assert len(math) == 2 and math[0]['block_label'] == 'display_formula'
    assert math[0]['block_bbox'] == [10,15,113,60]
    assert '2' in math[0]['block_content'] and math[1]['block_content'] == 'Separate prose'


@pytest.mark.parametrize('kind,expected', [('redirect','redirect_blocked'), ('oversize','response_too_large')])
def test_ocr_transport_rejects_redirect_and_oversize(tmp_path, kind, expected):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    import threading
    from PIL import Image
    from ocr_client import recognize
    calls = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            calls.append(self.path)
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(302 if kind == 'redirect' else 200)
            self.send_header('Location', '/must-not-follow')
            self.send_header('Content-Length', str(3*1024*1024) if kind == 'oversize' else '0')
            self.end_headers()
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    path = tmp_path / 'page.png'
    Image.new('RGB',(100,140),'white').save(path)
    try:
        with pytest.raises(ProviderError) as failure:
            recognize(path, dict(OCR, api_base=f'http://127.0.0.1:{server.server_port}/v1'))
        assert failure.value.code == expected
        assert calls == ['/v1/ocr']
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
