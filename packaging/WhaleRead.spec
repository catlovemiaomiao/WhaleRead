# Build a self-contained macOS application using only the native WebKit backend.
from pathlib import Path
import importlib.util
import importlib.metadata
import os
import sys
import json
from PyInstaller.config import CONF
root = Path(SPECPATH).parent
qt = Path(importlib.util.find_spec('PySide6').origin).parent / 'Qt'
qt_manifest = qt / 'WHALEREAD_QT_BUILD.json'
qt_build = json.loads(qt_manifest.read_text()) if qt_manifest.is_file() else {}
qt_verified = bool(qt_build.get('appstore_compliant_feature') and
                   qt_build.get('known_private_api_markers_absent') and
                   qt_build.get('known_runtime_diagnostic_paths_absent') and
                   qt_build.get('network_public_abi_preserved') and qt_build.get('qt_version') == '6.11.0')
if os.environ.get('WHALEREAD_REQUIRE_STORE_QT') == '1' and not qt_verified:
    raise SystemExit('An attested isolated App Store Qt build is required.')
if qt_verified:
    import hashlib
    for relative, expected in qt_build['binary_sha256'].items():
        path = qt / relative
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise SystemExit('The attested Qt runtime has changed: ' + relative)
license_data = []
if qt_verified:
    license_data.append((str(qt_manifest), 'licenses/Qt'))
for distribution in ('pypdfium2', 'pypdf', 'Pillow', 'PyYAML', 'lxml', 'pyinstaller', 'packaging', 'certifi',
                     'PySide6', 'PySide6_Essentials', 'PySide6_Addons', 'shiboken6'):
    dist = importlib.metadata.distribution(distribution)
    for file in dist.files or []:
        if any(part.lower() in {'license', 'licenses', 'copying', 'notice'} or
               part.lower().startswith(('license.', 'copying.', 'notice.')) for part in file.parts):
            located = Path(dist.locate_file(file))
            if located.is_file():
                license_data.append((str(located), 'licenses/' + distribution + '/' + str(file.parent)))
python_license = Path(sys.base_prefix) / 'lib/python3.13/LICENSE.txt'
if not python_license.is_file():
    raise SystemExit('The selected runtime must include its Python license.')
license_data.append((str(python_license), 'licenses/Python'))
runtime_manifest = Path(CONF['workpath']) / 'BUILD_RUNTIME.json'
runtime_manifest.parent.mkdir(parents=True, exist_ok=True)
runtime_manifest.write_text(json.dumps(dict(python=sys.version.split()[0],
    components={name: importlib.metadata.version(name) for name in
        ('PySide6', 'shiboken6', 'pypdfium2', 'pypdf', 'Pillow', 'PyYAML', 'lxml', 'PyInstaller', 'certifi')},
    qt_appstore_compliant_build_verified=qt_verified), indent=2) + '\n', encoding='utf-8')
license_data.append((str(runtime_manifest), 'licenses'))
# Qt's own catalogues for the standard dialogs the app installs.  They are
# resolved from the selected interpreter's Qt, never from the source tree, and a
# missing file fails the build instead of shipping a bundle that silently falls
# back to English-only Qt strings.
qt_translations = qt / 'translations'
required_qt_translations = ('qtbase_zh_CN.qm', 'qtbase_en.qm')
missing_qt_translations = [name for name in required_qt_translations
                           if not (qt_translations / name).is_file()]
if missing_qt_translations:
    raise SystemExit(
        'Missing Qt translation catalogues in ' + str(qt_translations) + ': '
        + ', '.join(missing_qt_translations)
        + '. Install the matching PySide6 wheel or fix the selected interpreter.')
qt_translation_data = [(str(qt_translations / name), 'PySide6/Qt/translations')
                       for name in required_qt_translations]

# The application's own reviewed catalogue must exist before packaging; the
# release gate re-checks freshness against the current .ts.
app_catalogue = root / 'i18n' / 'whaleread_en.qm'
if not app_catalogue.is_file():
    raise SystemExit('Missing application catalogue: ' + str(app_catalogue)
                     + '. Run scripts/i18n_compile.py first.')

# Only workers reachable from the frozen entry point are shipped as data.
# Legacy TypeScript wrappers contain developer-machine interpreter paths and are
# intentionally excluded from the standalone application.
worker_names = (
    'ask_ai_request.py', 'bundle_ui_probe.py', 'bundle_api_probe.py', 'post_edit.py', 'probe_model.py',
    'research_ocr.py', 'research_worker.py', 'translate_range.py', 'worker_entry.py',
)
missing_workers = [name for name in worker_names
                   if not (root / 'runtime/tools' / name).is_file()]
if missing_workers:
    raise SystemExit('Missing packaged workers: ' + ', '.join(missing_workers))
worker_data = [(str(root / 'runtime/tools' / name), 'runtime/tools')
               for name in worker_names]

hidden = [p.stem for p in (root/'runtime/lib').glob('*.py')]
hidden += ['translate_range', 'post_edit', 'probe_model', 'ask_ai_request', 'research_worker',
           'PySide6.QtWebView', 'PySide6.QtQuick', 'lxml.etree', 'pypdfium2']
