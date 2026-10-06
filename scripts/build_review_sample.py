"""Reproduce the original, credential-free EPUB used in App Review material.

The source paragraphs come from docs/review-samples/harbor.txt. This script
does not open the app, touch a library, call an API or create a review account.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
from xml.sax.saxutils import escape
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def sample_epub(source):
    sections = source.read_text(encoding='utf-8').strip().split('\n\n')
    title, paragraphs = sections[0], sections[1:]
    if title != 'The Harbor Letter' or len(paragraphs) != 5:
        raise ValueError('Expected the reviewed Harbor Letter title and five paragraphs')
    chapters = [('crossing', 'The crossing', paragraphs[:2]),
                ('letter', 'The letter', paragraphs[2:])]
    items = {
        'mimetype': 'application/epub+zip',
        'META-INF/container.xml': '''<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="EPUB/package.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>''',
        'EPUB/style.css': '''body { font-family: serif; line-height: 1.65; margin: 1.5em; }
h1 { font-size: 1.6em; line-height: 1.25; }
p { margin: 0 0 1em; }
''',
    }
    manifest, spine, navigation = [], [], []
    for slug, heading, text in chapters:
        manifest.append(f'<item id="{slug}" href="{slug}.xhtml" media-type="application/xhtml+xml"/>')
        spine.append(f'<itemref idref="{slug}"/>')
        navigation.append(f'<li><a href="{slug}.xhtml#heading">{escape(heading)}</a></li>')
        prose = '\n'.join(f'<p id="paragraph-{index}">{escape(paragraph)}</p>'
                          for index, paragraph in enumerate(text, 1))
        items[f'EPUB/{slug}.xhtml'] = f'''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en" lang="en">
  <head><title>{escape(heading)}</title><link rel="stylesheet" type="text/css" href="style.css"/></head>
  <body><h1 id="heading">{escape(heading)}</h1>{prose}</body>
</html>'''
    items['EPUB/nav.xhtml'] = f'''<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="en" lang="en">
  <head><title>Contents</title><link rel="stylesheet" type="text/css" href="style.css"/></head>
  <body><nav epub:type="toc" id="toc"><h1>Contents</h1><ol>{''.join(navigation)}</ol></nav></body>
</html>'''
    items['EPUB/package.opf'] = f'''<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id" xml:lang="en">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">urn:whaleread:review-sample:harbor-letter</dc:identifier>
    <dc:title>{escape(title)}</dc:title>
    <dc:language>en</dc:language>
    <dc:creator>WhaleRead review sample</dc:creator>
    <dc:description>Original fictional material for app review and testing.</dc:description>
    <meta property="dcterms:modified">2026-10-01T00:00:00Z</meta>
  </metadata>
  <manifest><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="style" href="style.css" media-type="text/css"/>{''.join(manifest)}</manifest>
  <spine>{''.join(spine)}</spine>
</package>'''
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, text in items.items():
            info = zipfile.ZipInfo(name, date_time=(2026, 10, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, text.encode('utf-8'))
    return buffer.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/review-samples/harbor.epub')
    args = parser.parse_args()
    content = sample_epub(ROOT / 'docs/review-samples/harbor.txt')
    if args.output.is_symlink():
        raise ValueError('Refusing a symlink output')
    if args.output.exists():
        if args.output.read_bytes() != content:
            raise ValueError('Refusing to replace a different existing file')
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open('xb') as stream:
            stream.write(content)
    print(json.dumps(dict(file=str(args.output), bytes=len(content),
                          sha256=hashlib.sha256(content).hexdigest()), indent=2))


if __name__ == '__main__':
    main()
