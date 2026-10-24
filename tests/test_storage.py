"""The same document operations against disk and a backend with no permanent path.

Every directory is temporary. Neither a model nor the user's library is opened.
"""
import contextlib
import copy
import time
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from textfold import build, ladder, library, local, notes, pdf, pdfdoc, pdftext, pipeline, storage_settings, store, zotero
from textfold.storage import DocumentStore, LocalDirectoryBackend, document_info
from textfold.documents import ConflictError, Document, LibraryState
from textfold._storage_layout import DirectoryFiles


class MemoryBackend:
    """A plugin with only document values: no file keys, paths, or workspaces."""
    name = "memory"

    def __init__(self):
        self.documents = {}
        self.deleted = {}
        self.state = LibraryState()
        self.clock = 0

    def get(self, document_id):
        if document_id not in self.documents:
            raise FileNotFoundError(document_id)
        return copy.deepcopy(self.documents[document_id])

    def save(self, document):
        old = self.documents.get(document.id)
        if document.revision != (old.revision if old else None):
            raise ConflictError(document.id)
        self.clock += 1
        document.revision = self.clock
        document.created = old.created if old else time.time()
        document.updated = time.time()
        self.documents[document.id] = copy.deepcopy(document)

    def list(self):
        return [document_info(copy.deepcopy(d)) for d in self.documents.values() if d.meta is not None]

    def info(self, document_id):
        doc = self.get(document_id)
        if doc.meta is None:
            raise FileNotFoundError(document_id)
        return document_info(doc)

    def delete(self, document_id):
        if document_id not in self.documents:
            raise FileNotFoundError(document_id)
        del self.documents[document_id]

    def trash(self, document_id, placement=None):
        document = self.get(document_id)
        self.clock += 1
        token = str(self.clock)
        self.deleted[token] = (document, copy.deepcopy(placement or {}), time.time())
        self.delete(document_id)
        return token

    def restore(self, document_id):
        if document_id in self.documents:
            return None
        for token, (doc, placement, _) in reversed(list(self.deleted.items())):
            if doc.id == document_id:
                doc.revision = None
                self.save(doc)
                del self.deleted[token]
                return placement
        return None

    def purge(self, before):
        gone = [token for token, (_, _, at) in self.deleted.items() if at < before]
        for token in gone:
            del self.deleted[token]
        return gone

    def get_library(self):
        return copy.deepcopy(self.state)

    def save_library(self, state):
        if state.revision != self.state.revision:
            raise ConflictError("library")
        self.clock += 1
        state.revision = self.clock
        self.state = copy.deepcopy(state)


TEXT = "# Paper \U0001f4da\n\nFirst paragraph with \u4e2d\u6587 and a formula $x^2$.\n\n## Results\n\nSecond paragraph.\n"


