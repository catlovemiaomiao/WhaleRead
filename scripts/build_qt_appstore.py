"""Build isolated Qt 6.11.0 with Apple's private API paths disabled.

Official source archives are hash pinned. No installed/shared PySide environment
is changed. The output manifest describes compiled files, not store approval.
Run with the selected build Python; then explicitly adopt this prefix into a
separate build environment using the release instructions.
"""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import shutil
import shlex
import ssl
import subprocess
import tarfile
import urllib.request
from pathlib import Path

import certifi

VERSION = '6.11.0'
ROOT = Path(__file__).resolve().parents[1]
SOURCE_BASE = 'https://download.qt.io/official_releases/qt/6.11/6.11.0/submodules/'
SOURCE_SHA256 = {
    'qtbase': '231ad85979864d914dc9568a1b71c91d6cf20d7b2021d059103bf0eb51cb755e',
    'qtshadertools': 'e43cb1ae8809b2a858281ee269f98da59d0fc1bcf958ca5510c81f7ad3d2e14a',
    'qtsvg': 'dfa8d653be07087d9407ed4a4ebae847f8953e0b7abd829f089803ab652a30e6',
    'qtdeclarative': '4eece569431ddf8324e7d322fa27001916570b23df535f8fb28aba445eedfde9',
    'qtwebview': 'cb0eaed94a12d5f650863d346c423e9f4383dbce1d05866869c40118c6e8c4b3',
    'qtimageformats': 'd3adb02ac5e2fe24068dbdaee0d7cc68cc3fa8553291c1bfce77c9fe8e940cc8',
}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources(root: Path) -> list[dict]:
    root.mkdir(parents=True, exist_ok=True)
    context = ssl.create_default_context(cafile=certifi.where())
    records = []
    for module, expected in SOURCE_SHA256.items():
        name = f'{module}-everywhere-src-{VERSION}.tar.xz'
        archive = root / name
        if not archive.exists():
            print('Download official source:', module, flush=True)
            with urllib.request.urlopen(SOURCE_BASE + name, context=context, timeout=120) as response:
                temporary = archive.with_suffix('.download')
                with temporary.open('wb') as output:
                    shutil.copyfileobj(response, output)
                temporary.replace(archive)
        if digest(archive) != expected:
            raise ValueError(f'Official source hash mismatch: {module}')
        directory = root / name.removesuffix('.tar.xz')
        if not directory.exists():
            with tarfile.open(archive) as source:
                source.extractall(root, filter='data')
        records.append(dict(module=module, version=VERSION, sha256=expected, source_url=SOURCE_BASE + name))
    return records


def run(command, directory: Path, log: Path):
    print('Run', directory.name, ':', ' '.join(map(str, command)), flush=True)
    with log.open('w') as output:
        subprocess.run(list(map(str, command)), cwd=directory, stdout=output,
                       stderr=subprocess.STDOUT, check=True)


def prefix_flags(source: Path, build: Path):
    return ['-ffile-prefix-map=' + str(ROOT) + '=/whaleread-build',
            '-ffile-prefix-map=' + str(build) + '=/qt-build',
            '-ffile-prefix-map=' + str(source) + '=/qt-sources']


def sanitize_cached_objects(directory: Path, flags: list[str], log: Path):
    """Recompile diagnostic-path objects from an earlier cache, then relink.

    Fresh builds receive the same compiler mapping through CMake. This bounded
    upgrade path avoids rebuilding every clean object in a verified Qt cache.
    It changes compiler diagnostic filenames, never patches runtime binaries.
    """
    rows = json.loads(subprocess.check_output(['ninja', '-t', 'compdb'], cwd=directory, text=True))
    affected = []
    ninja_file = directory / 'build.ninja'
    lines = ninja_file.read_text().splitlines(True)
    with log.open('w') as output:
        for row in rows:
            target = (directory / row['output']).resolve()
            if (target.suffix != '.o' or not target.is_relative_to(directory)
                    or not target.is_file() or str(ROOT).encode() not in target.read_bytes()):
                continue
            # This upstream test helper embeds its fixture search directory as
            # data, not a compiler diagnostic filename. The runtime allowlist
            # in WhaleRead.spec excludes it; the final bundle still undergoes
            # the full developer-path scan. Do not edit unrelated Qt test code.
            if 'CMakeFiles/QuickTestUtilsPrivate.dir/' in row['output']:
                output.write('Excluded unshipped Qt test helper: ' + row['output'] + '\n')
                continue
            command = shlex.split(row['command'])
            if not command or Path(command[0]).name not in {'cc', 'c++', 'clang', 'clang++'}:
                raise ValueError('Unexpected compiler command for cached object')
            # Register the new compiler command in Ninja itself. Invoking the
            # compiler outside Ninja causes its dependency log to regard the
            # object as stale and compile it again without mappings at relink.
            # This affects only disposable generated cache rules; fresh CMake
            # builds already receive these flags globally above.
            edge = 'build ' + row['output'].replace('$', '$$').replace(' ', '$ ') + ':'
            starts = [i for i, line in enumerate(lines) if line.startswith(edge + ' ')]
            if len(starts) != 1:
                raise ValueError('Ambiguous generated compiler edge: ' + row['output'])
            start = starts[0]
            end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith('build ')), len(lines))
            fields = [i for i in range(start + 1, end) if lines[i].startswith('  FLAGS = ')]
            if len(fields) != 1:
                raise ValueError('Missing generated compiler flags: ' + row['output'])
            index = fields[0]
            encoded = shlex.join(flags).replace('$', '$$')
            lines[index] = lines[index].rstrip('\n') + ' ' + encoded + '\n'
            affected.append(row['output'])
        if affected:
            ninja_file.write_text(''.join(lines))
            subprocess.run(['ninja', *affected], cwd=directory,
                           stdout=output, stderr=subprocess.STDOUT, check=True)
            for relative in affected:
                if str(ROOT).encode() in (directory / relative).read_bytes():
                    raise ValueError('Compiler prefix mapping did not remove diagnostic checkout path: ' + relative)
    print('Sanitized compiler diagnostic objects:', directory.name, len(affected), flush=True)
    return len(affected)


