#!/bin/zsh
# Self-contained candidate build for WhaleRead.
#
# Every tool is resolved from ONE selected interpreter, named by HY_BUILD_PYTHON.
# There is no fallback to a worktree .venv, no assumption about a developer
# environment, and no mutation of shared Qt/plugin flags.  A missing tool is a
# hard failure with a specific message.
#
# This script builds and gates a CANDIDATE only.  It never installs: installation,
# backup and real-window acceptance are Codex-owned.
set -eu

root_dir="${0:A:h:h}"
mode="${1:---build-only}"

if [[ "$mode" == '--install-built' ]]; then
  print -u2 'Task 06 builds a candidate only; installation is Codex-owned.'
  exit 2
fi
if [[ "$mode" != '--build-only' ]]; then
  print -u2 'Use --build-only'
  exit 2
fi

build_python="${HY_BUILD_PYTHON:-}"
if [[ -z "$build_python" ]]; then
  print -u2 'HY_BUILD_PYTHON is required: point it at the interpreter that owns PySide6.'
  print -u2 'Example: HY_BUILD_PYTHON=/path/to/venv/bin/python scripts/build_native_app.sh --build-only'
  exit 2
fi
if [[ ! -x "$build_python" ]]; then
  print -u2 "HY_BUILD_PYTHON is not executable: $build_python"
  exit 2
fi

resolver="$root_dir/scripts/resolve_build_tools.py"
clean_python() {
  /usr/bin/env -u PYTHONPATH -u PYTHONHOME -u QML2_IMPORT_PATH \
    PYTHONNOUSERSITE=1 "$build_python" "$@"
}
resolve() {
  clean_python -I -B "$resolver" "$@"
}

# All of these fail with a clear message if the selected interpreter is wrong.
# PySide6 is checked first because it is the dependency the runtime needs; the
# build tool is secondary.
resolve pyside6 >/dev/null
qt_root="$(resolve qt-root)"
resolve pyinstaller >/dev/null

# The application catalogue must exist; the release gate re-checks freshness.
catalogue="$root_dir/i18n/whaleread_en.qm"
if [[ ! -f "$catalogue" ]]; then
  print -u2 "Missing application catalogue: $catalogue (run scripts/i18n_compile.py)"
  exit 1
fi

# Qt's own catalogues come from the selected interpreter's Qt.  A miss is fatal:
# a bundle without them would silently fall back to English-only Qt strings.
for name in qtbase_zh_CN.qm qtbase_en.qm; do
  if [[ ! -f "$qt_root/translations/$name" ]]; then
    print -u2 "Missing Qt translation catalogue: $qt_root/translations/$name"
    exit 1
  fi
done

# Source-side release gates run before anything is built.
clean_python -I -B "$root_dir/scripts/check_release_bundle.py" --source-only
clean_python -B "$root_dir/scripts/i18n_check.py"

dist_dir="${HY_DIST_DIR:-$root_dir/dist/self-contained}"
work_dir="${HY_WORK_DIR:-$root_dir/build/self-contained}"
clean_python -I -m PyInstaller --noconfirm --distpath "$dist_dir" \
  --workpath "$work_dir" "$root_dir/packaging/WhaleRead.spec"

built_app="$dist_dir/鲸读.app"
if [[ ! -d "$built_app" ]]; then
  print -u2 "Build did not produce $built_app"
  exit 1
fi

# Gate the bundle: resources, inventory, version, no build-path leaks.
clean_python -I -B "$root_dir/scripts/build_store_launcher.py" "$built_app" \
  --mode "${WHALEREAD_RECEIPT_MODE:-standalone}"
clean_python -I -B "$root_dir/scripts/audit_native_bundle.py" "$built_app"
clean_python -I -B "$root_dir/scripts/check_release_bundle.py" "$built_app"

/usr/bin/chflags -R nohidden "$built_app" 2>/dev/null || true
sign_options=(--identity "${WHALEREAD_SIGN_IDENTITY:--}")
if [[ -n "${WHALEREAD_PROVISIONING_PROFILE:-}" ]]; then
  sign_options+=(--profile "$WHALEREAD_PROVISIONING_PROFILE")
fi
if [[ -n "${WHALEREAD_TEAM_ID:-}" ]]; then
  sign_options+=(--team-id "$WHALEREAD_TEAM_ID")
fi
clean_python -I -B "$root_dir/scripts/sign_store_candidate.py" "$built_app" "${sign_options[@]}"
clean_python -I -B "$root_dir/scripts/qa_bundle_only.py" "$built_app"
/usr/bin/codesign --verify --deep --strict "$built_app"
clean_python -I -B "$root_dir/scripts/hash_app_bundle.py" "$built_app"
print -r -- "$built_app"
