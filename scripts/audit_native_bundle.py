"""Fail closed if an unwanted reader/binding or unresolved Qt dependency ships."""
import re
import json
import plistlib
import subprocess
import sys
from pathlib import Path

app = Path(sys.argv[1]).resolve()
assert app.name == '鲸读.app' and (app/'Contents/Info.plist').is_file()
info = plistlib.loads((app/'Contents/Info.plist').read_bytes())
if info.get('WhaleReadReceiptValidation') == 'standalone':
    assert not (app/'Contents/Frameworks/WhaleReadPurchase.dylib').exists(), 'A paid launcher must not ship in the GitHub edition'
    assert not (app/'Contents/Resources/STORE_LAUNCHER.json').exists(), 'Unexpected Store launcher metadata'
    print('PASS: standalone GitHub edition contains no StoreKit purchase launcher')
else:
    launcher_links = subprocess.check_output(['/usr/bin/otool', '-L', str(app/'Contents/Frameworks/WhaleReadPurchase.dylib')], text=True)
    assert 'StoreKit.framework' in launcher_links, 'Native purchase verification entry is missing'
    assert '.framework/Versions/A/Qt' not in launcher_links and 'libpython' not in launcher_links, 'Reader code loaded before StoreKit'
    print('PASS: native StoreKit verification library does not link Qt/Python')
files = list(app.rglob('*'))
for path in files:
    relative = path.relative_to(app).as_posix().lower()
    if (path.is_file() or path.is_symlink()) and 'licenses/' not in relative:
        assert not any(term in relative for term in ('webengine', 'pyqt6', 'pymupdf', '/fitz/', 'qtcharts', 'qtgraphs', 'qtvirtualkeyboard', 'qtquick3d')), relative
qt = app/'Contents/Frameworks/PySide6/Qt'
assert (qt/'plugins/webview/libqtwebview_darwin.dylib').is_file(), 'Missing native WebKit plugin'
runtime = app/'Contents/Resources/licenses/BUILD_RUNTIME.json'
if runtime.is_file() and json.loads(runtime.read_text()).get('qt_appstore_compliant_build_verified'):
    marker_sets = {
        qt/'plugins/platforms/libqcocoa.dylib':
            (b'_helpCursor', b'busyButClickableCursor', b'_windowResizeNorthWestSouthEastCursor'),
        qt/'lib/QtCore.framework/Versions/A/QtCore':
            (b'__ulock_wait', b'__ulock_wake', b'csr_check', b'responsibility_get_pid_responsible_for_pid'),
    }
    for path, markers in marker_sets.items():
        assert path.is_file(), 'Missing App Store Qt runtime: ' + path.name
        data = path.read_bytes()
        assert not any(marker in data for marker in markers), 'Known private Apple API in ' + path.name
    print('PASS: compiled App Store Qt; known private Apple API markers absent in actual bundle')
frameworks = app/'Contents/Frameworks'
declared_minimum = str(plistlib.loads((app/'Contents/Info.plist').read_bytes()).get('LSMinimumSystemVersion') or '')
def version(value):
    parts = tuple(map(int, value.split('.')))
    return parts + (0,) * (3 - len(parts))
assert declared_minimum, 'Missing minimum macOS version'
native_count = 0
for path in files:
    if not path.is_file() or path.is_symlink():
        continue
    with path.open('rb') as stream:
        magic = stream.read(4)
    if magic not in (b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xca\xfe\xba\xbe'):
        continue
    native_count += 1
    commands = subprocess.check_output(['/usr/bin/otool', '-l', str(path)], text=True)
    minimums = re.findall(r'\bminos\s+([0-9.]+)', commands) or re.findall(
        r'LC_VERSION_MIN_MACOSX.*?\bversion\s+([0-9.]+)', commands, flags=re.S)
    assert minimums, 'Cannot determine native deployment target: ' + str(path.relative_to(app))
    assert all(version(value) <= version(declared_minimum) for value in minimums), (
        'Native deployment target exceeds application minimum', path.relative_to(app), minimums, declared_minimum)
print(f'PASS: {native_count} native files fit declared minimum macOS {declared_minimum}')
python_runtime = list(frameworks.glob('libpython3.*.dylib'))
python_runtime += [frameworks/'Python']
python_runtime += list(frameworks.glob('Python.framework/Versions/*/Python'))
assert any(path.is_file() for path in python_runtime), 'Missing bundled Python'
for path in list((qt/'lib').glob('*.framework/Versions/A/Qt*')) + list((qt/'plugins').rglob('*.dylib')):
    if not path.is_file(): continue
    output = subprocess.check_output(['otool', '-L', str(path)], text=True)
    for framework in re.findall(r'@rpath/(Qt\w+)\.framework', output):
        assert (qt/'lib'/f'{framework}.framework').is_dir(), (path.name, framework)
    assert '/Users/' not in '\n'.join(output.splitlines()[1:]), (path, 'external user library')
print('PASS: native WebKit only; bundled Python; no forbidden Qt/PDF bindings; Qt dependencies resolved')
