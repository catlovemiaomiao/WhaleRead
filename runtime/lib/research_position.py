"""Disposable original-position presentation; never changes source or translations."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from research import HEADINGS, page_dir, reading_text, save


def position_key(data):
    return hashlib.sha256(json.dumps(['position-v3-pdfium-scan-regions', data], sort_keys=True,
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
        native_blocks = page.get_text('dict')['blocks']
        images = [fitz.Rect(block['bbox']) for block in native_blocks if block['type'] == 1]
        can_display_translation = data.get('approved') or data.get('translation_mode') == 'batch'
        protected = []
        for block in data['blocks']:
            if block.get('visual') or not (can_display_translation and block.get('translation')):
                x, y, w, h = block['bbox']
                protected.append(fitz.Rect(x * width, y * height, (x+w) * width, (y+h) * height) & page.rect)
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
        for native in native_blocks:
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
            translated = bool(can_display_translation and block.get('translation'))
            # A scanned page can also contain an invisible OCR text layer.
            # Removing those text objects does not remove the letters in the image.
            scan_text = any(span.get('invisible') and any(
                (fitz.Rect(span['bbox']) & image).get_area() / max(1, fitz.Rect(span['bbox']).get_area()) > .55
                for image in images) for span in spans)
            if spans and not scan_text:
                rect = fitz.Rect(spans[0]['bbox'])
                for span in spans[1:]:
                    rect |= fitz.Rect(span['bbox'])
            color = '#182720'
            if spans:
                representative = max(spans, key=lambda s: len(s.get('text', '')))
                color = '#%06x' % representative.get('color', 0)
            if translated:
                if spans and block.get('origin') == 'native' and not scan_text:
                    # Remove text only, retaining PDF vector frames, connectors,
                    # colours, logos and all images on a temporary in-memory page.
                    for span in spans:
                        page.add_redact_annot(fitz.Rect(span['bbox']), fill=False, cross_out=False)
                else:
                    # Scans have no removable text layer. Mask only the detected
                    # text crop; visual blocks remain untouched.
                    mask = entry['rect'] if scan_text else rect
                    mask = fitz.Rect(mask.x0 - 1, mask.y0 - 1, mask.x1 + 1, mask.y1 + 1) & page.rect
                    page.draw_rect(mask, color=None, fill=(1, 1, 1), overlay=True, preserve=protected)
                    if scan_text:
                        # Hidden text geometry can be much shorter than the
                        # scanned letters. Use OCR regions, then stop before a
                        # protected paragraph/figure below instead of painting
                        # translated text over that untouched source.
                        for area in protected:
                            overlap = rect & area
                            if not overlap.is_empty and overlap.width > rect.width * .5 and rect.y0 < area.y0:
                                rect.y1 = min(rect.y1, area.y0)
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
