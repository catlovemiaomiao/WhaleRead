"""Frozen request context over QProcess stdin, never credentials in argv/env."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import runpy
import sys
from provider_transport import install_worker_credentials

WORKERS = {'translate_range.py', 'probe_model.py', 'post_edit.py', 'ask_ai_request.py',
           'research_worker.py', 'bundle_ui_probe.py'}
MAX_CONTEXT_BYTES = 2 * 1024 * 1024


def execute_worker(root, worker, arguments):
    if worker not in WORKERS:
        raise SystemExit('Unsupported worker')
    raw = sys.stdin.buffer.read(MAX_CONTEXT_BYTES + 1)
    if len(raw) > MAX_CONTEXT_BYTES:
        raise SystemExit('Worker context too large')
    context = json.loads(raw)
    install_worker_credentials(context.get('credentials', {}))
    from file_access import FileAccessError, worker_leases
    try:
        leases = worker_leases(context.get('grants', []))
    except FileAccessError:
        # Bootstrap failures happen before the engine can emit its events.
        # Return a stable, redacted error instead of disappearing at 0%.
        print(json.dumps(dict(type='error', message_code='fileAccessRequired',
                              detail='Worker file authorization is unavailable')), flush=True)
        raise SystemExit(1) from None
    try:
        sys.stdin = io.StringIO(str(context.get('input', '')))
        script = Path(root) / 'runtime/tools' / worker
        sys.argv = [str(script), *arguments]
        runpy.run_path(str(script), run_name='__main__')
    finally:
        for lease in leases:
            lease.close()


def prepare_worker(process, owner, worker, arguments, *, route=None, input_text='', feature=None,
                   additional_routes=()):
    if worker not in WORKERS:
        raise ValueError('Unsupported worker')
    root = Path(owner.root)
    credentials = {}
    routes = ([(feature, route)] if route is not None else []) + list(additional_routes)
    for selected_feature, selected_route in routes:
        if selected_feature is not None:
            owner.providers.require_consent(selected_feature, selected_route['id'])
        credentials[str(selected_route['auth_provider'])] = owner.providers.key(selected_route['id'])
    access = getattr(owner, 'file_access', None)
    body = json.dumps(dict(credentials=credentials, input=input_text,
                           grants=access.worker_grants() if access else []), ensure_ascii=False).encode('utf-8')
    if len(body) > MAX_CONTEXT_BYTES:
        raise ValueError('Worker context too large')
    if getattr(sys, 'frozen', False):
        helper = Path(sys.executable).parent / 'WhaleReadWorker'
        if not helper.is_file():
            raise RuntimeError('Bundled worker is missing')
        process.setProgram(str(helper))
        process.setArguments([worker, *arguments])
    else:
        process.setProgram(sys.executable)
        process.setArguments(['-B', str(root / 'runtime/tools/worker_entry.py'), worker, *arguments])

    def started():
        process.write(body)
        process.closeWriteChannel()

    process.started.connect(started)