class BackendContract:
    def test_stable_id_history_and_reader_cache_follow_document_revisions(self):
        from textfold import ids, server
        with store.use_backend(self.backend):
            pid = local.import_text(TEXT)
            current = store.parsed(pid)
            old = copy.deepcopy(current)
            lost = 'u-historical'
            old['units'][lost] = copy.deepcopy(next(iter(old['units'].values())))
            old['chunks'][0]['units'].append(lost)
            original = copy.deepcopy(store.document(pid).source)
            with mock.patch.object(store, 'pdir', side_effect=AssertionError('History metadata must use document storage')), \
                 mock.patch.object(build, 'page', side_effect=lambda doc, **kw: json.dumps(doc.get('gone', {}))), \
                 mock.patch.dict(server.PAGES, {}, clear=True):
                self.assertEqual(json.loads(server.reader_page(pid)), {})
                ids.remember(pid, old, current)
                self.assertIn(lost, ids.load(pid)['units'])
                self.assertIn(lost, json.loads(server.reader_page(pid))['units'])
                self.assertEqual(store.document(pid).source, original)

    def test_a_real_pdf_is_parsed_through_a_disposable_workspace(self):
        from test_pdftext import typeset
        data = typeset([(20, "Storage Paper"), (11, "Embedded words are read without a model or remote filesystem.")])
        with tempfile.TemporaryDirectory() as tmp, store.use_backend(self.backend):
            file = Path(tmp) / "paper.pdf"
            file.write_bytes(data)
            pid = pdfdoc.pid_of(data)
            self.assertTrue(pdftext.save(pid, file, "paper.pdf"))
            self.assertEqual(store.asset(pid, "source.pdf"), data)
            self.assertIn("Storage Paper", build.page(store.assemble(pid), app=True))

    def test_import_layers_notes_library_pdf_and_render(self):
        with store.use_backend(self.backend):
            pid = local.import_text(TEXT)
            self.assertEqual(store.asset(pid, "source.md").decode("utf-8").replace("\r\n", "\n"), TEXT)
            doc = store.assemble(pid)
            store.save_en(doc, "test")
            store.save_tr(doc, "zh", store.read(pid, "en")["version"], "test")
            self.assertEqual(store.languages(pid), ["zh"])
            self.assertEqual(store.read(pid, "en")["model"], "test")
            note = {"id": "n-storage1", "kind": "note", "text": "Keep \U0001f4da", "anchor": {"on": "paper"}}
            notes.put(pid, note)
            self.assertEqual(notes.read(pid)["notes"][0]["text"], note["text"])
            library.change({"op": "new", "name": "Reading"})
            self.assertEqual(library.folders()["folders"][0]["name"], "Reading")
            zotero.link("users/0", "ITEMKEY2", pid, "Paper", {"citekey": "paper2026"})
            self.assertEqual(zotero.citekeys(), {pid: "paper2026"})
            self.assertTrue(pdf.keep(pid, b"%PDF-1.4\nfixture"))
            self.assertEqual(pdf.read(pid), b"%PDF-1.4\nfixture")
            self.assertIn("Paper", build.page(store.assemble(pid), app=True))
            row = next(p for p in store.papers() if p["id"] == pid)
            self.assertEqual((row["notes"], row["langs"]), (1, ["en", "zh"]))

    def test_replace_preserves_notes_and_previous_original(self):
        with store.use_backend(self.backend):
            pid = local.import_text(TEXT)
            notes.put(pid, {"id": "n-storage2", "kind": "note", "text": "Mine", "anchor": {"on": "paper"}})
            local.replace_text(pid, TEXT.replace("Second paragraph.", "Edited paragraph."))
            self.assertIn(b"Edited paragraph", store.asset(pid, "source.md"))
            self.assertIn(b"Second paragraph", store.asset(pid, "previous/source.md"))
            self.assertEqual(notes.read(pid)["notes"][0]["text"], "Mine")

    def test_trash_restore_keeps_every_asset(self):
        with store.use_backend(self.backend):
            pid = local.import_text(TEXT)
            store.put_asset(pid, "img/figure.bin", b"\x00\xff")
            store.trash(pid, {"name": "My paper"})
            self.assertFalse(store.has(pid))
            self.assertEqual(store.untrash(pid), {"name": "My paper"})
            self.assertEqual(store.asset(pid, "img/figure.bin"), b"\x00\xff")
            self.assertEqual(store.empty_trash(), [])

    def test_generation_uses_the_selected_backend(self):
        with store.use_backend(self.backend):
            pid = local.import_text(TEXT)

            def make(doc, *args, **kw):
                for u in doc["units"].values():
                    u["lad"] = ladder.local(u, doc["atoms"])

            with mock.patch.object(ladder, "build", side_effect=make), mock.patch.object(pipeline.links, "build"):
                self.assertEqual(pipeline.generate(pid, "en", {"id": "test", "kind": "api"}), pid)
            self.assertEqual(store.read(pid, "en")["model"], "test")

    def test_document_is_the_save_and_index_unit(self):
        with store.use_backend(self.backend):
            pid = local.import_text(TEXT)
            with store.edit(pid) as doc:
                doc.notes = {"notes": [{"text": "A semantic note"}]}
                doc.images["plot.bin"] = b"image"
                doc.source.pdf = b"%PDF-all together"
            saved = store.document(pid)
            self.assertEqual(saved.notes["notes"][0]["text"], "A semantic note")
            self.assertEqual(saved.images["plot.bin"], b"image")
            self.assertEqual(saved.source.pdf, b"%PDF-all together")
            self.assertEqual(store.repository().list()[0].note_count, 1)

    def test_detached_snapshots_and_stale_save_conflicts(self):
        with store.use_backend(self.backend):
            pid = local.import_text(TEXT)
            first, second = store.document(pid), store.document(pid)
            first.meta["title"] = "New title"
            self.assertNotEqual(store.document(pid).meta["title"], "New title")
            store.repository().save(first)
            second.meta["title"] = "Stale title"
            with self.assertRaises(ConflictError):
                store.repository().save(second)
            self.assertEqual(store.document(pid).meta["title"], "New title")

    def test_parser_preserves_completed_caches_after_failure(self):
        with store.use_backend(self.backend):
            pid = local.import_text(TEXT)
            with self.assertRaisesRegex(RuntimeError, "generation stopped"):
                with store.repository().workspace(pid) as folder:
                    cache = folder / "llm-cache" / "completed.json"
                    cache.parent.mkdir(parents=True)
                    cache.write_bytes(b'{"out": "done"}')
                    raise RuntimeError("generation stopped")
            self.assertEqual(store.document(pid).processing.replies["completed.json"], b'{"out": "done"}')

    def test_parser_merges_unrelated_concurrent_notes(self):
        with store.use_backend(self.backend):
            pid = local.import_text(TEXT)
            with store.repository().workspace(pid) as folder:
                latest = self.backend.get(pid)
                latest.notes = {"notes": [{"text": "Saved on another request"}]}
                self.backend.save(latest)
                (folder / "img").mkdir(exist_ok=True)
                (folder / "img" / "plot.bin").write_bytes(b"image")
            result = store.document(pid)
            self.assertEqual(result.notes["notes"][0]["text"], "Saved on another request")
            self.assertEqual(result.images["plot.bin"], b"image")

    def test_parser_rejects_overlapping_concurrent_edits(self):
        with store.use_backend(self.backend):
            pid = local.import_text(TEXT)
            with self.assertRaises(ConflictError):
                with store.repository().workspace(pid) as folder:
                    latest = self.backend.get(pid)
                    latest.source.markdown = "Saved elsewhere"
                    self.backend.save(latest)
                    (folder / "source.md").write_text("Parser edit", encoding="utf-8")
            self.assertEqual(store.document(pid).source.markdown, "Saved elsewhere")

    def test_unsafe_document_ids_are_rejected(self):
        repository = DocumentStore(self.backend)
        for identity in ("../x", "/x", "C:/x", "a\\b", "a/../b", "a//b"):
            with self.subTest(identity=identity), self.assertRaises(ValueError):
                repository.get(identity)

    def test_library_state_has_its_own_revision(self):
        first, second = self.backend.get_library(), self.backend.get_library()
        first.catalog = {"folders": [{"id": "f1", "name": "Reading"}]}
        self.backend.save_library(first)
        second.zotero = {"users/0/KEY": {"paper": "2306.03310"}}
        with self.assertRaises(ConflictError):
            self.backend.save_library(second)
        self.assertEqual(self.backend.get_library().catalog, first.catalog)


