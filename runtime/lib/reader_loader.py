"""Background-safe preparation for shelf book switches and EPUB refreshes."""
from __future__ import annotations

import sys
from pathlib import Path

from epub_session import prepare_epub
from epub_reader import inspect_epub
from reader import prepare_document
from storage_context import StorageContext
from task_config import detect_language, extract_source_text


def existing_target(raw_path):
    if not raw_path:
        return None
    path = Path(raw_path).expanduser().resolve()
    if not path.is_file() and str(path).endswith('.partial'):
        path = Path(str(path)[:-8])
    return path if path.is_file() else None


def translated_companion(target):
    target = Path(target)
    return target.with_name(target.name.removesuffix('.partial')).with_suffix('.epub')


def is_epub_review_text(descriptor, target):
    """Return whether ``target`` is a managed text edition of an EPUB task.

    The text file remains the authoritative, ledger-verifiable review artifact.
    The reader may derive an EPUB companion from it, but arbitrary TXT files in
    a task directory must never be treated as reviewed editions by guesswork.
    """
    job = str((descriptor or {}).get('job') or '')
    if not job or not target:
        return False
    root = Path(job).expanduser().resolve()
    path = Path(target).expanduser().resolve()
    source = Path(str((descriptor or {}).get('source') or ''))
    epub_task = ((root / '.epub-source.json').is_file()
                 or source.suffix.lower() == '.epub')
    return bool(
        epub_task
        and path.is_file()
        and path.suffix.lower() == '.txt'
        and path.name.endswith('.当前阅读版.txt')
        and path.parent == (root / '译后校对').resolve()
    )


def path_signature(path):
    path = Path(path).resolve()
    stat = path.stat()
    return (str(path), stat.st_dev, stat.st_ino, stat.st_size,
            stat.st_mtime_ns, stat.st_ctime_ns)


def inspect_source(path):
    """Extract source text and language away from the GUI event loop."""
    path = Path(path).expanduser().resolve()
    before = path_signature(path)
    text = extract_source_text(path)
    after = path_signature(path)
    if before != after:
        raise OSError('原文正在替换，请稍后重试')
    detection = detect_language(text[:200_000])
    return dict(path=str(path), signature=after, size=after[3],
                characters=len(text), language=detection.get('language') or '',
                detection=detection)


def epub_work_signature(descriptor, reading_file, machine_path):
    """Cheap main-thread check used to avoid queueing unchanged EPUB work."""
    target = existing_target(reading_file or machine_path)
    if target is None:
        return None
    if target.suffix.lower() == '.epub':
        return ('epub', path_signature(target))
    job = str(descriptor.get('job') or '')
    original = Path(str(descriptor.get('source') or ''))
    if is_epub_review_text(descriptor, target):
        companion = translated_companion(target)
        machine = existing_target(machine_path)
        machine_companion = (
            translated_companion(machine) if machine is not None else None
        )
        return (
            'reviewed-epub',
            path_signature(target),
            path_signature(companion) if companion.is_file() else None,
            path_signature(machine) if machine is not None else None,
            (path_signature(machine_companion)
             if machine_companion is not None and machine_companion.is_file()
             else None),
        )
    if not job or reading_file or original.suffix.lower() != '.epub':
        return None
    companion = translated_companion(target)
    return ('translated-epub', path_signature(target),
            path_signature(companion) if companion.is_file() else None)


def sync_current_reading(app_root, descriptor, reading_file, machine_path):
    """Refresh an explicit current-reading edition without touching Qt state."""
    if not descriptor.get('job') or not reading_file or not machine_path:
        return
    root = Path(descriptor['job']).resolve()
    path = Path(reading_file).resolve()
    machine = existing_target(machine_path)
    if (path.parent != (root / '译后校对').resolve()
            or not path.name.endswith('.当前阅读版.txt')
            or machine is None or not path.is_file()):
        return
    try:
        sys.path.insert(0, str(Path(app_root) / 'runtime/tools'))
        import annotations as human_notes
        import post_edit
        human_notes.sync_current_edition(
            root, path, post_edit.snapshot(root), post_edit.engine)
    except (OSError, ValueError, RuntimeError, KeyError, IndexError):
        # Translation state and its visible file are committed separately. The
        # regular refresh will retry after the next stable checkpoint.
        return


