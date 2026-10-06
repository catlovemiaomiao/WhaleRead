"""Isolated, page-addressed research documents. PDF pixels remain authoritative."""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
from difflib import SequenceMatcher
from pathlib import Path

SCHEMA = 'research-hybrid-v1'
LAYOUT_REVISION = 'page-layout-v4'
TOKEN = re.compile(r'\[\[KEEP\d+\]\]')
VISUAL = {'display_formula', 'formula', 'image', 'chart', 'table', 'header_image', 'footer_image'}
HEADINGS = {'title', 'document_title', 'paragraph_title', 'section_title'}


def save(path, value):
    from tempfile import NamedTemporaryFile
    import os
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(f.name, path)


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def import_pdf(source, library):
    import pdf_backend as fitz
    source = Path(source).resolve()
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    job = Path(library) / digest[:20]
    manifest = job / 'document.json'
    if manifest.exists():
        data = load(manifest)
        if data.get('schema') != SCHEMA or data.get('sha256') != digest:
            raise ValueError('科研文档版本不兼容，请保留目录并使用对应版本打开。')
        return job
    with fitz.open(source) as doc:
        if doc.needs_pass:
            raise ValueError('请先解除 PDF 密码保护。')
        count = len(doc)
    job.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, job / 'original.pdf')
    save(manifest, dict(schema=SCHEMA, sha256=digest, title=source.stem,
                        page_count=count, page=1, terms='', original=str(source)))
    return job


def page_dir(job, number):
    return Path(job) / 'pages' / f'{number:04d}'


def render_page(job, number):
    import pdf_backend as fitz
    from PIL import Image
    from tempfile import NamedTemporaryFile
    directory = page_dir(job, number)
    directory.mkdir(parents=True, exist_ok=True)
    image = directory / 'original.png'
    if image.exists():
        try:
            with Image.open(image) as cached:
                cached.verify()
            return image
        except (OSError, ValueError, SyntaxError):
            pass  # Recover a cache interrupted by an older app version.
    with NamedTemporaryFile(dir=directory, suffix='.png', delete=False) as f:
        temporary = Path(f.name)
    try:
        with fitz.open(Path(job) / 'original.pdf') as doc:
            page = doc[number - 1]
            scale = min(2, 2400 / max(page.rect.width, page.rect.height))
            page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False).save(temporary)
        # Both the UI preview and worker can request this page. Never expose a
        # half-written PNG to the other process or to a resumed OCR request.
        os.replace(temporary, image)
    finally:
        temporary.unlink(missing_ok=True)
    return image


def crop(page, rect, path):
    import pdf_backend as fitz
    rect = fitz.Rect(rect) & page.rect
    if rect.is_empty:
        raise ValueError('版面区域超出 PDF 页面，无法安全裁图。')
    scale = min(2, 2400 / max(rect.width, rect.height))
    page.get_pixmap(matrix=fitz.Matrix(scale, scale), clip=rect, alpha=False).save(path)
    return Path(path).resolve().as_uri()


def protect(text, terms=(), protect_uppercase=True):
    """Protect exact abbreviations, explicit terms and common scientific symbols."""
    # Protect a complete TeX formula before matching variables inside it. The
    # surrounding prose remains translatable, while formula spelling/order is exact.
    parts = [r'\$\$[^$]*\$\$', r'\$[^$]+\$', r'\\\([^)]*\\\)', r'\\\[[^]]*\\\]']
    parts += sorted([re.escape(t.strip()) for t in terms if t.strip()], key=len, reverse=True)
    parts += [r'\b[A-Za-z]\s*[=<>]\s*(?:\d+(?:\.\d+)?|\.\d+)\b',
              r'(?<![\w.])(?:\d+(?:[.,]\d+)*|\.\d+)(?!\w)']
    if protect_uppercase:
        parts.append(r'\b[A-Z][A-Z0-9-]{1,15}\b')
    parts += [r'[α-ωΑ-Ω]+',
              r"(?<!['’.\w])(?:[b-hj-zB-HJ-Z])(?:[₀-₉⁰¹²³⁴⁵⁶⁷⁸⁹]+)?(?!['’\w])"]
    pattern = re.compile('|'.join(parts))
    mapping = {}
    def substitute(match):
        word = match.group()
        if word in {'THE', 'AND', 'OF', 'IN', 'TO', 'FOR', 'WITH', 'ON', 'BY', 'AS', 'IS', 'A', 'AN'}:
            return word
        token = f'[[KEEP{len(mapping):04d}]]'
        mapping[token] = word
        return token
    return pattern.sub(substitute, text), mapping


def restore(text, mapping):
    if sorted(TOKEN.findall(text)) != sorted(mapping):
        raise ValueError('变量或保留词缺失/重复，译稿未采纳，可重试。')
    for key, value in mapping.items():
        text = text.replace(key, value)
    return text


