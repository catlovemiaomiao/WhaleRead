# WhaleRead 1.19.0 candidate: build and submission

**Historical App Store route.** On 2026-10-06 the publisher chose free GPL GitHub distribution. Use [the standalone build instructions](OPEN_SOURCE_BUILD.md) and [component source information](COMPONENT_SOURCE.md). The StoreKit and account requirements below apply only to an explicitly selected Store build.

The engineering branch is `release/whaleread-store-20261001`, based on public
1.18.1 at `df7426ecbf66586f5add00eec33048ad96a1c2f3`. The installed preview and
its data are retained. Building does not install, upload, accept store terms or
purchase a license. See [the current evidence](../STORE_READINESS.md).

## Isolated build

Use an Apple Silicon Mac with Xcode command line tools, CMake and Ninja. The
verified build uses Python 3.13.3, Qt/PySide6 6.11.0, Xcode 16.2 / SDK 15.2,
arm64. Qt itself targets macOS 13, but the current Python/PySide native libraries
require macOS 15; the app honestly declares 15.0 and audits every shipped native
file against it. Supporting 13/14 requires compatible runtime builds and fresh
acceptance. Apple requirements must be checked again
when submitting. Every Python build tool must come from the selected environment.

Create a fresh virtual environment under this checkout's ignored `build/`
directory. Install `packaging/requirements-macos.txt` there. Do not edit a shared
PySide installation. Create `.whaleread-isolated-build` inside that environment
only after verifying it is disposable. Keep the Python license in its base
installation. The bundled component versions are recorded in `BUILD_RUNTIME.json`.

Build Qt from official, hash-pinned archives. This takes time and several GB:

```sh
build/store-venv/bin/python -B scripts/build_qt_appstore.py \
  --source-dir build/qt-sources --build-dir build/qt-appstore-ninja \
  --prefix /private/tmp/WhaleRead-Qt-6.11.0-appstore \
  --openssl-root /path/to/openssl-3 --jobs 3
```

Add Ninja to PATH if it is installed inside the virtual environment. The script
enables `appstore-compliant` and retains DTLS/OCSP public ABI for the PySide
wheel. `--fresh-configure` clears incompatible Qt base CMake feature caches.
The UTF-8 PRL patch is checked against the original archive before applying it.
Compiler prefix mappings remove personal checkout paths from diagnostic strings.
For an existing cache the script recompiles only affected objects and relinks,
using its Ninja compiler database; it never edits compiled binaries in place.
System paths are build inputs; they must not become runtime dependencies.

Adopt that verified prefix into a separate, marked build environment:

```sh
build/store-appstore-venv/bin/python -B scripts/adopt_qt_appstore.py \
  /private/tmp/WhaleRead-Qt-6.11.0-appstore
```

The original Qt tree is retained for rollback. `--refresh` explicitly retains
another backup when adopting a revised verified build. Verify imports of
`PySide6.QtNetwork`, `QtQuick`, `QtQml` and `QtWebView` before packaging.
The source notices are collected with `scripts/collect_qt_notices.py`.

Select a **new QA Bundle ID**, separate output/work directories and the adopted
Python. Do not reuse a production identifier for test data:

```sh
HY_BUILD_PYTHON="$PWD/build/store-appstore-venv/bin/python" \
HY_DIST_DIR=/private/tmp/WhaleRead-QA \
HY_WORK_DIR="$PWD/build/frozen-store-qa" \
WHALEREAD_BUNDLE_ID=local.sindy.jingdu.storeqa.example \
WHALEREAD_RECEIPT_MODE=qa \
WHALEREAD_REQUIRE_STORE_QT=1 \
scripts/build_native_app.sh --build-only
```

The build checks source resources, source/bundle inventories, bundled CA roots,
forbidden modules, personal build paths, entitlements and real packaged QML
controls. Ad-hoc signing is appropriate for isolated local QA, **not submission**.
The main entitlement enables Sandbox, network client, user-selected files,
application bookmarks and JIT. The visible reader remains the main bundle
executable; the background helper enables Sandbox + inherit only.