def prepare_book(app_root, descriptor, reading_file, machine_path,
                 storage_context=None):
    """Perform all file reads, hashing, export and extraction off the GUI thread."""
    storage = storage_context or StorageContext.production()
    storage.assert_test_safe()
    descriptor = dict(descriptor)
    reading_file = str(reading_file or '')
    sync_current_reading(app_root, descriptor, reading_file, machine_path)
    target = existing_target(reading_file or machine_path)
    identity = str(descriptor.get('reader_identity') or descriptor.get('job')
                   or descriptor.get('source') or '')
    if target is None:
        return dict(kind='empty', message=('尚无可读译文；可切换到此书的翻译任务。'
                                           if descriptor.get('job') else '文件已移动，请重新打开。'))
    if target.suffix.lower() == '.epub':
        job = str(descriptor.get('job') or '')
        original = Path(str(descriptor.get('source') or '')).expanduser().resolve()
        translated = bool(job and target != original
                          and not target.is_relative_to(Path(job).resolve() / '原文'))
        metadata_source = (original if original.suffix.lower() == '.epub'
                           and original.is_file() else target)
        shelf_metadata = inspect_epub(
            metadata_source, thumbnail_root=storage.thumbnail_root)
        prepared = prepare_epub(
            target, cache_identity=identity,
            edition_identity=('translation' if translated else 'original'),
            storage_context=storage)
        return dict(kind='epub', prepared=prepared, translated=translated,
                    reading_path=str(target), shelf_metadata=shelf_metadata)

    job = Path(str(descriptor.get('job') or '')).resolve() if descriptor.get('job') else None
    original = Path(str(descriptor.get('source') or '')).resolve()
    reviewed_text = is_epub_review_text(descriptor, target)
    epub_task = bool(job is not None and (
        (job / '.epub-source.json').is_file() or original.suffix.lower() == '.epub'
    ))
    if epub_task and (not reading_file or reviewed_text):
        sys.path.insert(0, str(Path(app_root) / 'runtime/tools'))
        from epub_translation import attach, export
        metadata = job / '.epub-source.json'
        if not metadata.is_file() and original.is_file():
            attach(job, original)
        if metadata.is_file():
            companion = translated_companion(target)
            needs_export = (not companion.exists()
                            or companion.stat().st_mtime_ns < target.stat().st_mtime_ns)
            if needs_export and descriptor.get('_allow_epub_export', True):
                import post_edit
                book = post_edit.snapshot(job)
                if target.name.endswith('.partial'):
                    from task_config import atomic
                    atomic(target.with_name(target.name.removesuffix('.partial')),
                           target.read_text(encoding='utf-8'))
                translations = book['translated']
                if reviewed_text:
                    import annotations as human_notes
                    translations = human_notes.edition(
                        job, target, book, post_edit.engine)
                from task_config import task_target_language
                export(job, companion, book['segments'], translations,
                       language_name=task_target_language(job))
            if not companion.is_file():
                return dict(kind='empty', message='译文已安全保存，EPUB 阅读版正在合并发布。')
            metadata_source = (
                original if original.suffix.lower() == '.epub' and original.is_file()
                else companion
            )
            shelf_metadata = inspect_epub(
                metadata_source, thumbnail_root=storage.thumbnail_root)
            prepared = prepare_epub(
                companion, cache_identity=identity, edition_identity='translation',
                storage_context=storage)
            return dict(kind='epub', prepared=prepared, translated=True,
                        reading_path=str(companion), shelf_metadata=shelf_metadata)

    prepared = prepare_document(target)
    result = dict(kind='text', prepared=prepared, reading_path=str(target),
                  sources=[], alignment_anchors=[], alignment_signature=None,
                  bilingual_message='')
    job = str(descriptor.get('job') or '')
    if job:
        try:
            sys.path.insert(0, str(Path(app_root) / 'runtime/tools'))
            import post_edit
            from annotations import bilingual_alignment
            root = Path(job).resolve()
            sources, anchors = bilingual_alignment(
                root, target, post_edit.snapshot(root), post_edit.engine,
                prepared['rows'])
            signature = tuple(prepared['signature'])
            result.update(
                sources=sources,
                alignment_anchors=anchors,
                alignment_signature=(job, str(target), signature[4],
                                     signature[3], len(prepared['rows'])))
        except (OSError, ValueError, RuntimeError, KeyError, TypeError, IndexError):
            signature = tuple(prepared['signature'])
            result.update(
                alignment_signature=(job, str(target), signature[4],
                                     signature[3], len(prepared['rows'])),
                bilingual_message='原文对照暂不可用，正在等待可验证的段落映射。')
    return result


def publish_epub_checkpoint(app_root, task_root):
    """Publish the newest verified checkpoint without making any API call."""
    root = Path(task_root).expanduser().resolve()
    if not (root / '.epub-source.json').is_file():
        return None
    sys.path.insert(0, str(Path(app_root) / 'runtime/tools'))
    from translate_range import publish_saved_outputs
    return publish_saved_outputs(root)