def reading_text(text):
    """Make protected TeX readable on the paper view; persisted text stays exact."""
    sub = str.maketrans('0123456789+-=()', '₀₁₂₃₄₅₆₇₈₉₊₋₌₍₎')
    sup = str.maketrans('0123456789+-=()', '⁰¹²³⁴⁵⁶⁷⁸⁹⁺⁻⁼⁽⁾')
    commands = {'to': '→', 'infty': '∞', 'approx': '≈', 'le': '≤', 'leq': '≤',
                'ge': '≥', 'geq': '≥', 'neq': '≠', 'times': '×', 'cdot': '·'}
    def formula(match):
        value = match.group(0)
        if value.startswith('$$'):
            value = value[2:-2]
        elif value.startswith('$'):
            value = value[1:-1]
        elif value.startswith(r'\(') or value.startswith(r'\['):
            value = value[2:-2]
        value = re.sub(r'\\frac\{([^{}]+)\}\{([^{}]+)\}', r'\1⁄\2', value)
        value = re.sub(r'_\{([0-9+\-=()]+)\}', lambda m: m.group(1).translate(sub), value)
        value = re.sub(r'\^\{([0-9+\-=()]+)\}', lambda m: m.group(1).translate(sup), value)
        for name, glyph in commands.items():
            value = re.sub(r'\\' + name + r'\b', glyph, value)
        value = value.replace(r'\log', 'log').replace(r'\ln', 'ln')
        value = value.replace('{', '').replace('}', '')
        return re.sub(r'\s+', ' ', value).strip()
    return re.sub(r'\$\$[^$]*\$\$|\$[^$]+\$|\\\([^)]*\\\)|\\\[[^]]*\\\]', formula, text)


def join_prose(text, reference=''):
    def join(match):
        left, right = match.groups()
        combined = left + right
        return combined if re.search(r'\b' + re.escape(combined) + r'\b', reference, re.I) else left + '-' + right
    text = re.sub(r'([A-Za-z]+)-\s*\n\s*([A-Za-z]+)', join, text)
    return re.sub(r'\s+', ' ', text).strip()


