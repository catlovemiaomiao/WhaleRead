# Build the GPL GitHub edition

WhaleRead 1.19.0 / build 67 is a free, standalone macOS application. It does not
require an Apple account, App Store purchase, subscription or donation.
The application source is licensed under GPL-3.0-only; third-party components
retain their own notices and license terms.

## Build on an Apple Silicon Mac

The current binary targets macOS 15 or later. Use Python 3.13, Xcode command line
tools and an isolated virtual environment. The Python installation must include
its `lib/python3.13/LICENSE.txt`. Do not change a shared Python or Qt installation.

```sh
python3.13 -m venv build/venv
build/venv/bin/python -m pip install -r packaging/requirements-macos.txt
build/venv/bin/python scripts/i18n_compile.py
HY_BUILD_PYTHON="$PWD/build/venv/bin/python" \
HY_DIST_DIR="$PWD/dist/github" \
HY_WORK_DIR="$PWD/build/frozen-github" \
WHALEREAD_RECEIPT_MODE=standalone \
scripts/build_native_app.sh --build-only
```

The build produces `dist/github/鲸读.app` and does not replace an installed app.
It packages Python, native WebKit, application and Qt translations, component
licenses, and a sandboxed background helper. No API key, book, OCR weight or
server account belongs in the application.

The published binary uses Qt/PySide 6.11.0 against a modified, dynamically linked
Qt build. Its release includes the matching Qt/PySide component source kit,
checksums and rebuild scripts. See `docs/COMPONENT_SOURCE.md`. The kit's exact
archives and UTF-8 PRL patch are pinned by `scripts/build_qt_appstore.py`.
The complete application checkout is required for application release checks.

You may modify and rebuild the application and its LGPL/GPL components.
Replace ABI-compatible frameworks/plugins in `Contents/Frameworks/PySide6/Qt`,
then sign your local copy with `scripts/sign_store_candidate.py` and rerun the
bundle checks. Local ad-hoc signing does not require a paid Apple Developer
membership. Rebuilt binaries need their own validation; do not reuse another
binary's checksum or QA evidence.

## Runtime models

Install Ollama separately for the HY-MT2 1.8B Q8 or 7B Q4 presets. Model weights
and third-party API usage are not included. Translation, review and Ask AI can
select different compatible Chat Completions services. Custom profiles require
separate permission for each feature and save credentials in macOS Keychain.

Research PDF translation uses the separate service under `deployment/ocr_service`.
Deploy it on a machine you control and connect privately. Batch translation can
skip repeated page approval, pause and resume; drafts still need human review.
Do not publish service credentials or private SSH configuration.
