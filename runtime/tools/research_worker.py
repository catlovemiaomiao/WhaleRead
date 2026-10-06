from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from research import load, save, page_dir, render_page, build_page, upgrade_page
from ocr_client import recognize
from research_position import render_position
from research_terms import prepare, validate
from translate_range import load_key, translate_once, start_parent_watchdog


def emit(text, code='', args=None, detail=''):
    """Emit an additive structured status while preserving the legacy line."""
    payload = {'status': text}
    if code:
        payload.update(message_code=code, message_args=list(args or []))
    if detail:
        payload['detail'] = str(detail)[:500]
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def bind_route(job, feature, route):
    manifest = load(job / 'document.json')
    binding = {k: route[k] for k in ('id', 'api_base', 'model', 'auth_provider')}
    bound = manifest.setdefault('routes', {}).get(feature)
    if bound and bound != binding:
        raise ValueError('科研文档已有模型断点，请恢复原模型档案后继续。')
    manifest['routes'][feature] = binding
    save(job / 'document.json', manifest)


def analyze(job, number, route):
    path = page_dir(job, number) / 'page.json'
    if path.exists():
        return upgrade_page(job, number)
    bind_route(job, 'ocr', route)
    emit('正在识别本页…', 'researchConnecting')
    result_path = page_dir(job, number) / 'result-remote.json'
    # Recover a completed OCR request after interruption during page assembly.
    if result_path.exists():
        result = load(result_path)
    else:
        result = recognize(render_page(job, number), route)
        save(result_path, result)
    data = build_page(job, number, result, witness=False)
    data.update(approved=False, ocr_model=route['model'],
                ocr_unrecognized=bool(result.get('unrecognized_page')),
                ocr_unmatched_lines=int(result.get('unmatched_lines', 0)))
    save(path, data)
    emit('本页解析完成。', 'researchAnalyzed')
    return data


def translate(job, number, route, *, allow_unreviewed=False):
    path = page_dir(job, number) / 'page.json'
    data = upgrade_page(job, number)
    if not data['approved'] and not allow_unreviewed:
        raise ValueError('请先核对并确认本页 OCR 原文。')
    bind_route(job, 'translation', route)
    if allow_unreviewed and data.get('translation_mode') != 'batch':
        data['translation_mode'] = 'batch'
        save(path, data)
    manifest = load(job / 'document.json')
    terms = manifest['terms'].splitlines()
    key = load_key(Path.home() / '.local/share/opencode/auth.json', route['auth_provider'])
    blocks = [b for b in data['blocks'] if not b['visual'] and not b['translation']]
    failed = 0
    for i, block in enumerate(blocks):
        emit(f'翻译本页第 {i+1}/{len(blocks)} 段；公式、图表保留原图…',
             'researchTranslatingBlock', [i + 1, len(blocks)])
        heading = block.get('label') in {'title', 'document_title', 'paragraph_title', 'section_title'}
        protected, mapping, checks, instructions = prepare(block['source'], manifest.get('term_rules', []), number, terms)
        rule = '所有保留标记必须各保留一次且编号不变：' + ' '.join(mapping) if mapping else ''
        kind = '论文标题或章节标题' if heading else '学术正文'
        prompt = (f'将下面{kind}准确、完整地翻译成简体中文。只输出译文，不解释，不补写原文没有的内容。'
                  + rule + '\n' + instructions + '\n\n' + protected)
        target = None
        for attempt in range(2):
            raw, seconds = translate_once(route['api_base'].rstrip('/') + '/chat/completions',
                                         route['model'], key, prompt, 180)
            block.setdefault('attempts', []).append(dict(raw=raw, protected=mapping, seconds=round(seconds, 2)))
            save(path, data)
            try:
                target = validate(raw, mapping, checks)
                break
            except ValueError as exc:
                block['translation_error'] = str(exc)
                if attempt == 1:
                    failed += 1
                    block['status'] = 'translation_failed'
                    save(path, data)
                    break
                emit(str(exc) + '；正在重试本段…', 'researchRetryingBlock', detail=str(exc))
                prompt += '\n\n逐字保留以下标记，每个仅出现一次：' + ' '.join(mapping)
        if target is None:
            continue
        if not any('\u4e00' <= c <= '\u9fff' for c in target) and len(block['source']) > 100:
            raise ValueError('返回结果缺少中文译文，当前段未采纳。')
        block.update(translation=target, status='translated', model=route['model'], seconds=round(seconds, 2))
        block.pop('translation_error', None)
        save(path, data)
    if failed:
        emit(f'本页翻译结束，{failed} 段术语校验未通过；已完成段落已保存，可单独检查原文后重试。',
             'researchTranslatedPartial', [failed])
    else:
        emit('本页翻译完成，已保存。', 'researchTranslated')
    return failed