class LocalDocuments(BackendContract, unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / "data"
        self.backend = LocalDirectoryBackend(self.root)

    def test_opening_a_legacy_library_does_not_rewrite_it(self):
        folder = self.root / "papers" / "2306.03310"
        folder.mkdir(parents=True)
        raw = b'{"id":"2306.03310","title":"LIBERO","authors":[]}'
        (folder / "meta.json").write_bytes(raw)
        before = (folder / "meta.json").stat().st_mtime_ns
        with store.use_backend(self.backend):
            self.assertEqual(store.pdir("2306.03310"), folder)
            self.assertEqual(store.papers()[0]["title"], "LIBERO")
        self.assertEqual((folder / "meta.json").read_bytes(), raw)
        self.assertEqual((folder / "meta.json").stat().st_mtime_ns, before)

    def test_semantic_edit_preserves_unmodified_bytes_and_timestamps(self):
        with store.use_backend(self.backend):
            pid = local.import_text(TEXT)
            folder = store.pdir(pid)
            (folder / "plugin-data.bin").write_bytes(b"opaque\x00\xff")
            files = DirectoryFiles(folder)
            before = {e.key: (files.read(e.key), e.modified) for e in files.entries_for_root()}
            with store.edit(pid) as doc:
                doc.notes = {"notes": [{"text": "A new note"}]}
            for key, (raw, modified) in before.items():
                self.assertEqual(files.read(key), raw)
                self.assertEqual(files.path(key).stat().st_mtime, modified)

    def test_index_does_not_read_originals(self):
        with store.use_backend(self.backend):
            pid = local.import_text(TEXT)
            with store.edit(pid) as doc:
                doc.source.pdf = b"%PDF-original"
            read = DirectoryFiles.read

            def read_index(files, key):
                self.assertFalse(key.endswith(".pdf"))
                return read(files, key)

            with mock.patch.object(DirectoryFiles, "read", read_index):
                self.assertEqual(self.backend.list()[0].id, pid)

    def test_failed_document_publication_restores_original(self):
        from textfold import storage
        with store.use_backend(self.backend):
            pid = local.import_text(TEXT)
            before = store.document(pid)
            replacement = store.document(pid)
            replacement.meta["title"] = "Not published"
            rename = storage.rename_directory

            def fail_publication(source, target):
                if Path(source).name == "ready":
                    raise OSError("publication failed")
                return rename(source, target)

            with mock.patch.object(storage, "rename_directory", side_effect=fail_publication), self.assertRaises(OSError):
                self.backend.save(replacement)
            self.assertEqual(store.document(pid).meta, before.meta)
            self.assertEqual(store.document(pid).source, before.source)


class MemoryDocuments(BackendContract, unittest.TestCase):
    def setUp(self):
        self.backend = MemoryBackend()

    def test_no_path_is_available_outside_a_workspace(self):
        with store.use_backend(self.backend), self.assertRaises(RuntimeError):
            store.pdir("2306.03310")


class Migration(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.source, self.target, self.home = (self.root / n for n in ("old", "new", "settings"))
        self.backend = DirectoryFiles(self.source)
        self.backend.write("papers/2306.03310/source.pdf", b"%PDF-1.4\x00\xff")
        self.backend.write("papers/2306.03310/layers/tr-zh.json", '{"title":"中文"}'.encode("utf-8"))
        self.backend.write("papers/2306.03310/notes.json", b'{"notes":[]}')
        self.backend.write("papers/.trash/old-20261001-000000/notes.json", b"{}")
        self.backend.write("library.json", b'{"folders":[]}')
        self.backend.write("zotero.json", b"{}")
        self.backend.write("server.log", b"runtime log")
        self.backend.write("engine/not-a-document", b"engine")

    def test_schedule_then_restart_copies_documents_only_without_changing_source(self):
        before = {e.key: self.backend.read(e.key) for e in self.backend.entries_for_root()}
        storage_settings.schedule(self.source, self.home, str(self.target))
        self.assertFalse(self.target.exists())
        self.assertEqual(storage_settings.active_path(self.source, self.home), self.source)
        self.backend.write("papers/2306.03310/notes.json", b'{"notes":["written before restart"]}')
        before["papers/2306.03310/notes.json"] = b'{"notes":["written before restart"]}'
        self.assertEqual(storage_settings.activate(self.source, self.home), self.target)
        for key, data in before.items():
            self.assertEqual(self.backend.read(key), data)
            if key.startswith("papers/") or key in ("library.json", "zotero.json"):
                self.assertEqual((self.target / key).read_bytes(), data)
        self.assertFalse((self.target / "server.log").exists())
        self.assertFalse((self.target / "engine").exists())
        self.assertEqual(storage_settings.active_path(self.source, self.home), self.target)

    def test_failure_keeps_the_old_library_selected(self):
        storage_settings.schedule(self.source, self.home, str(self.target))
        with mock.patch.object(storage_settings.shutil, "copy2", side_effect=OSError("disk full")):
            self.assertEqual(storage_settings.activate(self.source, self.home), self.source)
        self.assertIn("disk full", storage_settings.status(self.source, self.home)["error"])
        self.assertEqual(storage_settings.activate(self.source, self.home), self.target)

    def test_overlap_relative_and_nonempty_targets_are_refused(self):
        bad = self.root / "occupied"
        bad.mkdir()
        (bad / "keep.txt").write_bytes(b"keep")
        for path in (str(bad), str(self.source / "child"), str(self.root), "relative"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                storage_settings.schedule(self.source, self.home, path)
        self.assertEqual((bad / "keep.txt").read_bytes(), b"keep")

    def test_pending_move_can_be_cancelled(self):
        storage_settings.schedule(self.source, self.home, str(self.target))
        status = storage_settings.schedule(self.source, self.home, cancel=True)
        self.assertEqual(status["pending"], "")
        self.assertEqual(storage_settings.activate(self.source, self.home), self.source)
        self.assertFalse(self.target.exists())

    def test_conflicting_target_added_after_schedule_is_not_overwritten(self):
        storage_settings.schedule(self.source, self.home, str(self.target))
        self.target.mkdir()
        (self.target / "keep").write_bytes(b"keep")
        self.assertEqual(storage_settings.activate(self.source, self.home), self.source)
        self.assertEqual((self.target / "keep").read_bytes(), b"keep")

    def test_configuration_write_failure_does_not_select_the_copy(self):
        storage_settings.schedule(self.source, self.home, str(self.target))
        with mock.patch.object(storage_settings, "_save", side_effect=OSError("settings disk full")):
            self.assertEqual(storage_settings.activate(self.source, self.home), self.source)
        self.assertEqual(storage_settings.active_path(self.source, self.home), self.source)
        self.assertEqual(storage_settings.activate(self.source, self.home), self.target)

    def test_copy_temporary_names_do_not_collide_with_document_assets(self):
        self.backend.write("papers/2306.03310/foo", b"first")
        self.backend.write("papers/2306.03310/foo.textfold-copy", b"second")
        storage_settings.schedule(self.source, self.home, str(self.target))
        self.assertEqual(storage_settings.activate(self.source, self.home), self.target)
        self.assertEqual((self.target / "papers/2306.03310/foo.textfold-copy").read_bytes(), b"second")

    def test_source_changes_during_copy_leave_it_selected(self):
        storage_settings.schedule(self.source, self.home, str(self.target))
        original = storage_settings.shutil.copy2
        changed = False

        def copy_and_change(src, dst):
            nonlocal changed
            result = original(src, dst)
            if not changed:
                changed = True
                self.backend.write("papers/2306.03310/notes.json", b'{"notes":["new"]}')
            return result

        with mock.patch.object(storage_settings.shutil, "copy2", side_effect=copy_and_change):
            self.assertEqual(storage_settings.activate(self.source, self.home), self.source)
        self.assertEqual(storage_settings.active_path(self.source, self.home), self.source)
        self.assertEqual(self.backend.read("papers/2306.03310/notes.json"), b'{"notes":["new"]}')

    def test_another_running_server_defers_migration(self):
        storage_settings.schedule(self.source, self.home, str(self.target))
        self.backend.write("server.json", json.dumps({"format": "textfold/server", "pid": os.getpid() + 1}).encode())
        with mock.patch.object(storage_settings, "_process_alive", return_value=True):
            self.assertEqual(storage_settings.activate(self.source, self.home), self.source)
        self.assertFalse(self.target.exists())
        self.assertIn("other TextFold", storage_settings.status(self.source, self.home)["error"])

    def test_current_process_liveness_is_queried_without_signaling_it(self):
        self.assertTrue(storage_settings._process_alive(os.getpid()))
