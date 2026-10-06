"""Private PP-OCRv6 / PP-DocLayoutV3 service. Only page pixels leave the client.

Run behind SSH forwarding; never bind this HTTP service to a public interface.
Official ONNX models are pinned locally; inference does not download models.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import re
import threading
import time

MAX_BODY = 12 * 1024 * 1024
MODEL = 'pp-ocrv6-small'
VISUAL = {'image', 'chart', 'table', 'display_formula', 'inline_formula',
          'header_image', 'footer_image', 'seal', 'algorithm'}


def area(box):
    return max(0, box[2]-box[0]) * max(0, box[3]-box[1])


def overlap(a, b):
    return area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


def protect_orphan_math(blocks):
    """Keep nearby superscripts / denominators with an unmatched math line.

    This is conservative pixel preservation, not formula reconstruction. Text
    and original geometry remain in the result for later human correction.
    """
    pending = list(blocks)
    result = []
    while pending:
        group = [pending.pop(0)]
        changed = True
        while changed:
            changed = False
            for candidate in list(pending):
                b = candidate['block_bbox']
                for member in group:
                    a = member['block_bbox']
                    height = max(a[3]-a[1], b[3]-b[1])
                    dx = max(0, a[0]-b[2], b[0]-a[2])
                    dy = max(0, a[1]-b[3], b[1]-a[3])
                    if dx <= height * .7 and dy <= height * 1.5:
                        group.append(candidate)
                        pending.remove(candidate)
                        changed = True
                        break
        text = '\n'.join(b['block_content'] for b in group)
        if re.search(r'[=+*/∑∫√≤≥]', text) and len(re.findall(r'[A-Za-z]{2,}', text)) < 6:
            boxes = [b['block_bbox'] for b in group]
            result.append(dict(block_label='display_formula', block_content=text,
                block_bbox=[min(b[0] for b in boxes), min(b[1] for b in boxes),
                            max(b[2] for b in boxes), max(b[3] for b in boxes)],
                confidence=min(b['confidence'] for b in group), needs_review=True,
                unmatched=True, protection='unmatched_math_pixels'))
        else:
            result.extend(group)
    return result


def assemble(regions, lines, width, height):
    """Assign each line once. Keep unmatched lines instead of silently dropping them."""
    blocks = [dict(block_label=('document_title' if r['label'] == 'doc_title' else
                               'image' if r['label'] in VISUAL - {'table', 'chart', 'display_formula'} else r['label']),
                   block_bbox=r['box'], block_content='', confidence=r['score'],
                   order=r['order'], lines=[]) for r in regions]
    orphan = []
    for line in lines:
        if not line['text'].strip():
            continue
        box = [min(p[0] for p in line['polygon']), min(p[1] for p in line['polygon']),
               max(p[0] for p in line['polygon']), max(p[1] for p in line['polygon'])]
        box = [max(0, box[0]), max(0, box[1]), min(width, box[2]), min(height, box[3])]
        matches = [(i, overlap(box, b['block_bbox']) / max(1, area(box))) for i, b in enumerate(blocks)]
        matches = [(i, ratio) for i, ratio in matches if ratio >= .5]
        if matches:
            # Prefer the smallest containing region (e.g. formula inside prose).
            index = min(matches, key=lambda row: (area(blocks[row[0]]['block_bbox']), -row[1]))[0]
            blocks[index]['lines'].append((box, line))
            old = blocks[index]['block_bbox']
            blocks[index]['block_bbox'] = [min(old[0], box[0]), min(old[1], box[1]),
                                           max(old[2], box[2]), max(old[3], box[3])]
        else:
            orphan.append(dict(block_label='text', block_bbox=box, block_content=line['text'],
                               confidence=line['confidence'], needs_review=True, unmatched=True))
    for block in blocks:
        block['lines'].sort(key=lambda item: (item[0][1], item[0][0]))
        block['block_content'] = '\n'.join(line['text'] for _, line in block.pop('lines'))
    blocks.sort(key=lambda b: b['order'])
    # Place unmatched text next to its closest region. Reading order remains a
    # draft; the original page is always retained for human comparison.
    for block in protect_orphan_math(orphan):
        if blocks:
            closest = min(range(len(blocks)), key=lambda i:
                abs(blocks[i]['block_bbox'][1] - block['block_bbox'][1]) +
                abs(blocks[i]['block_bbox'][0] - block['block_bbox'][0]))
            blocks.insert(closest + (block['block_bbox'][1] >= blocks[closest]['block_bbox'][1]), block)
        else:
            blocks.append(block)
    for block in blocks:
        block.pop('order', None)
        # Include the whole detected line when the layout box was too tight.
        block['needs_review'] = True
    return dict(schema='whaleread-ocr-v1', model=MODEL, layout='PP-DocLayoutV3',
                width=width, height=height, parsing_res_list=blocks,
                detected_lines=len(lines), unmatched_lines=len(orphan), needs_review=True)


class Engine:
    def __init__(self, models, threads=4):
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'experiments/ocr_v6'))
        from engine import LocalOCR
        import onnxruntime as ort
        import yaml
        self.ocr = LocalOCR(models, threads)
        directory = Path(models) / 'PP-DocLayoutV3_onnx_infer'
        lock = json.loads(Path(__file__).with_name('layout.lock.json').read_text())
        for name, digest in lock['files'].items():
            if hashlib.sha256((directory / name).read_bytes()).hexdigest() != digest:
                raise ValueError('Layout model hash mismatch')
        self.labels = yaml.safe_load((directory / 'inference.yml').read_text())['label_list']
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self.layout = ort.InferenceSession(str(directory / 'inference.onnx'), options,
                                          providers=['CPUExecutionProvider'])

    def recognize(self, image):
        import cv2
        import numpy as np
        started = time.monotonic()
        w, h = image.size
        pixels = np.asarray(image.convert('RGB'))
        resized = cv2.resize(pixels, (800, 800), interpolation=cv2.INTER_CUBIC).astype('float32') / 255
        boxes = self.layout.run(None, {
            'image': resized.transpose(2, 0, 1)[None],
            'im_shape': np.array([[800, 800]], dtype='float32'),
            'scale_factor': np.array([[800/h, 800/w]], dtype='float32'),
        })[0]
        regions = []
        for row in sorted(boxes, key=lambda r: -r[1]):
            if row[1] < .45 or int(row[0]) not in range(len(self.labels)):
                continue
            box = [max(0, float(row[2])), max(0, float(row[3])),
                   min(w, float(row[4])), min(h, float(row[5]))]
            if area(box) <= 1 or any(overlap(box, r['box']) / max(1, min(area(box), area(r['box']))) > .95 for r in regions):
                continue
            regions.append(dict(box=box, label=self.labels[int(row[0])], score=float(row[1]), order=float(row[6])))
        result = assemble(regions, self.ocr.recognize(image), w, h)
        if not result['parsing_res_list']:
            # Absence of detections does not prove a blank page. Preserve pixels
            # and flag it rather than claiming that all content was translated.
            result['parsing_res_list'] = [dict(block_label='image', block_bbox=[0, 0, w, h],
                                             block_content='', needs_review=True)]
            result['unrecognized_page'] = True
        result['seconds'] = round(time.monotonic() - started, 3)
        return result


def serve(engine, token, port=18086):
    from PIL import Image
    Image.MAX_IMAGE_PIXELS = 2400 * 2400
    gate = threading.BoundedSemaphore(1)

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(20)

        def log_message(self, *_):
            pass  # No page content, credentials, or document names in logs.

        def reply(self, code, data):
            body = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self):
            if self.path == '/health':
                self.reply(200, dict(status='ok', model=MODEL, layout='PP-DocLayoutV3', device='cpu', concurrency=1))
            else:
                self.reply(404, dict(error='not_found'))

        def do_POST(self):
            if self.path != '/v1/ocr':
                return self.reply(404, dict(error='not_found'))
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + token):
                return self.reply(401, dict(error='unauthorized'))
            if not gate.acquire(blocking=False):
                return self.reply(503, dict(error='busy'))
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if length <= 0 or length > MAX_BODY or self.headers.get('Transfer-Encoding'):
                    return self.reply(413, dict(error='request_too_large'))
                payload = json.loads(self.rfile.read(length))
                if payload.get('model') != MODEL:
                    return self.reply(400, dict(error='unsupported_model'))
                raw = base64.b64decode(payload['image'], validate=True)
                with Image.open(io.BytesIO(raw)) as original:
                    if original.format != 'PNG' or min(original.size) < 16 or max(original.size) > 2400:
                        raise ValueError('Invalid image')
                    image = original.convert('RGB')
                result = engine.recognize(image)
                self.reply(200, result)
            except (ValueError, KeyError, TypeError, OSError, Image.DecompressionBombError):
                self.reply(400, dict(error='invalid_page'))
            except Exception:
                self.reply(500, dict(error='inference_failed'))
            finally:
                gate.release()

    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    server.serve_forever()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--models', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18086)
    args = parser.parse_args()
    token = os.environ.get('WHALEREAD_OCR_TOKEN', '')
    if len(token) < 32:
        raise SystemExit('Private service token is required')
    serve(Engine(args.models), token, args.port)
