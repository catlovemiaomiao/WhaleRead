"""Disposable original-position presentation; never changes source or translations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from research import HEADINGS, page_dir, reading_text, save


def position_key(data):
    return hashlib.sha256(json.dumps(['position-v2-pdfium', data], sort_keys=True,
                                    ensure_ascii=False).encode()).hexdigest()[:20]


def position_path(job, number, data):
    return page_dir(job, number) / ('position-' + position_key(data) + '.json')


def render_position(job, number, data):
    import pdf_backend as fitz
    target = position_path(job, number, data)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        return target
    with fitz.open(Path(job) / 'original.pdf') as doc:
        page = doc[number - 1]
        width, height = page.rect.width, page.rect.height
        frames = [d['rect'] for d in page.get_drawings()
                  if d.get('color') is not None and d['rect'].width > 60 and d['rect'].height > 25
                  and any(item[0] == 're' for item in d['items'])]
        entries = []
        for index, block in enumerate(data['blocks']):
            if block.get('visual'):
                continue
            x, y, w, h = block['bbox']
            rect = fitz.Rect(x * width, y * height, (x+w) * width, (y+h) * height) & page.rect
            if rect.is_empty:
                continue
            entries.append(dict(index=index, block=block, rect=rect, spans=[]))
        # Assign each native span once: OCR regions can overlap at heading/body
        # boundaries. The tighter region owns a fully contained heading span.
        for native in page.get_text('dict')['blocks']:
            for line in native.get('lines', []):
                for span in line['spans']:
                    if not span.get('text', '').strip():
                        continue
                    box = fitz.Rect(span['bbox'])
                    candidates = [e for e in entries if (box & e['rect']).get_area() / max(1, box.get_area()) > .55]
                    if candidates:
                        owner = min(candidates, key=lambda e: e['rect'].get_area())
                        owner['spans'].append(span)
        rendered = []
        for entry in entries:
            block, rect, spans = entry['block'], entry['rect'], entry['spans']
            translated = bool((data.get('approved') or data.get('translation_mode') == 'batch')
                              and block.get('translation'))
            if spans:
                rect = fitz.Rect(spans[0]['bbox'])
                for span in spans[1:]:
                    rect |= fitz.Rect(span['bbox'])
            color = '#182720'
            if spans:
                representative = max(spans, key=lambda s: len(s.get('text', '')))
                color = '#%06x' % representative.get('color', 0)
            if translated:
                if spans and block.get('origin') == 'native':
                    # Remove text only, retaining PDF vector frames, connectors,
                    # colours, logos and all images on a temporary in-memory page.
                    for span in spans:
                        page.add_redact_annot(fitz.Rect(span['bbox']), fill=False, cross_out=False)
                else:
                    # Scans have no removable text layer. Mask only the detected
                    # text crop; visual blocks remain untouched.
                    page.draw_rect(rect, color=None, fill=(1, 1, 1), overlay=True)
            containers = [r for r in frames if r.contains(rect)]
            if containers:
                container = min(containers, key=lambda r: r.get_area())
                rect.x1 = max(rect.x1, container.x1 - 5)
            rendered.append(dict(index=entry['index'],
                bbox=[rect.x0/width, rect.y0/height, rect.width/width, rect.height/height],
                source=block['source'], translation=reading_text(block.get('translation', '')),
                translated=translated, heading=block['label'] in HEADINGS,
                font_size=block.get('font_size', 11), color=color))
        page.apply_redactions(images=0, graphics=0, text=0)
        image = target.with_suffix('.png')
        scale = min(2, 2400 / max(width, height))
        page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).save(image)
        save(target, dict(image=image.resolve().as_uri(), blocks=rendered,
                          width=width, height=height))
    return target