def compare_ocr(primary, witness):
    """Describe agreement between two OCR candidates without selecting a winner."""
    def normalized(value):
        return re.sub(r'[^a-z0-9]+', '', value.casefold())
    left, right = normalized(primary), normalized(witness)
    if not right:
        return dict(status='no_result', similarity=0.0, length_ratio=0.0,
                    possible_extension=False, reason='第二识别没有返回可比较文字')
    if not left:
        return dict(status='review', similarity=0.0, length_ratio=0.0,
                    possible_extension=False, reason='主识别没有返回可比较文字')
    matcher = SequenceMatcher(None, left, right, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    similarity = matcher.ratio()
    length_ratio = min(len(left), len(right)) / max(len(left), len(right))
    witness_coverage = matched / len(right)
    possible_extension = (len(left) - len(right) >= max(18, int(len(right) * .10))
                          and witness_coverage >= .78)
    numeric_conflict = (not re.search(r'\$|\\(?:frac|sum|int|sqrt|log)\b', primary)
                        and re.findall(r'\d+(?:[.,]\d+)*', primary)
                        != re.findall(r'\d+(?:[.,]\d+)*', witness))
    if possible_extension:
        status, reason = 'review', '主识别疑似比页面可见文字多出内容'
    elif numeric_conflict:
        status, reason = 'check', '数字或编号识别不一致'
    elif similarity >= .90 and length_ratio >= .84:
        status, reason = 'agree', '两套识别基本一致'
    elif similarity < .80 or length_ratio < .72:
        status, reason = 'review', '两套识别差异较大'
    else:
        status, reason = 'check', '两套识别存在局部差异'
    return dict(status=status, similarity=round(similarity, 4),
                length_ratio=round(length_ratio, 4),
                possible_extension=possible_extension, numeric_conflict=numeric_conflict,
                reason=reason)


def tesseract_witness(image, primary):
    """Run a local, crop-level English OCR witness; never mutate primary text."""
    if len(re.sub(r'[^A-Za-z0-9]', '', primary)) < 12:
        return dict(engine='tesseract', status='skipped', text='', reason='文字过短，不足以可靠比较')
    candidates = [os.environ.get('WHALEREAD_TESSERACT'), shutil.which('tesseract'),
                  '/opt/homebrew/bin/tesseract', '/usr/local/bin/tesseract', '/usr/bin/tesseract']
    binary = next((item for item in candidates if item and Path(item).is_file()), None)
    if not binary:
        return dict(engine='tesseract', status='unavailable', text='', reason='本机未找到 Tesseract')
    try:
        completed = subprocess.run(
            [binary, str(image), 'stdout', '-l', 'eng', '--psm', '6'],
            capture_output=True, text=True, timeout=20,
            env=dict(os.environ, OMP_THREAD_LIMIT='1'), check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return dict(engine='tesseract', status='failed', text='', reason=f'第二识别失败：{type(exc).__name__}')
    if completed.returncode:
        return dict(engine='tesseract', status='failed', text='', reason='第二识别进程返回错误')
    text = join_prose(completed.stdout)
    return dict(engine='tesseract', text=text, **compare_ocr(primary, text))


def preserve_as_image(label, text, spans=()):
    """Keep true equations/figures as pixels without swallowing mixed prose.

    Italic and math fonts are useful evidence, but neither is sufficient alone:
    prose emphasis is italic too, and equations contain upright operators/digits.
    """
    if label in VISUAL or label in {'header', 'footer', 'number'} or not text:
        return True
    if label == 'figure_title' and re.fullmatch(r'\(?[A-Za-z0-9]\)?', text):
        return True
    compact = re.sub(r'\s+', '', text)
    prose = re.sub(r'\$[^$]+\$|\\\([^)]*\\\)|\\\[[^]]*\\\]', ' ', text)
    prose_words = re.findall(r"[A-Za-z]{2,}(?:['’][A-Za-z]+)?", prose)
    math_font_chars = sum(len(s.get('text', '').strip()) for s in spans
                          if re.search(r'(?:CM(?:MI|SY|EX)|Math|Symbol|STIX|MTExtra)', s.get('font', ''), re.I))
    italic_chars = sum(len(s.get('text', '').strip()) for s in spans if int(s.get('flags', 0)) & 2)
    styled_ratio = max(math_font_chars, italic_chars) / max(1, sum(len(s.get('text', '').strip()) for s in spans))
    math_markup = bool(re.search(r'\$|\\(?:frac|sum|int|begin|sqrt|log)\b|[∑∫∂√∏≤≥≠∞]', text))
    operator_ratio = len(re.findall(r'[=+*/<>∑∫∂√∏≤≥≠∞]', compact)) / max(1, len(compact))
    # A sentence with several real words is prose even when it embeds formula
    # images or mathematical italics; its TeX fragments are protected separately.
    if len(prose_words) >= 6:
        return False
    return math_markup or operator_ratio >= .08 or (styled_ratio >= .55 and len(prose_words) <= 3)


def build_page(job, number, result, previous=None, *, witness=True):
    """Use Paddle geometry, native prose when available, and pixels for pure math."""
    import pdf_backend as fitz
    directory = page_dir(job, number)
    directory.mkdir(parents=True, exist_ok=True)
    if not result.get('parsing_res_list'):
        raise ValueError('本页未检出有效版面，请检查原 PDF 后重试。')
    with fitz.open(Path(job) / 'original.pdf') as doc:
        page = doc[number - 1]
        native = len(re.sub(r'\s', '', page.get_text())) >= 60
        images = [fitz.Rect(b['bbox']) for b in page.get_text('dict')['blocks'] if b['type'] == 1]
        sx, sy = page.rect.width / result['width'], page.rect.height / result['height']
        blocks = []
        for i, item in enumerate(result['parsing_res_list']):
            label = item['block_label']
            rect = fitz.Rect(*[v * (sx if n % 2 == 0 else sy) for n, v in enumerate(item['block_bbox'])])
            picture = crop(page, rect, directory / f'block-{i:03d}.png')
            region_native = page.get_text('text', clip=rect).strip()
            embedded = native and region_native and any((r & rect).get_area() > r.get_area() * .6 for r in images)
            ocr_text = item.get('block_content', '').strip()
            origin = 'mixed' if embedded else 'native' if native and region_native else 'ocr'
            text = join_prose(region_native, ocr_text) if origin == 'native' else join_prose(ocr_text)
            spans = [s for b in page.get_text('dict', clip=rect)['blocks']
                     for line in b.get('lines', []) for s in line.get('spans', [])]
            visual = preserve_as_image(label, text, spans)
            block = dict(id=i, label=label, source=text, origin=origin,
                         bbox=[rect.x0 / page.rect.width, rect.y0 / page.rect.height,
                               rect.width / page.rect.width, rect.height / page.rect.height],
                         font_size=sorted([s['size'] for s in spans])[len(spans)//2] if spans else 11,
                         image=picture, visual=visual, translation='',
                         status='preserved' if visual else 'ready' if origin == 'native' else 'needs_review')
            if witness and not visual and origin != 'native':
                block['witness'] = tesseract_witness(directory / f'block-{i:03d}.png', text)
            blocks.append(block)
        width, height = page.rect.width, page.rect.height
    data = dict(schema=SCHEMA, revision=LAYOUT_REVISION, width=width, height=height,
                number=number, origin='mixed' if any(b['origin'] == 'mixed' for b in blocks) else 'native' if native else 'ocr',
                blocks=blocks, approved=all(b['visual'] or b['origin'] == 'native' for b in blocks),
                witness_checked=sum(b.get('witness', {}).get('status') in {'agree', 'check', 'review'} for b in blocks),
                witness_conflicts=sum(b.get('witness', {}).get('status') in {'check', 'review'} for b in blocks))
    if previous:
        repaired = False
        reclassified = False
        translation_rules_changed = False
        for b, old in zip(blocks, previous['blocks']):
            if b['id'] != old['id']:
                continue
            same = join_prose(old['source'], b['source']) == b['source']
            if same or old.get('reviewed'):
                changed_kind = bool(old.get('visual')) != bool(b['visual'])
                if old.get('reviewed'):
                    b['visual'] = False
                    changed_kind = bool(old.get('visual'))
                for key in ('translation', 'status', 'history', 'revisions', 'attempts', 'reviewed'):
                    if changed_kind and key in ('translation', 'status'):
                        continue
                    if key in old:
                        b[key] = old[key]
                if old.get('reviewed'):
                    b['source'] = old['source']
                if changed_kind:
                    if old.get('translation'):
                        b.setdefault('revisions', []).append(dict(source=old['source'], translation=old['translation']))
                    reclassified = True
                stale_heading = (b['label'] in HEADINGS and old.get('translation')
                                 and len(re.findall(r'\b[A-Z]{2,}\b', old['source'])) >= 1)
                if stale_heading:
                    b.setdefault('revisions', []).append(dict(source=old['source'], translation=old['translation']))
                    b.update(translation='', status='ready')
                    translation_rules_changed = True
            else:
                b['revisions'] = old.get('revisions', []) + [dict(source=old['source'], translation=old['translation'])]
                repaired = True
        data['approved'] = bool(previous.get('approved')) and not repaired and not reclassified
        data['source_repaired'] = repaired
        data['layout_reclassified'] = reclassified
        data['translation_rules_changed'] = translation_rules_changed
    save(directory / 'page.json', data)
    return data


def upgrade_page(job, number):
    directory = page_dir(job, number)
    path = directory / 'page.json'
    data = load(path)
    if data.get('revision') != LAYOUT_REVISION:
        results = list((directory / 'layout').glob('*_res.json'))
        if len(results) != 1:
            raise ValueError('缺少原始版面结果，无法升级本页；已保留旧数据。')
        backup = directory / f'page.before-{LAYOUT_REVISION}.json'
        if not backup.exists():
            save(backup, data)
        data = build_page(job, number, load(results[0]), previous=data)
    return data


def edit_source(job, number, index, text):
    path = page_dir(job, number) / 'page.json'
    data = load(path)
    block = data['blocks'][index]
    if block['visual'] or not text.strip():
        raise ValueError('正文不能为空；公式和图表通过原图保留。')
    if block['source'] != text.strip():
        block.setdefault('revisions', []).append(dict(source=block['source'], translation=block['translation']))
        block.update(source=text.strip(), translation='', status='ready', reviewed=True)
        if block.get('witness'):
            block['witness']['resolved_by_user'] = True
        data['approved'] = False
    save(path, data)


def export_html(job):
    job = Path(job)
    manifest = load(job / 'document.json')
    rows = ['<!doctype html><meta charset="utf-8"><title>鲸读科研样本</title>',
            '<style>body{background:#f5f3ed;color:#253e34;font:18px/1.8 Georgia,serif;margin:32px}section{display:grid;grid-template-columns:1fr 1fr;gap:28px;margin:32px 0}img{max-width:100%}p{white-space:pre-wrap}small{color:#68786f}</style>',
            '<h1>' + html.escape(manifest['title']) + '</h1><p>科研实验版 · 公式及图表保留原图 · 未翻译内容明确标记</p>']
    for path in sorted((job / 'pages').glob('*/page.json')):
        data = load(path)
        review = '已核对' if data.get('approved') else '尚未人工核对 · OCR 与译文可能有误'
        rows.append(f'<h2>第 {data["number"]} 页</h2><p>{review}</p><section><div><img src="{(path.parent / "original.png").as_uri()}"></div><div>')
        for b in data['blocks']:
            if b['visual']:
                rows.append(f'<img src="{html.escape(b["image"], quote=True)}">')
            else:
                rows.append('<small>' + ('译文' if b['translation'] else '待翻译原文') + '</small><p>' + html.escape(b['translation'] or b['source']) + '</p>')
        rows.append('</div></section>')
    output = job / '科研双语预览.html'
    output.write_text('\n'.join(rows), encoding='utf-8')
    return output