def apply_build_patch(source_root: Path):
    """Qt's file(STRINGS) otherwise splits non-ASCII paths in .prl metadata."""
    name = f'qtbase-everywhere-src-{VERSION}'
    with tarfile.open(source_root / (name + '.tar.xz')) as archive:
        stream = archive.extractfile(name + '/cmake/QtFinishPrlFile.cmake')
        original = stream.read().decode('utf-8')
    revised = original.replace('file(STRINGS "${IN_FILE}" lines)',
        'file(STRINGS "${IN_FILE}" lines ENCODING UTF-8)').replace(
        'file(STRINGS "${IN_META_FILE}" lines)', 'file(STRINGS "${IN_META_FILE}" lines ENCODING UTF-8)')
    patch = ''.join(difflib.unified_diff(original.splitlines(True), revised.splitlines(True),
        fromfile='a/cmake/QtFinishPrlFile.cmake', tofile='b/cmake/QtFinishPrlFile.cmake'))
    path = ROOT / 'packaging/licenses/Qt/QtFinishPrlFile-utf8.patch'
    if not patch or path.read_text() != patch:
        raise ValueError('The recorded Qt build patch does not match the pinned source')
    destination = source_root / name / 'cmake/QtFinishPrlFile.cmake'
    if destination.read_text() not in (original, revised):
        raise ValueError('Unexpected local changes in Qt build metadata code')
    if destination.read_text() != revised:
        destination.write_text(revised)
    return dict(file='Qt/QtFinishPrlFile-utf8.patch', sha256=digest(path),
                purpose='Preserve UTF-8 paths in upstream CMake PRL metadata; no runtime API change')


