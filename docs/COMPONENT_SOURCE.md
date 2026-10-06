# Corresponding component source

The GitHub release provides the free application binary, the complete reviewed
WhaleRead application source, and `WhaleRead-1.19.0-Qt-source-kit.zip`.
`SHA256SUMS.txt` identifies each release asset.

The component kit contains the exact official Qt 6.11.0 source archives for
QtBase, QtDeclarative, QtShaderTools, QtSvg, QtWebView and QtImageFormats, plus
PySide/Shiboken 6.11.0 source. `SOURCE_MANIFEST.json` records original URLs and
pinned hashes. It also contains the UTF-8 PRL patch and the build/adoption,
signing and packaging material used for the published runtime. Qt changes are
applied by `scripts/build_qt_appstore.py`; complete application builds require
the application checkout and `docs/OPEN_SOURCE_BUILD.md`.

Qt/PySide components are dynamically linked. You may study, modify and rebuild
them, replace ABI-compatible frameworks/plugins, and run the relinked app after
locally re-signing it. There is no App Store receipt restriction in the GitHub
edition and no Apple developer membership is required for local ad-hoc signing.
The original binaries can be kept as a rollback copy. No developer private
signing key is needed or supplied.

The application includes its GPL-3.0-only license under
`Contents/Resources/licenses/WhaleRead/LICENSE`, Qt LGPL/GPL texts, upstream
attributions and dependency notices. Third-party components retain their own
licenses. Model weights are downloaded separately from their publishers and
are not part of the application or this component source kit.

Support and source questions can be submitted through the repository's Issues.
Do not attach private books, account tokens or personal logs.