a = Analysis([str(root/'packaging/app_entry.py'), str(root/'runtime/tools/worker_entry.py')],
    pathex=[str(root), str(root/'runtime/lib'), str(root/'runtime/tools')],
    binaries=[(str(qt/'plugins/webview/libqtwebview_darwin.dylib'), 'PySide6/Qt/plugins/webview')],
    datas=[(str(root/'ui'), 'ui'), (str(root/'assets'), 'assets'),
                        (str(root/'LICENSE'), 'licenses/WhaleRead'),
                        (str(root/'packaging/PrivacyInfo.xcprivacy'), '.'),
                        (str(root/'docs/PRIVACY.md'), 'docs'),
                        (str(root/'packaging/licenses'), 'licenses'),
                        (str(root/'i18n'), 'i18n'),
                        (str(root/'runtime/lib'), 'runtime/lib')] + worker_data + qt_translation_data + license_data,
    hiddenimports=hidden, hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=['PyQt6', 'PyQt5', 'pymupdf', 'fitz', 'PySide6.QtWebEngineCore',
              'PySide6.QtWebEngineQuick', 'PySide6.QtWebEngineWidgets', 'matplotlib',
              'tkinter', 'IPython', 'notebook', 'scipy', 'pandas', 'torch', 'tensorflow'],
    noarchive=False, optimize=1)
# QML's broad collection includes unused GPL-only modules. Keep just the
# components this reader imports and their LGPL-capable dynamic dependencies.
frameworks = {'QtCore', 'QtGui', 'QtWidgets', 'QtNetwork', 'QtDBus', 'QtOpenGL',
              'QtQml', 'QtQmlCore', 'QtQmlMeta', 'QtQmlModels', 'QtQmlNetwork',
              'QtQmlWorkerScript', 'QtQuick', 'QtQuickLayouts', 'QtQuickTemplates2',
              'QtQuickEffects', 'QtQuickShapes', 'QtWebView', 'QtWebViewQuick', 'QtSvg', 'QtShaderTools'}
quick_modules = {'Controls', 'Layouts', 'Templates', 'Window', 'NativeStyle', 'Effects', 'Shapes'}
def wanted(entry):
    if entry[2] == 'SYMLINK' and not wanted((entry[1], '', 'TARGET')): return False
    path = entry[0].replace('\\', '/')
    name = path.lower()
    if any(part in name for part in ['webengine', 'pymupdf', '/fitz/', 'pyqt6', '__pycache__']): return False
    if 'PySide6/Qt/lib/' in path:
        framework = path.split('PySide6/Qt/lib/', 1)[1].split('.')[0]
        return framework in frameworks or framework.startswith('QtQuickControls2')
    if 'PySide6/Qt/qml/' in path:
        parts = path.split('PySide6/Qt/qml/', 1)[1].split('/')
        if parts[0] not in {'QtQml', 'QtQuick', 'QtWebView'}: return False
        if parts[0] == 'QtQuick' and len(parts) > 2 and parts[1] not in quick_modules: return False
        if parts[0] == 'QtQml' and len(parts) > 2 and parts[1] not in {'Models', 'WorkerScript'}: return False
    if 'PySide6/Qt/plugins/' in path:
        category = path.split('PySide6/Qt/plugins/', 1)[1].split('/')[0]
        if category not in {'platforms', 'imageformats', 'iconengines', 'networkinformation', 'tls', 'webview', 'styles'}: return False
    return True
a.binaries = [item for item in a.binaries if wanted(item)]
a.datas = [item for item in a.datas if wanted(item)]
pyz = PYZ(a.pure)
main_scripts = [item for item in a.scripts if item[0] != 'worker_entry']
helper_scripts = [item for item in a.scripts if item[0] != 'app_entry']
assert any(item[0] == 'app_entry' for item in main_scripts)
assert any(item[0] == 'worker_entry' for item in helper_scripts)
exe = EXE(pyz, main_scripts, [], exclude_binaries=True, name='鲸读', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          argv_emulation=False, target_arch='arm64', codesign_identity=None, entitlements_file=None)
helper = EXE(pyz, helper_scripts, [], exclude_binaries=True, name='WhaleReadWorker', debug=False,
             bootloader_ignore_signals=False, strip=False, upx=False, console=True,
             argv_emulation=False, target_arch='arm64', codesign_identity=None, entitlements_file=None)
coll = COLLECT(exe, helper, a.binaries, a.datas, strip=False, upx=False, name='WhaleRead-runtime')
# COLLECT normalizes entries alphabetically; BUNDLE must select the GUI.
coll.toc = sorted(coll.toc, key=lambda item: item[0] != '鲸读')
coll.console = exe.console
app = BUNDLE(coll, name='鲸读.app', icon=str(root/'assets/HyTranslator.icns'),
    bundle_identifier=os.environ.get('WHALEREAD_BUNDLE_ID', 'local.sindy.jingdu'),
    info_plist={'CFBundleName':'鲸读','CFBundleDisplayName':'鲸读',
                'CFBundleExecutable':'鲸读', 'LSBackgroundOnly':False,
                'CFBundleShortVersionString':'1.19.1','CFBundleVersion':'68',
                'LSApplicationCategoryType':'public.app-category.reference',
                # Declared only because the bundle really carries
                # whaleread_en.qm plus the Qt catalogues above.
                'CFBundleDevelopmentRegion':'en',
                'CFBundleLocalizations':['zh_CN', 'en'],
                'LSMinimumSystemVersion':'15.0', 'NSHighResolutionCapable':True,
                'LSMultipleInstancesProhibited':True,
                'NSHumanReadableCopyright':'WhaleRead · Third-party notices included in Resources.'})
