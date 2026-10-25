"""Review regressions, using isolated libraries and no real model calls."""
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from textfold import ladder, local, notes, pdfdoc, pipeline, server, storage, storage_settings, store
from textfold.documents import Document, Source


class LocalRegressions(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "library"
        self.backend = storage.LocalDirectoryBackend(self.root)
        backend_context = store.use_backend(self.backend)
        backend_context.__enter__()
        self.addCleanup(backend_context.__exit__, None, None, None)
        self.pid = local.import_text("# Review fixture\n\nOriginal text.\n")

    def test_interrupt_before_atomic_file_replacement_keeps_original_document(self):
        from textfold import _storage_layout
        before = store.document(self.pid)
        changed = store.document(self.pid); changed.meta['title'] = 'Unpublished'
        with mock.patch.object(_storage_layout, 'replace_file', side_effect=KeyboardInterrupt()):
            with self.assertRaises(KeyboardInterrupt): self.backend.save(changed)
        self.assertEqual(store.document(self.pid).meta, before.meta)
        self.assertEqual(store.document(self.pid).source, before.source)

    def test_restart_recovers_a_process_killed_between_directory_renames(self):
        self._kill_save(after_publish=False)
        restarted = storage.LocalDirectoryBackend(self.root)
        self.assertEqual(restarted.get(self.pid).meta["title"], "Review fixture")

    def test_restart_keeps_a_published_document_after_process_exit(self):
        self._kill_save(after_publish=True)
        restarted = storage.LocalDirectoryBackend(self.root)
        self.assertEqual(restarted.get(self.pid).meta["title"], "Published")

    def test_uploaded_pdf_is_durable_before_progress_deletes_the_upload(self):
        pid = "2306.03310"
        self.backend.save(Document(pid, meta={"id": pid, "title": "Online paper"},
                                   source=Source(html='<article class="ltx_document"></article>')))
        upload = self.root.parent / "attachment.pdf"
        upload.write_bytes(b"%PDF-reader-original")
        code = '''
import os, sys, threading
from textfold import server, storage, store
with store.use_backend(storage.LocalDirectoryBackend(sys.argv[1])):
    with store.repository().workspace(sys.argv[2]):
        job = {"kind":"generate", "pid":sys.argv[2], "title":"Online paper", "attach":sys.argv[3]}
        server.Jobs._progress(object.__new__(server.Jobs), job, threading.Event())("parse", 0, 1)
        os._exit(17)
'''
        result = subprocess.run([sys.executable, "-c", code, str(self.root), pid, str(upload)],
                                capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 17, result.stderr.decode("utf-8", "replace"))
        self.assertEqual(storage.LocalDirectoryBackend(self.root).get(pid).source.pdf, b"%PDF-reader-original")
        self.assertFalse(upload.exists())

    def test_answer_and_another_note_saved_while_model_waits_both_survive(self):
        notes.put(self.pid, {"id": "n-question", "anchor": {"on": "paper"}, "text": "Question"})

        def model(*args, **kwargs):
            cache = args[3]
            cache.mkdir(parents=True, exist_ok=True)
            (cache / "completed.json").write_bytes(b'{"out":"cached answer"}')
            errors = []

            def save_note():
                try:
                    notes.put(self.pid, {"id": "n-other", "anchor": {"on": "paper"}, "text": "Keep me"})
                except Exception as error:
                    errors.append(error)

            thread = threading.Thread(target=save_note)
            thread.start()
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            return {"out": {"answer": "A useful answer", "takeaway": "Summary", "next": []}}, False

        with mock.patch.object(notes.llm, "call", side_effect=model):
            answer = notes.ask(self.pid, "n-question", "Why?", {"id": "fixture"})
        saved = {n["id"]: n for n in notes.read(self.pid)["notes"]}
        self.assertEqual(answer["thread"][-1]["a"], "A useful answer")
        self.assertEqual(saved["n-question"]["thread"][-1]["a"], "A useful answer")
        self.assertEqual(saved["n-other"]["text"], "Keep me")
        self.assertEqual(store.document(self.pid).processing.replies["completed.json"], b'{"out":"cached answer"}')

    def test_migration_retry_does_not_resurrect_a_deleted_document(self):
        home, target = self.root.parent / "home", self.root.parent / "destination"
        storage_settings.schedule(self.root, home, str(target))
        with mock.patch.object(storage_settings, "_save", side_effect=OSError("configuration unavailable")):
            self.assertEqual(storage_settings.activate(self.root, home), self.root)
        self.backend.delete(self.pid)
        self.assertEqual(storage_settings.activate(self.root, home), target)
        self.assertEqual(storage.LocalDirectoryBackend(target).list(), [])

    def test_migration_retry_updates_only_its_own_old_copy(self):
        home, target = self.root.parent / "home", self.root.parent / "destination"
        storage_settings.schedule(self.root, home, str(target))
        with mock.patch.object(storage_settings, "_save", side_effect=OSError("configuration unavailable")):
            storage_settings.activate(self.root, home)
        with store.edit(self.pid) as doc:
            doc.notes = {"notes": [{"text": "Written in the source after the failed move"}]}
            doc.meta["title"] = "Updated source title"
        self.assertEqual(storage_settings.activate(self.root, home), target)
        self.assertEqual(storage.LocalDirectoryBackend(target).get(self.pid).notes, store.document(self.pid).notes)

    def test_migration_retry_does_not_overwrite_user_edits_in_destination(self):
        home, target = self.root.parent / "home", self.root.parent / "destination"
        storage_settings.schedule(self.root, home, str(target))
        with mock.patch.object(storage_settings, "_save", side_effect=OSError("configuration unavailable")):
            storage_settings.activate(self.root, home)
        changed = target / "papers" / self.pid / "source.md"
        changed.write_bytes(b"User edited this copy")
        self.assertEqual(storage_settings.activate(self.root, home), self.root)
        self.assertEqual(changed.read_bytes(), b"User edited this copy")

    def test_warm_reader_cache_does_not_read_original_or_image_payloads(self):
        with store.edit(self.pid) as doc:
            doc.source.pdf = b"%PDF-" + b"x" * (1024 * 1024)
            doc.images["figure.bin"] = b"image bytes"
        server.PAGES.clear()
        self.addCleanup(server.PAGES.clear)
        expected = server.reader_page(self.pid)
        read = Path.read_bytes
        payloads = []

        def counted(path):
            if path.name in ("source.pdf", "figure.bin"):
                payloads.append(path.name)
            return read(path)

        with mock.patch.object(Path, "read_bytes", counted):
            self.assertEqual(server.reader_page(self.pid), expected)
        self.assertEqual(payloads, [])

    def test_assembling_a_reader_reuses_one_complete_document_snapshot(self):
        with mock.patch.object(self.backend, "get", wraps=self.backend.get) as reading:
            actual = store.assemble(self.pid)
        self.assertEqual(actual["meta"]["title"], "Review fixture")
        self.assertEqual(reading.call_count, 0)

    def test_checkpoint_cannot_publish_a_read_only_or_nested_read_only_session(self):
        repo = store.repository()
        with repo.workspace(self.pid, write=False):
            with repo.workspace(self.pid, write=True):
                with store.edit(self.pid) as doc:
                    doc.meta["title"] = "Temporary only"
                repo.checkpoint(self.pid)
        self.assertEqual(store.document(self.pid).meta["title"], "Review fixture")

    def test_first_listing_reuses_one_library_index_for_all_added_dates(self):
        for i in range(4):
            local.import_text(f"# Paper {i}\n\nBody {i}.\n")
        with mock.patch.object(self.backend, "list", wraps=self.backend.list) as listing:
            rows = server.listed()
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(row["added"] > 0 for row in rows))
        self.assertEqual(listing.call_count, 0)

    def test_pdf_reconversion_invalidates_layers_but_keeps_original_and_notes(self):
        pid = "pdf-0123456789abcdef"
        old = Document(pid, meta={"id": pid, "title": "Old", "source": {"kind": "pdf"}},
                       source=Source(html='<article class="ltx_document"><p class="ltx_p">Old text</p></article>', pdf=b"%PDF-original"),
                       layers={"en": {"version": "old"}, "tr-zh": {"version": "old-translation"}},
                       notes={"notes": [{"id": "n-preserved", "text": "Private note", "anchor": {"on": "paper"}}]})
        self.backend.save(old)
        with tempfile.TemporaryDirectory() as output:
            with mock.patch.object(pdfdoc, "to_markdown", return_value=("New", [], "# New\n\nNew words.\n")), \
                    mock.patch.object(pdfdoc, "cut_figures", return_value={}), mock.patch.object(pdfdoc, "layout_of", return_value=[]):
                pdfdoc.store_pdf(pid, output, {}, "fixture.pdf", "accurate", replace=True)
        actual = store.document(pid)
        self.assertEqual(actual.meta["title"], "New")
        self.assertEqual(actual.layers, {})
        self.assertEqual(actual.source.pdf, b"%PDF-original")
        self.assertEqual(actual.notes["notes"][0]["text"], "Private note")
        self.assertEqual(actual.notes["notes"][0]["id"], "n-preserved")

    def test_new_document_and_completed_english_layer_are_visible_during_generation(self):
        pid = "2306.03310"
        reached, release = threading.Event(), threading.Event()
        phase = []
        errors = []

        def fetched(identity, root):
            with store.edit(identity, create=True) as doc:
                doc.meta = {"id": identity, "title": "Fetched fixture", "authors": []}
                doc.source.html = '<article class="ltx_document"><p class="ltx_p">A research paragraph.</p></article>'

        def english(doc, *args, **kwargs):
            kwargs["progress"](0, 1)
            phase.append("english")
            reached.set()
            if not release.wait(10):
                raise AssertionError("reader did not inspect the English phase")
            release.clear()
            for unit in doc["units"].values():
                unit["lad"] = ladder.local(unit, doc["atoms"])

        def translated(doc, *args, **kwargs):
            phase.append("translation")
            reached.set()
            if not release.wait(10):
                raise AssertionError("reader did not inspect the translation phase")

        def generate():
            try:
                pipeline.generate(pid, "zh", {"id": "fixture", "kind": "api"})
            except BaseException as error:
                errors.append(error)

        with mock.patch.object(pipeline, "fetch", side_effect=fetched), \
                mock.patch.object(pipeline.ladder, "build", side_effect=english), \
                mock.patch.object(pipeline.links, "build"), \
                mock.patch.object(pipeline.translate, "build", side_effect=translated):
            thread = threading.Thread(target=generate)
            thread.start()
            try:
                self.assertTrue(reached.wait(10))
                self.assertTrue(store.readable(pid), "new document must open before any model result")
                self.assertIn(pid, [row["id"] for row in store.papers()])
                reached.clear()
                release.set()
                self.assertTrue(reached.wait(10))
                self.assertEqual(phase, ["english", "translation"])
                self.assertIsNotNone(store.read(pid, "en"), "completed English must open while translation runs")
            finally:
                release.set()
                thread.join(15)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])

    def _kill_save(self, after_publish):
        # Build the on-disk transaction left by the earlier experimental build.
        # The new fast local writer no longer swaps whole directories.
        code = r'''
import json, os, sys
from pathlib import Path
from textfold import storage
from textfold._storage_layout import LegacyLayout
root, identity, published = Path(sys.argv[1]), sys.argv[2], sys.argv[3] == "True"
backend = storage.LocalDirectoryBackend(root)
folder = root / "papers" / identity
stage = root / "papers/.document-legacy-fixture"; stage.mkdir()
raw = LegacyLayout.read_folder(folder)
meta = json.loads(raw["meta.json"]); meta["title"] = "Published"
raw["meta.json"] = json.dumps(meta).encode("utf-8")
LegacyLayout.write_folder(stage / "ready", raw)
(stage / "transaction.json").write_text(json.dumps({"format":"textfold/document-save", "id":identity,
    "pid":os.getpid(), "new":False, "digest":backend._fingerprint(raw)}))
folder.rename(stage / "backup")
if published: (stage / "ready").rename(folder)
os._exit(17)
'''
        result = subprocess.run([sys.executable, '-c', code, str(self.root), self.pid, str(after_publish)], capture_output=True, timeout=20)
        self.assertEqual(result.returncode, 17, result.stderr.decode('utf-8', 'replace'))


if __name__ == "__main__":
    unittest.main()
