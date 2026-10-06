"""Adopt an attested Qt prefix in a marked, isolated build virtualenv only."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import shutil
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def copy_file(source, destination):
    shutil.copyfile(source, destination)
    Path(destination).chmod(Path(source).stat().st_mode & 0o777)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('prefix', type=Path)
    parser.add_argument('--refresh', action='store_true')
    args = parser.parse_args()
    env = Path(sys.prefix).resolve()
    if env == Path(sys.base_prefix).resolve() or not env.is_relative_to((ROOT / 'build').resolve()):
        raise RuntimeError('Select a dedicated virtualenv inside this worktree build directory')
    if not (env / '.whaleread-isolated-build').is_file():
        raise RuntimeError('The build virtualenv is not explicitly marked as disposable')
    prefix = args.prefix.resolve()
    manifest_path = prefix / 'WHALEREAD_QT_BUILD.json'
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get('qt_version') != '6.11.0' or not manifest.get('appstore_compliant_feature')
            or not manifest.get('known_private_api_markers_absent') or not manifest.get('network_public_abi_preserved')
            or not manifest.get('known_runtime_diagnostic_paths_absent')
            or not manifest.get('binary_sha256')):
        raise ValueError('Qt source build has not been attested')
    for relative, expected in manifest['binary_sha256'].items():
        path = prefix / relative
        if not path.resolve().is_relative_to(prefix) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('The attested Qt source build has changed: ' + relative)
    spec = importlib.util.find_spec('PySide6')
    if not spec or not spec.origin:
        raise RuntimeError('The selected build environment needs PySide6 6.11.0')
    qt = Path(spec.origin).parent / 'Qt'
    if not qt.resolve().is_relative_to(env):
        raise RuntimeError('The selected PySide6 belongs to another environment')
    backup = env / ('Qt-build-backup-' + uuid.uuid4().hex if args.refresh else 'Qt-wheel-backup')
    if backup.exists():
        raise RuntimeError('This environment has already adopted Qt; create another isolated clone')
    qt.rename(backup)
    try:
        qt.mkdir()
        # Only runtime trees. CMake configuration, PRL files, SDK headers and
        # machine-specific build paths are never copied into the app runtime.
        (qt / 'lib').mkdir()
        for framework in (prefix / 'lib').glob('*.framework'):
            shutil.copytree(framework, qt / 'lib' / framework.name, symlinks=True, copy_function=copy_file)
        for name in ('plugins', 'qml'):
            shutil.copytree(prefix / name, qt / name, symlinks=True, copy_function=copy_file)
        for name in ('translations', 'libexec'):
            shutil.copytree(backup / name, qt / name, symlinks=True, copy_function=copy_file)
        copy_file(manifest_path, qt / manifest_path.name)
    except BaseException:
        shutil.rmtree(qt)
        backup.rename(qt)
        raise
    print('PASS: attested Qt adopted in isolated build environment; wheel backup retained')


if __name__ == '__main__':
    main()
