from __future__ import annotations

import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402
from PySide6.QtNetwork import QLocalServer  # noqa: E402

import native_launcher as launcher  # noqa: E402


class SingleInstanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QCoreApplication.instance() or QCoreApplication([])

    def test_lock_path_is_sanitized_and_scoped_to_requested_directory(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = launcher.instance_lock_path("鲸读/test instance", raw)
            self.assertEqual(path.parent, Path(raw))
            self.assertEqual(path.name, "-test-instance.lock")

    def test_second_guard_does_not_claim_live_instance_and_notifies_primary(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            instance_key = f"jingdu-test-{uuid.uuid4().hex}"
            lock_path = Path(raw) / "instance.lock"
            primary = launcher.SingleInstanceGuard(instance_key, lock_path)
            secondary = launcher.SingleInstanceGuard(instance_key, lock_path)
            activations: list[bool] = []
            primary.activationRequested.connect(lambda: activations.append(True))
            try:
                self.assertTrue(primary.claim())
                self.assertFalse(secondary.claim())
                deadline = time.monotonic() + 1.0
                while not activations and time.monotonic() < deadline:
                    self.app.processEvents()
                    time.sleep(0.01)
                self.assertEqual(activations, [True])
            finally:
                secondary.close()
                primary.close()
                QLocalServer.removeServer(str(launcher.instance_server_path(instance_key, lock_path)))

    def test_dead_server_endpoint_is_recovered_by_new_primary(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            instance_key = f"jingdu-test-{uuid.uuid4().hex}"
            stale_server = QLocalServer()
            server_path = str(launcher.instance_server_path(instance_key, Path(raw) / 'instance.lock'))
            self.assertTrue(stale_server.listen(server_path))
            stale_server.close()
            guard = launcher.SingleInstanceGuard(instance_key, Path(raw) / "instance.lock")
            try:
                self.assertTrue(guard.claim())
            finally:
                guard.close()
                QLocalServer.removeServer(server_path)


if __name__ == "__main__":
    unittest.main()
