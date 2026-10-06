"""Bundled GUI and allowlisted worker entry point; no external Python needed."""
import os
import runpy
import sys
from pathlib import Path

root = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root), str(root / 'runtime/lib'), str(root / 'runtime/tools')]
os.environ.setdefault('QT_QUICK_CONTROLS_STYLE', 'Basic')
os.environ.setdefault('QSG_RENDER_LOOP', 'basic')
os.environ['QT_WEBVIEW_PLUGIN'] = 'native'

if getattr(sys, 'frozen', False):
    import ctypes
    import json
    import hashlib
    import plistlib
    contents = Path(sys.executable).resolve().parents[1]
    info = plistlib.loads((contents / 'Info.plist').read_bytes())
    if info.get('WhaleReadReceiptValidation') == 'standalone':
        from distribution_policy import validate_standalone
        try:
            manifest = validate_standalone(info, json.loads(
                (contents / 'Resources/DISTRIBUTION.json').read_text()))
            license_path = contents / 'Resources/licenses/WhaleRead/LICENSE'
            if hashlib.sha256(license_path.read_bytes()).hexdigest() != manifest['license_sha256']:
                raise ValueError('Application license is missing or changed')
        except (OSError, ValueError, TypeError, KeyError):
            raise SystemExit('Standalone distribution metadata is unavailable') from None
        if sys.argv[1:] == ['--distribution-self-test']:
            if sys.stdout is None: sys.stdout = os.fdopen(os.dup(1), 'w', buffering=1)
            print(json.dumps(dict(mode='standalone', channel='github',
                metadata_verified=True, bundle_identity_matches=True,
                license=manifest['license'], purchase_required=False)), flush=True)
            raise SystemExit(0)
    else:
        library_path = Path(sys.executable).resolve().parents[1] / 'Frameworks/WhaleReadPurchase.dylib'
        try:
            purchase = ctypes.CDLL(str(library_path))
            purchase.WhaleReadVerifyPurchase.argtypes = []
            purchase.WhaleReadVerifyPurchase.restype = ctypes.c_int32
            purchase.WhaleReadPurchasePolicyStatus.argtypes = []
            purchase.WhaleReadPurchasePolicyStatus.restype = ctypes.c_int32
            if sys.argv[1:] == ['--receipt-policy-self-test']:
                if sys.stdout is None: sys.stdout = os.fdopen(os.dup(1), 'w', buffering=1)
                status = purchase.WhaleReadPurchasePolicyStatus()
                manifest = json.loads((library_path.parent.parent / 'Resources/STORE_LAUNCHER.json').read_text())
                print(json.dumps(dict(policy_tests_passed=bool(status & 1), cases=5,
                    bundle_identity_matches=bool(status & 2), mode=manifest['mode'],
                    real_purchase_verified=False)), flush=True)
                raise SystemExit(0 if status == 3 else 1)
            if purchase.WhaleReadVerifyPurchase() != 1:
                raise SystemExit(0)
        except (OSError, AttributeError, ValueError, KeyError):
            raise SystemExit('Native purchase verification is unavailable') from None

workers = {
    'translate_range.py', 'probe_model.py', 'post_edit.py', 'ask_ai_request.py',
    'research_worker.py', 'bundle_ui_probe.py', 'bundle_api_probe.py',
}
if len(sys.argv) > 2 and sys.argv[1] == '-B':
    if sys.stdin is None: sys.stdin = os.fdopen(os.dup(0), 'r')
    if sys.stdout is None: sys.stdout = os.fdopen(os.dup(1), 'w', buffering=1)
    if sys.stderr is None: sys.stderr = os.fdopen(os.dup(2), 'w', buffering=1)
    script = Path(sys.argv[2]).resolve()
    if script.parent != (root / 'runtime/tools').resolve() or script.name not in workers:
        raise SystemExit('Unsupported worker')
    sys.argv = [str(script), *sys.argv[3:]]
    runpy.run_path(str(script), run_name='__main__')
else:
    import faulthandler
    from file_access import application_home
    logs = application_home() / 'Library/Logs/WhaleRead'
    logs.mkdir(parents=True, exist_ok=True)
    log = (logs / 'application.log').open('a', buffering=1)
    os.dup2(log.fileno(), 2)
    faulthandler.enable(log)
    if os.environ.get('WHALEREAD_TRACE_OPEN') == '1':
        def trace_open(event, args):
            if event == 'open':
                print('[open]', repr(args), file=sys.stderr, flush=True)
        sys.addaudithook(trace_open)
    from native_launcher import main
    raise SystemExit(main())