def attest(prefix: Path, records: list[dict]) -> dict:
    config = prefix / 'lib/QtCore.framework/Headers/qconfig.h'
    text = config.read_text()
    if '#define QT_FEATURE_appstore_compliant 1' not in text or '#define QT_APPLE_NO_PRIVATE_APIS' not in text:
        raise ValueError('Installed Qt lacks the required App Store feature')
    network = (prefix / 'lib/QtNetwork.framework/Headers/qtnetwork-config.h').read_text()
    if any('#define QT_FEATURE_' + name + ' 1' not in network for name in ('dtls', 'ocsp')):
        raise ValueError('Qt Network public ABI required by PySide6 is missing')
    cocoa = prefix / 'plugins/platforms/libqcocoa.dylib'
    core = prefix / 'lib/QtCore.framework/Versions/A/QtCore'
    markers = {
        cocoa: (b'_helpCursor', b'busyButClickableCursor', b'_windowResizeNorthWestSouthEastCursor'),
        core: (b'__ulock_wait', b'__ulock_wake', b'csr_check', b'responsibility_get_pid_responsible_for_pid'),
    }
    for path, forbidden in markers.items():
        data = path.read_bytes()
        if any(marker in data for marker in forbidden):
            raise ValueError('Private API marker survived the rebuild: ' + path.name)
    for relative in ('lib/QtQuick.framework/Versions/A/QtQuick',
                     'lib/QtWidgets.framework/Versions/A/QtWidgets',
                     'plugins/imageformats/libqtiff.dylib'):
        if str(ROOT).encode() in (prefix / relative).read_bytes():
            raise ValueError('Compiler diagnostic checkout path survived relinking: ' + relative)
    files = {}
    for folder in ('lib', 'plugins', 'qml'):
        for path in (prefix / folder).rglob('*'):
            # CMake installs static-link object files under lib/objects-*.
            # They are build inputs, not the dynamically shipped runtime.
            if path.is_file() and not path.is_symlink() and path.suffix != '.o':
                with path.open('rb') as stream:
                    magic = stream.read(4)
                if magic in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe'):
                    files[path.relative_to(prefix).as_posix()] = digest(path)
    return dict(qt_version=VERSION, appstore_compliant_feature=True,
                known_private_api_markers_absent=True, network_public_abi_preserved=True,
                known_runtime_diagnostic_paths_absent=True,
                architecture='arm64', minimum_macos='13.0',
                sources=records, binary_sha256=files, store_approval=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, required=True)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--jobs', type=int, default=3)
    parser.add_argument('--openssl-root', type=Path)
    parser.add_argument('--fresh-configure', action='store_true')
    args = parser.parse_args()
    if not 1 <= args.jobs <= 8:
        parser.error('--jobs must be between 1 and 8')
    src, build, prefix = args.source_dir.resolve(), args.build_dir.resolve(), args.prefix.resolve()
    if prefix in (src, build) or prefix.is_relative_to(src):
        parser.error('The install prefix must be separate from sources/builds')
    cmake = shutil.which('cmake')
    if not cmake:
        raise RuntimeError('CMake is required')
    records = sources(src)
    patch = apply_build_patch(src)
    build.mkdir(parents=True, exist_ok=True)
    if not shutil.which('ninja'):
        raise RuntimeError('Ninja is required by the supported Qt build workflow')
    generator = 'Ninja'
    common = ['-G', generator, '-DCMAKE_OSX_ARCHITECTURES=arm64',
              '-DCMAKE_OSX_DEPLOYMENT_TARGET=13.0',
              '-DCMAKE_C_FLAGS=' + ' '.join(prefix_flags(src, build)),
              '-DCMAKE_CXX_FLAGS=' + ' '.join(prefix_flags(src, build))]
    sanitized = {}
    for module in SOURCE_SHA256:
        directory = build / module
        directory.mkdir(exist_ok=True)
        source = src / f'{module}-everywhere-src-{VERSION}'
        previous_cache = (directory / 'CMakeCache.txt').read_text() if (directory / 'CMakeCache.txt').exists() else ''
        needs_configure = not previous_cache or (module == 'qtbase' and (args.fresh_configure or
            ('QT_FEATURE_dtls:INTERNAL=ON' not in previous_cache or 'QT_FEATURE_ocsp:INTERNAL=ON' not in previous_cache))
            )
        if needs_configure:
            if module == 'qtbase':
                ssl_args = ['-DOPENSSL_ROOT_DIR=' + str(args.openssl_root.resolve())] if args.openssl_root else []
                command = [source / 'configure', '-prefix', prefix, '-release', '-opensource',
                           '-confirm-license', '-nomake', 'examples', '-nomake', 'tests',
                           '-feature-appstore-compliant', '-openssl-runtime', '-feature-openssl',
                           '-feature-openssl-runtime', '-feature-opensslv30', '-feature-dtls',
                           '-feature-ocsp', '--', *common, *ssl_args,
                           *(['--fresh'] if args.fresh_configure else [])]
            else:
                command = [prefix / 'bin/qt-configure-module', source, '--', *common]
            run(command, directory, build / (module + '-configure.log'))
        cache = (directory / 'CMakeCache.txt').read_text()
        if module == 'qtbase' and 'QT_FEATURE_appstore_compliant:INTERNAL=ON' not in cache:
            raise ValueError('An existing Qt build was configured without App Store compliance')
        if module == 'qtbase' and ('QT_FEATURE_dtls:INTERNAL=ON' not in cache or 'QT_FEATURE_ocsp:INTERNAL=ON' not in cache):
            raise ValueError('The Qt Network public ABI must retain the features used by the PySide wheel')
        run([cmake, '--build', '.', '--parallel', str(args.jobs)], directory, build / (module + '-build.log'))
        sanitized[module] = sanitize_cached_objects(directory, prefix_flags(src, build),
            build / (module + '-prefix-map.log'))
        if sanitized[module]:
            run([cmake, '--build', '.', '--parallel', str(args.jobs)], directory,
                build / (module + '-relink.log'))
        run([cmake, '--install', '.'], directory, build / (module + '-install.log'))
    manifest = attest(prefix, records)
    manifest['build_patch'] = patch
    manifest['compiler_path_mapping'] = dict(source='/qt-sources', build='/qt-build',
        checkout='/whaleread-build', upgraded_cached_objects=sanitized)
    (prefix / 'WHALEREAD_QT_BUILD.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print('PASS: isolated Qt build verified; licensing and App Store review remain separate.', flush=True)


if __name__ == '__main__':
    main()
