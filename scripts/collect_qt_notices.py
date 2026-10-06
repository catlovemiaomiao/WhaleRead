"""Preserve upstream attribution records and the license files they reference."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from build_qt_appstore import SOURCE_SHA256, VERSION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source, output = args.source_dir.resolve(), args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    inventory, missing = [], []
    for module in SOURCE_SHA256:
        root = source / f'{module}-everywhere-src-{VERSION}'
        for metadata in sorted(root.rglob('qt_attribution.json')):
            # Official Qt attribution files include literal tabs/newlines in
            # descriptive strings. Preserve that upstream text verbatim.
            raw = json.loads(metadata.read_text(), strict=False)
            rows = raw if isinstance(raw, list) else [raw]
            relative = metadata.relative_to(root)
            target = output / module / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(metadata, target)
            for row in rows:
                files = row.get('LicenseFile', [])
                if isinstance(files, str):
                    files = files.split()
                copied = []
                for filename in files:
                    path = (metadata.parent / filename).resolve()
                    if not path.is_relative_to(root) or not path.is_file():
                        missing.append(f'{module}/{relative}: {filename}')
                        continue
                    destination = output / module / path.relative_to(root)
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, destination)
                    copied.append(destination.relative_to(output).as_posix())
                inventory.append(dict(module=module, id=row.get('Id'), name=row.get('Name'),
                    version=row.get('Version'), license=row.get('License'), license_id=row.get('LicenseId'),
                    copyright=row.get('Copyright'), usage=row.get('QtUsage'),
                    attribution=(Path(module) / relative).as_posix(), license_files=copied))
        for path in (root / 'LICENSES').glob('*.txt'):
            destination = output / module / 'LICENSES' / path.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
    (output / 'ATTRIBUTIONS.json').write_text(json.dumps(dict(
        scope='Upstream source notices, including build tools and other platforms; not a binary usage claim',
        qt_version=VERSION, records=inventory, missing_referenced_files=missing), ensure_ascii=False, indent=2) + '\n')
    if missing:
        raise ValueError('Referenced upstream license files missing: ' + '; '.join(missing))
    print(f'PASS: {len(inventory)} upstream attribution records and referenced notices preserved')


if __name__ == '__main__':
    main()
