# WhaleRead 1.19.0 candidate — third-party components

This application uses Python 3.13 (PSF), Qt/PySide6/Shiboken 6.11.0
(LGPL v3 option), the system-provided Apple WebKit, pypdfium2 5.13.0
(Apache 2.0 / BSD 3-clause) and its PDFium binary and dependencies,
Pillow (HPND), PyYAML (MIT), lxml (BSD with its own dependency notices),
and pypdf (BSD 3-clause). certifi supplies Mozilla CA roots under MPL 2.0.
OpenSSL 3.5.0 is provided under Apache 2.0; its notice is included in OpenSSL/.
PyInstaller has a bootloader distribution exception.
Bundled license files retain the individual authors' copyright notices.

Qt is dynamically linked. The App Store candidate is compiled from the pinned
official sources with `-feature-appstore-compliant`. The included
Qt/QtFinishPrlFile-utf8.patch changes upstream CMake metadata readers to preserve
UTF-8 build paths; it does not change runtime APIs. Its frameworks and plugins are under
Contents/Frameworks/PySide6/Qt. Replacing these with ABI-compatible builds is
permitted for LGPL compliance, including reverse engineering to debug such
modifications. A changed local bundle may need to be re-signed for macOS.
The project packaging/WhaleRead.spec records the dynamic component list.

Corresponding upstream sources:

- https://download.qt.io/archive/qt/6.11/6.11.0/submodules/
- https://code.qt.io/pyside/pyside-setup.git/tag/?h=v6.11.0
- https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.0-src/
- https://www.python.org/downloads/release/python-3133/
- https://github.com/pypdfium2-team/pypdfium2/tree/5.13.0

Neither Qt WebEngine, PyQt, PyMuPDF, Qt Charts, Qt Graphs, Qt Virtual Keyboard
nor Qt Quick 3D is intentionally distributed in this build. The build audit
rejects these components. System WebKit does not execute publisher scripts:
the app strips them from imported chapters and adds its own reading bridge.

This candidate is not an App Store release. Before any
public distribution, finish the complete transitive notice/source/relinking
audit and obtain distribution-specific licensing review. The presence of
LGPL-capable bindings alone is not a certification of App Store compliance.