The main app stores persistent app-scoped file bookmarks and acquires their
URLs before a job starts. It creates ephemeral implicit-scope bookmarks from
those acquired URLs for each helper launch, sends them only over stdin, and
keeps the parent leases alive. The helper resolves those transferable bookmarks
without app scope, explicitly starts access and closes it after the worker.
Passing the stored app-scoped bookmark directly fails under the helper's
different identity; merely inheriting static entitlements does not confer a
Powerbox grant acquired after launch. See [Apple's file-access documentation](https://developer.apple.com/documentation/security/accessing-files-from-the-macos-app-sandbox).

The native Swift library verifies `AppTransaction.shared` using public StoreKit
from the application entry, before constructing the Qt application. Only a verified transaction for the compiled Bundle ID
can start the paid Store reader. A failed check offers Retry or Quit; Retry alone
requests `AppTransaction.refresh()`. It does not delete user data or log Apple
account/transaction payloads. QA bypass is compiled separately and restricted to
the local QA identity above; no runtime variable enables it in a Store build.
Python and its packaged startup hooks run before the native gate; this does not
grant access to the reader or AI workers. Keeping the main executable and its
signed Info dictionary avoids losing the Powerbox file-selection entitlement.
The build runs negative identity/verification policy checks. These are not an
actual App Store purchase, missing-receipt window or TestFlight acceptance.
`WHALEREAD_RECEIPT_MODE` defaults to `store` for the production rebuild. An ad-hoc
Store build runs policy checks and explicitly reports UI acceptance pending. The
real StoreKit success/refresh/offline/error paths require the registered account,
valid signatures and a TestFlight/App Store build.

For offscreen source GUI checks, use `shutil.copyfile` to create a fresh
`libqoffscreen.dylib` in a temporary `platforms/` directory. Do not use `copy2`:
it preserves macOS `UF_HIDDEN`, which prevents Qt from discovering the plugin
in this environment and aborts before tests start. Set `QT_PLUGIN_PATH` to the
temporary parent, `QT_QPA_PLATFORM_PLUGIN_PATH` to its `platforms/` directory
and `QT_QPA_PLATFORM=offscreen` in a clean test environment. Do not change the
original plugin flags or a shared environment.

## Component replacement and source material

Qt and PySide are dynamically linked. ABI-compatible rebuilt Qt frameworks and
plugins can replace the trees under `Contents/Frameworks/PySide6/Qt` (and their
Resources symlinks). A modified local bundle needs fresh signatures. For local
testing use the explicit nested signing script and rerun the bundle gates.
Modifications needed to debug replacement LGPL components are permitted.

`scripts/prepare_qt_source_kit.py` creates a local zip containing the exact six Qt
archives, PySide/Shiboken source, archive checksums, build patch and scripts. It
does not include keys, books or the developer's virtual environment. PySide's
upstream build guide is [its getting started documentation](https://doc.qt.io/qtforpython-6/gettingstarted.html).
The current runtime uses unchanged official PySide wheels against rebuilt Qt;
building replacement bindings also requires the matching Python and upstream
Clang/build dependencies. The kit is for replacement components. Application
release gates and packaging commands run from the complete WhaleRead checkout.

Source availability, dynamic replacement and notices are technical preparation.
The Qt/PySide licensing choice still requires review against the intended store
distribution terms. A commercial license must be verified for the components
and distribution it covers. This document is not a legal approval.

## Concrete submission dependencies

Before generating an upload candidate, resolve Apple Developer team,
registered Bundle ID, app record, version/pricing/regions and applicable Qt
license. Install the Apple-authorized distribution identities on the host;
never put private keys or signing credentials in this repository. The current
host has no valid code signing identity. A local technical Store-mode candidate
may use the provisional `local.sindy.jingdu.storecandidate` identity for build
and policy checks. That identity is unregistered, its signature is ad-hoc, and
the resulting app is not ready for upload or real purchase validation. It is
separate from the installed preview and all QA containers.

The user's confirmed plan is a paid app under an individual account. Enrollment
does not require a company; the legal name is displayed as seller. Complete the
Paid Apps Agreement, tax and bank information in App Store Connect. The owner
performs enrollment and acceptance. Current Apple SDK requirements must be
rechecked for macOS specifically: the April 2026 notice lists iOS/iPadOS/tvOS/
visionOS/watchOS 26 and does not itself establish a macOS 26 requirement. The
local Xcode 16.2 build has not been accepted by App Store Connect. Apple's
[upload documentation](https://developer.apple.com/help/app-store-connect/manage-builds/upload-builds/)
currently lists macOS builds separately and says uploads require Xcode 14 or
later starting in 2026. There is no verified reason to apply the iOS 26 SDK
requirement to this Mac app.

Use the final approved production ID and certificate identity to rebuild. The
existing technical app contains a library compiled for its provisional ID;
editing only Info.plist is not an identity migration. Team identifiers,
provisioning and certificate-specific Keychain behavior need real verification.

The build accepts `WHALEREAD_SIGN_IDENTITY` (Apple Distribution),
`WHALEREAD_TEAM_ID`, and `WHALEREAD_PROVISIONING_PROFILE` in addition to the final
`WHALEREAD_BUNDLE_ID`. `sign_store_candidate.py` checks profile expiry, macOS,
exact app ID, team and authorized signing certificate before embedding it. It
rejects development/device-limited profiles. Only the app/team identity and,
when present, `beta-reports-active` are added to the reviewed main entitlements;
wildcard keychain groups and unrelated capabilities are not copied. The helper
retains Sandbox + inherit. Profiles and private-key archives are Git-ignored.

For TestFlight, use a Mac App Store distribution profile with beta reporting.
Apple requires a profile for TestFlight even when a Mac app otherwise needs no
restricted entitlements. A direct App Store submission of this app can omit the
profile if its signed entitlements remain unrestricted. See
[TN3125](https://developer.apple.com/documentation/technotes/tn3125-inside-code-signing-provisioning-profiles).

After rebuilding and completing real-window acceptance under that identity,
package the signed app. Set the following shell variables to the actual
registered identity and reviewed candidate paths; the script never registers an
app, signs the input, installs it or uploads it:

```sh
build/store-appstore-venv/bin/python -I -B scripts/package_store_candidate.py \
  "$CANDIDATE_APP" --bundle-id "$REGISTERED_BUNDLE_ID" \
  --team-id "$APPLE_TEAM_ID" --installer-identity "$MAC_INSTALLER_IDENTITY" \
  --output artifacts/WhaleRead-1.19.0-store.pkg \
  --report artifacts/WhaleRead-1.19.0-store-package.json
```

The default channel is `testflight`; select `--channel app-store` only for a
direct submission. `--preflight-only` performs local checks without creating a
package. The tool rejects QA/provisional identities, verifies the Apple signing
anchor and matching team, checks the app/helper entitlements, and selects a
valid **Mac Installer Distribution** identity. Inspect installer identities with
`security find-identity -v`; `-p codesigning` hides them. It uses `productbuild`
with one component installed to `/Applications`, macOS 15 and arm64 requirements,
checks the package signature, expands it without installing, and compares every
payload file/executable bit/symlink with the input app. Existing outputs are
never overwritten. This follows [Apple's packaging workflow](https://developer.apple.com/documentation/xcode/packaging-mac-software-for-distribution).

An explicit `--qa-unsigned` mode only accepts the separate QA identity and a
filename ending in `.qa-unsigned.pkg`. It exercises the same packaging/payload
path without a signing account; it cannot produce a signed Store candidate.
Local checks and decoded profile fields do not replace Apple's validation.
Use Xcode/Transporter to validate the signed package and inspect server
diagnostics before upload. Real distribution identity, purchase and TestFlight
acceptance remain unverified until the account and certificates are available.

Complete native cold launch, file selection, reading, background translation,
export and reopen with fictional data; then collect screenshots of that exact
candidate. Cloud testing uses tiny fictional excerpts and private input pipes.
Do not give the user's DSH/ETF keys to App Review. Prepare a separate review
service/test credential, publish the [privacy page](PRIVACY.md), finish App Store
privacy/age/encryption declarations and review [metadata](APP_STORE_METADATA.md).

## Rollback

The original application and public branch remain untouched. Keep candidate
hashes and evidence paired. Profile IDs and old task routes are retained when
creating a changed route; a new model cannot silently resume an old edition.
Preview preferences are explicitly imported with a verified settings backup;
bookmarks and notes merge by stable IDs and current edits take precedence.
Remove only the named QA artifacts/credentials after testing. Restoring the
old installation never means overwriting new reader data with old preferences.
