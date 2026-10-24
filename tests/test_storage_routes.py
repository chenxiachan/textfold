"""Storage settings through the real server, including an application restart.

HOME, data and destination are temporary. No user's settings or library is used.
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path


class StorageRoutes(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.data, self.target = self.root / "data", self.root / "destination"
        home = self.root / "home"
        home.mkdir()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home),
                    "LOCALAPPDATA": str(home / "AppData" / "Local"), "TEXTFOLD_DATA": str(self.data),
                    "TEXTFOLD_NO_MODELS": "1", "PYTHONUTF8": "1"}
        self.proc = None
        self.addCleanup(self.stop)
        self.start()

    def start(self):
        self.proc = subprocess.Popen([sys.executable, "-m", "textfold", "serve", "--port", str(self.port)],
                                     cwd=Path(__file__).resolve().parent.parent, env=self.env,
                                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                if self.call("/api/storage")[0] == 200:
                    return
            except OSError:
                pass
            if self.proc.poll() is not None:
                self.fail("Storage server exited during startup")
            time.sleep(0.1)
        self.fail("Storage server did not start")

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=10)

    def call(self, path, body=None):
        request = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}",
                                         data=None if body is None else json.dumps(body).encode("utf-8"),
                                         headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    def test_settings_restart_and_existing_document_are_preserved(self):
        _, imported = self.call("/api/import", {"name": "notes.md", "text": "# Original \U0001f4da\n\nKeep this text.\n"})
        pid = imported["paper"]
        source = self.data / "papers" / pid / "source.md"
        before = source.read_bytes()
        code, status = self.call("/api/storage", {"path": str(self.target)})
        self.assertEqual((code, status["path"], status["pending"]), (200, str(self.data), str(self.target)))
        self.assertFalse(self.target.exists())
        # A change made after saving the setting belongs in the startup copy too.
        self.assertEqual(self.call("/api/import", {"name": "late.md", "text": "# Late\n\nWritten before restart."})[0], 200)
        self.stop()
        self.start()
        code, status = self.call("/api/storage")
        self.assertEqual((code, status["path"], status["pending"], status["error"]), (200, str(self.target), "", ""))
        self.assertEqual(source.read_bytes(), before)
        self.assertEqual((self.target / "papers" / pid / "source.md").read_bytes(), before)
        self.assertEqual(len(self.call("/api/papers")[1]), 2)
        self.assertEqual(self.call(f"/api/doc/{pid}")[0], 200)
        self.assertEqual(self.call("/api/import", {"name": "new.md", "text": "# New\n\nNew library only."})[0], 200)
        self.assertEqual(len(list((self.data / "papers").glob("*/meta.json"))), 2)
        self.assertEqual(len(list((self.target / "papers").glob("*/meta.json"))), 3)

    def test_bad_destination_and_cancel_do_not_change_the_active_library(self):
        self.assertEqual(self.call("/api/storage", {"path": "relative"})[0], 400)
        self.call("/api/storage", {"path": str(self.target)})
        code, status = self.call("/api/storage", {"cancel": True})
        self.assertEqual((code, status["path"], status["pending"]), (200, str(self.data), ""))
        self.stop()
        self.start()
        self.assertFalse(self.target.exists())

    def test_a_failed_startup_copy_keeps_the_old_library_accessible(self):
        self.call("/api/import", {"name": "old.md", "text": "# Old\n\nStill readable."})
        self.call("/api/storage", {"path": str(self.target)})
        self.target.mkdir()
        (self.target / "unrelated.txt").write_bytes(b"do not overwrite")
        self.stop()
        self.start()
        status = self.call("/api/storage")[1]
        self.assertEqual(status["path"], str(self.data))
        self.assertTrue(status["error"])
        self.assertEqual(len(self.call("/api/papers")[1]), 1)
        self.assertEqual((self.target / "unrelated.txt").read_bytes(), b"do not overwrite")
