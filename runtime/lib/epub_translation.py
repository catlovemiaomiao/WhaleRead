"""Rebuild an EPUB from verified segment translations; never modify the source."""
import hashlib
import json
import os
import posixpath
import re
import tempfile
import zipfile
from pathlib import Path
from lxml import etree
from epub_reader import inspect_epub, xml, local, member
from task_config import atomic, epub_language_tag, extract_epub


def compact(text):
    return re.sub(r'\s+', '', text)


def translated_label(label, segments, translated, allowed=None):
    """Only join whole, consecutive source segments; never guess a translation."""
    expected = compact(label)
    if not expected:
        return None
    matches = []
    for start in range(len(segments)):
        if allowed is not None and start + 1 not in allowed:
            continue
        combined, numbers = '', []
        for index in range(start, len(segments)):
            if allowed is not None and index + 1 not in allowed:
                break
            combined += compact(segments[index])
            numbers.append(index + 1)
            if not expected.startswith(combined):
                break
            if combined == expected:
                if all(n in translated for n in numbers):
                    matches.append('　'.join(translated[n].strip() for n in numbers))
                else:
                    matches.append(None)
                break
    return matches[0] if matches and all(m == matches[0] for m in matches) else None


def attach(root, source):
    """Sidecar deliberately leaves translation rules and checkpoints untouched."""
    root, source = Path(root), Path(source)
    # Attaching a translation task validates the source package, but it must not
    # materialize a GUI reading cache.  The reader prepares that cache only when
    # the book is actually opened under its explicit StorageContext.
    validated = inspect_epub(source)
    content = source.read_bytes()
    digest = validated['source_hash']
    destination = root / '原文' / ('原版-' + digest[:16] + '.epub')
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and destination.read_bytes() != content:
        raise ValueError('EPUB 原版副本指纹不一致')
    if not destination.exists():
        fd, temporary = tempfile.mkstemp(dir=destination.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
            os.replace(temporary, destination)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    atomic(root / '.epub-source.json', json.dumps(dict(source=str(destination.relative_to(root)), sha256=digest), ensure_ascii=False))


def slots(node):
    # Same excluded elements as the existing EPUB prose extractor. Include
    # head/title to maintain exact compatibility with already translated books.
    if not isinstance(node.tag, str) or local(node) in {'script', 'style', 'svg', 'nav'}:
        return
    if node.text:
        yield node, 'text', node.text
    for child in node:
        yield from slots(child)
        if child.tail:
            yield child, 'tail', child.tail


def export(root, output, segments, translated, *, language_name=''):
    root, output = Path(root), Path(output)
    metadata = json.loads((root / '.epub-source.json').read_text())
    source = (root / metadata['source']).resolve()
    if not source.is_relative_to(root.resolve()) or hashlib.sha256(source.read_bytes()).hexdigest() != metadata['sha256']:
        raise ValueError('EPUB 原版指纹改变，停止回填')
    if compact(extract_epub(source)) != compact(''.join(segments)):
        raise ValueError('EPUB 原文与已译段落无法精确对应，停止回填')
    documents, ordered_slots, document_ranges = {}, [], {}
    with zipfile.ZipFile(source) as archive:
        container = xml(archive.read('META-INF/container.xml'))
        opf_path = next(n.get('full-path') for n in container.iter() if local(n) == 'rootfile')
        opf = xml(archive.read(opf_path))
        manifest = {n.get('id'): n for n in opf.iter() if local(n) == 'item'}
        for ref in opf.iter():
            if local(ref) != 'itemref':
                continue
            item = manifest.get(ref.get('idref'))
            if item is None or 'html' not in item.get('media-type', ''):
                continue
            name = member(posixpath.dirname(opf_path), item.get('href'))
            if name in documents:
                raise ValueError('重复书脊章节不支持回填')
            doc = xml(archive.read(name))
            documents[name] = doc
            start = sum(len(compact(t)) for _, _, t in ordered_slots)
            chapter_slots = list(slots(doc))
            ordered_slots.extend(chapter_slots)
            document_ranges[name] = (start, start + sum(len(compact(t)) for _, _, t in chapter_slots))
        actual = ''.join(compact(text) for _, _, text in ordered_slots)
        expected = ''.join(compact(text) for text in segments)
        if actual != expected:
            raise ValueError('EPUB 标签文本与段落映射不一致，停止回填')
        # Map exact source character offsets to DOM text slots. Inline elements,
        # images, IDs and links remain present; no guessed sentence alignment.
        replacements = []
        cursor = 0
        for number, text in enumerate(segments, 1):
            end = cursor + len(compact(text))
            if number in translated:
                replacements.append((cursor, end, translated[number]))
            cursor = end
        boundaries = {}
        offset = 0
        for number, segment in enumerate(segments, 1):
            boundaries[offset] = (number, segment)
            offset += len(compact(segment))
        cursor, index = 0, 0
        for node, attribute, text in ordered_slots:
            result = []
            for character in text:
                if character.isspace():
                    result.append(character)
                    continue
                if cursor in boundaries:
                    number, original_text = boundaries[cursor]
                    block = node if attribute == 'text' else node.getparent()
                    while block is not None and local(block) not in {'p', 'div', 'li', 'h1', 'h2', 'h3', 'blockquote', 'td', 'body'}:
                        block = block.getparent()
                    if block is not None:
                        block.set('data-whale-source-text', (block.get('data-whale-source-text', '') + '\n' + original_text).strip())
                        block.set('data-whale-segments', (block.get('data-whale-segments', '') + ' ' + str(number)).strip())
                        if number not in translated:
                            block.set('data-whale-pending', 'true')
                while index < len(replacements) and cursor >= replacements[index][1]:
                    index += 1
                if index < len(replacements) and replacements[index][0] <= cursor < replacements[index][1]:
                    if cursor == replacements[index][0]:
                        result.append(replacements[index][2])
                else:
                    result.append(character)
                cursor += 1
            setattr(node, attribute, ''.join(result))
        complete = len(translated) == len(segments)
        for doc in documents.values():
            head = next((n for n in doc.iter() if local(n) == 'head'), None)
            if head is not None:
                style = etree.SubElement(head, '{http://www.w3.org/1999/xhtml}style')
                style.text = '[data-whale-pending="true"]::before { content: "原文 · 待译"; display:block; font:11px sans-serif; color:#7c857c; margin:1em 0 .4em; }'
        chapter_segments = {name: {n for offset, (n, _) in boundaries.items() if start <= offset < end}
                            for name, (start, end) in document_ranges.items()}
        # Keep NCX/nav links and ordering, translate only labels with exact evidence.
        for item in manifest.values():
            if 'nav' in item.get('properties', '').split() or item.get('media-type') == 'application/x-dtbncx+xml':
                name = member(posixpath.dirname(opf_path), item.get('href'))
                doc = documents.get(name)
                if doc is None:
                    doc = xml(archive.read(name))
                for node in doc.iter():
                    if local(node) not in {'text', 'a'} or len(node):
                        continue
                    href = node.get('href', '')
                    if local(node) == 'text':
                        parent = node.getparent()
                        point = parent.getparent() if parent is not None else None
                        content = next((n for n in point if local(n) == 'content'), None) if point is not None else None
                        href = content.get('src', '') if content is not None else ''
                    destination = member(posixpath.dirname(name), href.split('#')[0]) if href.split('#')[0] else name
                    translated_title = translated_label(node.text or '', segments, translated, chapter_segments.get(destination, set()))
                    if translated_title is not None:
                        node.text = translated_title
                documents[name] = doc
        for node in opf.iter():
            if local(node) == 'language' and complete:
                node.text = epub_language_tag(language_name) if language_name else 'zh-CN'
            elif local(node) == 'title':
                node.text = (translated_label(node.text or '', segments, translated) or node.text or '') + (' · 译本' if complete else ' · 翻译中')
        documents[opf_path] = opf
        output.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(suffix='.epub', dir=output.parent)
        os.close(fd)
        try:
            with zipfile.ZipFile(temporary, 'w', zipfile.ZIP_DEFLATED) as result:
                result.writestr('mimetype', 'application/epub+zip', compress_type=zipfile.ZIP_STORED)
                for info in archive.infolist():
                    if info.filename == 'mimetype':
                        continue
                    data = etree.tostring(documents[info.filename], encoding='utf-8', xml_declaration=True) if info.filename in documents else archive.read(info.filename)
                    result.writestr(info, data)
            with zipfile.ZipFile(temporary) as check:
                if check.testzip() is not None:
                    raise ValueError('EPUB 输出校验失败')
            os.replace(temporary, output)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    return output
