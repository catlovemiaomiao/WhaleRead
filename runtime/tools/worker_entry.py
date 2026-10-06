"""Only the allowlisted bundled workers may receive a request context."""
import sys
from pathlib import Path

root = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).resolve().parents[2]
sys.path[:0] = [str(root), str(root / 'runtime/lib'), str(root / 'runtime/tools')]
if sys.stdout is None:
    import os
    sys.stdout = os.fdopen(os.dup(1), 'w', buffering=1)
if sys.stderr is None:
    import os
    sys.stderr = os.fdopen(os.dup(2), 'w', buffering=1)
from worker_context import execute_worker

if __name__ == '__main__':
    if len(sys.argv) < 2:
        raise SystemExit('Worker name required')
    execute_worker(root, sys.argv[1], sys.argv[2:])