def batch(job, route, ocr_route):
    """Checkpoint each block and page. Resuming never replays saved translations."""
    bind_route(job, 'translation', route)
    manifest = load(job / 'document.json')
    count = int(manifest['page_count'])
    if any(not (page_dir(job, n) / 'page.json').exists() for n in range(1, count + 1)):
        bind_route(job, 'ocr', ocr_route)
    manifest = load(job / 'document.json')
    checkpoint = manifest.setdefault('batch', {})
    checkpoint.update(state='running', total=count, started_at=int(time.time()))
    checkpoint.setdefault('pages', {})
    save(job / 'document.json', manifest)
    try:
        for number in range(1, count + 1):
            emit(f'批量处理第 {number}/{count} 页', 'researchBatchProgress', [number, count])
            manifest = load(job / 'document.json')
            manifest['page'] = number
            manifest['batch']['current'] = number
            save(job / 'document.json', manifest)
            data = analyze(job, number, ocr_route)
            failed = translate(job, number, route, allow_unreviewed=True)
            manifest = load(job / 'document.json')
            # A wholly unrecognized page is never counted as a translated page.
            manifest['batch']['pages'][str(number)] = (
                'needs_review' if data.get('ocr_unrecognized') else 'partial' if failed else 'done')
            save(job / 'document.json', manifest)
        manifest = load(job / 'document.json')
        attention = sum(v != 'done' for v in manifest['batch']['pages'].values())
        manifest['batch'].update(state='partial' if attention else 'done', finished_at=int(time.time()))
        save(job / 'document.json', manifest)
        emit('批量处理结束。', 'researchBatchPartial' if attention else 'researchBatchDone',
             [attention] if attention else [count])
        return attention
    except Exception:
        manifest = load(job / 'document.json')
        manifest['batch'].update(state='interrupted')
        save(job / 'document.json', manifest)
        raise


def restore_job(job):
    manifest = load(Path(job) / 'document.json')
    number = max(1, min(int(manifest.get('page', 1)), int(manifest['page_count'])))
    render_page(job, number)
    if (page_dir(job, number) / 'page.json').exists():
        upgrade_page(job, number)
    emit('上次科研文档已恢复。', 'researchDocumentRestored')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['analyze', 'translate', 'batch', 'upgrade', 'restore', 'position'])
    parser.add_argument('job', type=Path)
    parser.add_argument('page', type=int)
    parser.add_argument('--route', default='{}')
    parser.add_argument('--ocr-route', default='{}')
    args = parser.parse_args()
    start_parent_watchdog(os.getppid())
    try:
        if args.action == 'analyze':
            analyze(args.job, args.page, json.loads(args.ocr_route))
        elif args.action == 'batch':
            if batch(args.job, json.loads(args.route), json.loads(args.ocr_route)):
                sys.exit(2)
        elif args.action == 'upgrade':
            upgrade_page(args.job, args.page)
            emit('本页译文纸张升级完成；旧数据已备份。', 'researchUpgraded')
        elif args.action == 'restore':
            restore_job(args.job)
        elif args.action == 'position':
            render_position(args.job, args.page, load(page_dir(args.job, args.page) / 'page.json'))
            emit('原位对照已就绪。未译区域保留原文，点击段落可查看全文。',
                 'researchPositionReady')
        else:
            translate(args.job, args.page, json.loads(args.route))
    except Exception as exc:
        emit(str(exc), 'researchFailed', detail=str(exc))
        sys.exit(1)
