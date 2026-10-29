"""The PDF engine's folder: beside the library unless the reader chooses one, an empty one; where it was installed it
stays (its Python cannot move), when the library moves too.

The settings, the library, the system's folder for app data and PaperFold's old place are temporary: nothing of the
reader's is read or written, and nothing is installed (the steps of the install are stubbed).
"""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from textfold import engine, models, server, store


def ready(e):
    """An engine's folder as an install leaves it (its command, its models, its mark)."""
    kit = engine._bin(e, "mineru-kit")
    kit.parent.mkdir(parents=True, exist_ok=True)
    kit.touch()
    (e / "mineru-home" / "models").mkdir(parents=True, exist_ok=True)
    (e / engine.MARK).write_text("mineru\n", encoding="utf-8")
    return e


class Base(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.data, self.app = self.root / "data", self.root / "app-data"
        self.library = self.data   # (not moved)
        for target, value in ((models, {"SETTINGS": self.root / "home" / "settings.json", "OLD_HOMES": ()}),
                              (store, {"DATA": self.data, "app_data": lambda: self.app,
                                       "library_root": lambda: self.library}),
                              (engine, {"legacy": lambda: self.root / "PaperFold" / "engine"})):
            for name, v in value.items():
                patch = mock.patch.object(target, name, v)
                patch.start()
                self.addCleanup(patch.stop)


class Folder(Base):
    def test_beside_the_library(self):
        self.assertEqual(engine.status()["path"], str(self.app / "engine"))
        self.library = self.root / "D" / "TextFold"   # moved in Settings
        self.assertEqual(engine.home(), self.library / "engine")
        self.assertEqual(engine.status()["path"], str(self.library / "engine"))
        self.assertFalse(engine.status()["installed"])

    def test_a_chosen_folder_is_used_and_forgotten_when_removed(self):
        chosen = self.root / "E" / "MinerU"
        self.assertEqual(engine.choose(str(chosen)), chosen)
        self.assertEqual(engine.status()["path"], str(chosen))
        self.assertIsNone(engine.where())
        ready(chosen)
        self.assertEqual(engine.where(), chosen)
        engine.uninstall()
        self.assertFalse(chosen.exists())
        self.assertIsNone(models.load_settings()["engine_path"])
        self.assertEqual(engine.status()["path"], str(self.app / "engine"))

    def test_a_folder_with_other_files_is_refused(self):
        full = self.root / "Documents"
        full.mkdir()
        (full / "thesis.docx").write_text("mine", encoding="utf-8")
        for folder, code in (("engine", "path"), ("", "path"), (str(full), "not_empty"), (str(full / "thesis.docx"), "not_empty")):
            with self.assertRaises(ValueError) as refused:
                engine.choose(folder)
            self.assertEqual(refused.exception.code, code)
        self.assertIsNone(models.load_settings()["engine_path"])
        (self.root / "empty").mkdir()
        self.assertEqual(engine.choose(str(self.root / "empty")), self.root / "empty")
        self.assertEqual(engine.choose(str(ready(self.root / "old-engine"))), self.root / "old-engine")   # an engine's own

    def test_it_stays_where_it_was_installed_when_the_library_moves(self):
        with mock.patch.object(engine, "_uv", return_value="uv"), mock.patch.object(engine, "_run") as run:
            engine.install()
        self.assertTrue(run.called)
        self.assertEqual(models.load_settings()["engine_path"], str(self.app / "engine"))
        ready(self.app / "engine")
        self.library = self.root / "D" / "TextFold"
        self.assertEqual(engine.where(), self.app / "engine")

    def test_one_installed_before_a_folder_was_recorded_is_found(self):
        ready(self.app / "engine")
        self.library = self.root / "D" / "TextFold"
        self.assertEqual(engine.where(), self.app / "engine")
        self.assertTrue(engine.status()["installed"])


class FakeJobs:
    def __init__(self):
        self.started = []

    def _same(self, **kw):
        return None

    def install_engine(self):
        self.started.append(engine.target())
        return {"id": "j1", "kind": "install", "status": "queued"}


class Route(Base):
    def setUp(self):
        super().setUp()
        self.jobs = FakeJobs()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.jobs))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.addCleanup(self.httpd.server_close)
        self.addCleanup(self.httpd.shutdown)

    def post(self, path, body):
        req = urllib.request.Request(f"http://127.0.0.1:{self.httpd.server_port}{path}", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_install_into_the_folder_chosen_in_settings(self):
        full = self.root / "Documents"
        full.mkdir()
        (full / "notes.txt").write_text("mine", encoding="utf-8")
        code, got = self.post("/api/engine/install", {"path": str(full)})
        self.assertEqual((code, got.get("code")), (400, "not_empty"))
        self.assertEqual(self.jobs.started, [])
        code, got = self.post("/api/engine/install", {"path": str(self.root / "E" / "MinerU")})
        self.assertEqual((code, got["kind"]), (200, "install"))
        self.assertEqual(self.jobs.started, [self.root / "E" / "MinerU"])
        code, got = self.post("/api/engine/install", {})   # (a file's card: where it was chosen)
        self.assertEqual(self.jobs.started[-1], self.root / "E" / "MinerU")

    def test_no_other_folder_once_it_is_installed(self):
        ready(self.app / "engine")
        code, got = self.post("/api/engine/install", {"path": str(self.root / "E" / "MinerU")})
        self.assertEqual((code, got["error"]), (409, "busy"))
        self.assertEqual(self.jobs.started, [])


if __name__ == "__main__":
    unittest.main()
