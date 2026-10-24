"""Document repositories. File layout and parser workspaces stay internal."""
import contextlib
import hashlib
import json
import os
import re
import shutil
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .documents import ConflictError, Document, DocumentInfo, LibraryState, StorageBackend
from ._storage_layout import DirectoryFiles, LegacyLayout, checked_id, checked_key, decode_text, rename_directory


def document_info(doc):
    languages = (["en"] if "en" in doc.layers else []) + sorted(k[3:] for k in doc.layers if k.startswith("tr-"))
    titles = {k[3:]: v["title"] for k, v in doc.layers.items() if k.startswith("tr-") and v.get("title")}
    return DocumentInfo(doc.id, doc.meta or {}, titles, languages,
                        len((doc.notes or {}).get("notes", [])), doc.created, doc.updated, doc.revision,
                        doc.meta is not None and doc.source.html is not None)


_locks = {}
_locks_guard = threading.Lock()


class LocalDirectoryBackend:
    """Read existing libraries in place; retain layout and unchanged bytes."""
    name = "local"

    def __init__(self, root):
        self.root = Path(root).expanduser().resolve()
        self._files = DirectoryFiles(self.root)
        with _locks_guard:
            self._lock = _locks.setdefault(self.root, threading.RLock())
        with self._lock:
            self._recover()

    @staticmethod
    def _fingerprint(raw):
        digest = hashlib.sha256()
        for key, value in sorted(raw.items()):
            digest.update(key.encode("utf-8") + b"\0")
            digest.update(len(value).to_bytes(8, "big"))
            digest.update(value)
        return digest.hexdigest()

    def _recover(self):
        """Recover only our journaled swaps, never unrelated hidden folders."""
        from .storage_settings import _process_alive
        papers = self._files.path("papers")
        for stage in papers.glob(".document-*"):
            if stage.is_symlink() or getattr(stage, "is_junction", lambda: False)():
                continue
            try:
                journal = json.loads((stage / "transaction.json").read_text(encoding="utf-8"))
                if not isinstance(journal, dict) or journal.get("format") != "textfold/document-save":
                    continue
                folder = self._path(journal["id"])
                owner = journal["pid"]
                if owner != os.getpid() and _process_alive(owner):
                    continue
                ready, backup = stage / "ready", stage / "backup"
                if any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in (ready, backup)):
                    continue
                if not folder.exists() and backup.is_dir():
                    rename_directory(backup, folder)
                elif not folder.exists() and journal.get("new") and ready.is_dir():
                    if self._fingerprint(LegacyLayout.read_folder(ready)) != journal["digest"]:
                        continue
                    rename_directory(ready, folder)
                elif backup.exists():
                    # Another writer may have recreated this ID. Keep the
                    # backup unless this is exactly the published candidate.
                    if self._fingerprint(LegacyLayout.read_folder(folder)) != journal["digest"]:
                        continue
                if folder.exists():
                    shutil.rmtree(stage)
            except (OSError, ValueError, KeyError, TypeError):
                # An unavailable/ambiguous backup remains recoverable on disk.
                continue

    def _path(self, identity):
        return self._files.path("papers/" + checked_id(identity))

    def _entries(self, identity):
        return list(DirectoryFiles(self._path(identity)).entries_for_root())

    @staticmethod
    def _read_legacy(folder, keys=None):
        from .store import read_text
        files = DirectoryFiles(folder)
        keys = keys if keys is not None else [e.key for e in files.entries_for_root()]
        raw = {}
        for key in keys:
            # Preserve upstream's one-time repair for old Windows codepages.
            # UTF-8 assets, including their line endings, stay byte-identical.
            if key in (*LegacyLayout.TEXT.values(), *LegacyLayout.JSON.values()) or key.startswith("layers/"):
                read_text(files.path(key))
            raw[key] = files.read(key)
        return raw

    @staticmethod
    def _revision(entries):
        return tuple(sorted((e.key, e.version, e.size) for e in entries)) or None

    def get(self, document_id):
        with self._lock:
            entries = self._entries(document_id)
            if not entries:
                raise FileNotFoundError(document_id)
            raw = self._read_legacy(self._path(document_id))
            entries = self._entries(document_id)
            return LegacyLayout.decode(document_id, raw, revision=self._revision(entries), entries=entries)

    @classmethod
    def _folder_info(cls, folder, identity):
        files = DirectoryFiles(folder)
        if not files.exists("meta.json"):
            raise FileNotFoundError(identity)
        entries = list(files.entries_for_root())
        keys = [e.key for e in entries if e.key in ("meta.json", "notes.json") or e.key.startswith("layers/")]
        raw = cls._read_legacy(folder, keys)
        entries = list(files.entries_for_root())
        doc = LegacyLayout.decode(identity, raw, revision=cls._revision(entries), entries=entries)
        doc.source.html = "" if any(e.key == "source.html" for e in entries) else None
        return document_info(doc)

    def info(self, document_id):
        """A document index lookup never loads original/attachment payloads."""
        with self._lock:
            return self._folder_info(self._path(document_id), document_id)

    def save(self, document):
        raw = LegacyLayout.encode(document)
        if not raw:
            raise ValueError("Cannot save an empty document")
        with self._lock:
            entries = self._entries(document.id)
            if document.revision != self._revision(entries):
                raise ConflictError(f"Document changed: {document.id}")
            folder = self._path(document.id)
            old = LegacyLayout.read_folder(folder)
            if raw != old:
                folder.parent.mkdir(parents=True, exist_ok=True)
                stage = Path(tempfile.mkdtemp(prefix=".document-", dir=folder.parent))
                ready, backup = stage / "ready", stage / "backup"
                published = False
                try:
                    if folder.exists():
                        shutil.copytree(folder, ready, symlinks=True)
                    LegacyLayout.write_folder(ready, raw)
                    journal = {"format": "textfold/document-save", "id": document.id,
                               "pid": os.getpid(), "new": not folder.exists(), "digest": self._fingerprint(raw)}
                    DirectoryFiles(stage).write("transaction.json", json.dumps(journal).encode("utf-8"))
                    if folder.exists():
                        rename_directory(folder, backup)
                    rename_directory(ready, folder)
                    published = True
                finally:
                    # This also runs for KeyboardInterrupt/SystemExit. A failed
                    # rollback must leave the only original copy on disk.
                    if not published and backup.exists() and not folder.exists():
                        try:
                            rename_directory(backup, folder)
                        except BaseException:
                            pass
                    if published or not backup.exists():
                        shutil.rmtree(stage, ignore_errors=True)
            document.revision = self._revision(self._entries(document.id))
            document._legacy = dict(raw)
            info = LegacyLayout.decode(document.id, {}, entries=self._entries(document.id))
            document.created, document.updated = info.created, info.updated

    def list(self):
        with self._lock:
            root = self._files.path("papers")
            out = []
            for folder in sorted(root.iterdir()) if root.exists() else []:
                if folder.name.startswith(".") or not folder.is_dir() or folder.is_symlink():
                    continue
                try:
                    out.append(self._folder_info(folder, folder.name))
                except FileNotFoundError:
                    continue
            return out

    def delete(self, document_id):
        with self._lock:
            path = self._path(document_id)
            if not path.exists():
                raise FileNotFoundError(document_id)
            shutil.rmtree(path)

    def trash(self, document_id, placement=None):
        with self._lock:
            folder = self._path(document_id)
            target = self._files.path(f"papers/.trash/{document_id}-{time.strftime('%Y%m%d-%H%M%S')}")
            target.parent.mkdir(parents=True, exist_ok=True)
            rename_directory(folder, target)
            if placement:
                DirectoryFiles(target).write("library.json", json.dumps(placement, ensure_ascii=False).encode("utf-8"))
            return str(target)

    def restore(self, document_id):
        from .store import valid_id
        with self._lock:
            folder = self._path(document_id)
            if not valid_id(document_id) or folder.exists():
                return None
            pattern = re.compile(re.escape(document_id) + r"-\d{8}-\d{6}")
            gone = sorted(d for d in self._files.path("papers/.trash").glob(f"{document_id}-*")
                          if pattern.fullmatch(d.name) and not d.is_symlink() and (d / "meta.json").exists())
            if not gone:
                return None
            kept_file = gone[-1] / "library.json"
            try:
                kept = json.loads(decode_text(kept_file.read_bytes())) if kept_file.exists() else {}
            except (OSError, ValueError):
                kept = {}
            rename_directory(gone[-1], folder)
            (folder / "library.json").unlink(missing_ok=True)
            return kept if isinstance(kept, dict) else {}

    def purge(self, before):
        from .store import valid_id
        with self._lock:
            root = self._files.path("papers/.trash")
            gone = []
            for folder in sorted(root.iterdir()) if root.is_dir() else []:
                match = re.fullmatch(r"(.+)-(\d{8}-\d{6})", folder.name)
                if not (match and valid_id(match[1]) and folder.is_dir() and not folder.is_symlink()):
                    continue
                try:
                    at = time.mktime(time.strptime(match[2], "%Y%m%d-%H%M%S"))
                except (ValueError, OverflowError):
                    continue
                if at < before:
                    shutil.rmtree(folder, ignore_errors=True)
                    if not folder.exists():
                        gone.append(folder.name)
            return gone

    def _library_revision(self):
        return tuple((name, self._files.path(name).stat().st_mtime_ns, self._files.path(name).stat().st_size)
                     for name in ("library.json", "zotero.json") if self._files.exists(name)) or None

    def get_library(self):
        with self._lock:
            values = [json.loads(decode_text(self._files.read(name))) if self._files.exists(name) else {}
                      for name in ("library.json", "zotero.json")]
            return LibraryState(*values, revision=self._library_revision())

    def save_library(self, state):
        with self._lock:
            if state.revision != self._library_revision():
                raise ConflictError("Library organization changed")
            for name, value in (("library.json", state.catalog), ("zotero.json", state.zotero)):
                if self._files.exists(name) and json.loads(decode_text(self._files.read(name))) == value:
                    continue
                if value or self._files.exists(name):
                    self._files.write(name, json.dumps(value, ensure_ascii=False, indent=1).encode("utf-8"))
            state.revision = self._library_revision()


@dataclass
class _Workspace:
    folder: Path
    base: Document
    writable: bool


class DocumentStore:
    """Documents and an internal bridge for older file-based tools."""
    RECORDS = {"library.json": "catalog", "zotero.json": "zotero"}

    def __init__(self, backend):
        self.backend = backend
        self.cache_token = object()
        self._sessions = threading.local()

    def _active(self):
        if not hasattr(self._sessions, "paths"):
            self._sessions.paths = {}
        return self._sessions.paths

    def get(self, identity):
        checked_id(identity)
        if identity in self._active():
            session = self._active()[identity]
            raw = LegacyLayout.read_folder(session.folder)
            if not raw:
                raise FileNotFoundError(identity)
            return LegacyLayout.decode(identity, raw, revision=session.base.revision)
        return self.backend.get(identity)

    def save(self, document):
        checked_id(document.id)
        if document.id in self._active():
            LegacyLayout.write_folder(self._active()[document.id].folder, LegacyLayout.encode(document))
        else:
            self.backend.save(document)

    def list(self):
        return list(self.backend.list())

    def info(self, identity):
        checked_id(identity)
        if identity in self._active():
            return LocalDirectoryBackend._folder_info(self._active()[identity].folder, identity)
        return self.backend.info(identity)

    @contextlib.contextmanager
    def edit(self, identity, create=False):
        try:
            doc = self.get(identity)
        except FileNotFoundError:
            if not create:
                raise
            doc = Document(checked_id(identity))
        yield doc
        self.save(doc)

    def delete(self, identity):
        checked_id(identity)
        if identity in self._active():
            shutil.rmtree(self._active()[identity].folder, ignore_errors=True)
        else:
            self.backend.delete(identity)

    def revision(self, identity):
        return (self.info(identity).revision,)

    def library(self):
        return self.backend.get_library()

    def save_library(self, state):
        self.backend.save_library(state)

    # Compatibility for old importers only. Plugins don't implement these.
    def has(self, identity, name="meta.json"):
        try:
            doc = self.get(identity)
        except FileNotFoundError:
            return False
        return checked_key(name) in LegacyLayout.encode(doc)

    def read_asset(self, identity, name):
        raw = LegacyLayout.encode(self.get(identity))
        try:
            return raw[checked_key(name)]
        except KeyError:
            raise FileNotFoundError(name) from None

    def write_asset(self, identity, name, data):
        with self.edit(identity, create=True) as doc:
            raw = LegacyLayout.encode(doc)
            raw[checked_key(name)] = data
            updated = LegacyLayout.decode(identity, raw, revision=doc.revision)
            doc.__dict__.update(updated.__dict__)

    def write_json(self, identity, name, value):
        self.write_asset(identity, name, json.dumps(value, ensure_ascii=False, indent=1).encode("utf-8"))

    def checkpoint(self, identity):
        """Publish a completed document stage without closing parser tools.

        Refresh the working copy with merged concurrent edits so the next
        checkpoint uses the committed snapshot as its base.
        """
        if identity not in self._active():
            return True   # edits outside a workspace are already persistent
        session = self._active()[identity]
        if not session.writable:
            return False
        original, current = LegacyLayout.encode(session.base), LegacyLayout.read_folder(session.folder)
        if current != original:
            saved = self._publish(identity, session.base, original, current)
            base = saved if saved is not None else Document(identity)
            LegacyLayout.write_folder(session.folder, LegacyLayout.encode(base))
            session.base = base
        return True

    @contextlib.contextmanager
    def workspace(self, identity, write=True):
        """Parser bridge: materialize a document and merge completed changes.

        This is application code, never a plugin requirement. Model replies
        survive failed generations; conflicting concurrent edits are rejected.
        """
        checked_id(identity)
        if identity in self._active():
            yield self._active()[identity].folder
            return
        try:
            base = self.backend.get(identity)
        except FileNotFoundError:
            base = Document(identity)
        original = LegacyLayout.encode(base)
        with tempfile.TemporaryDirectory(prefix="textfold-document-") as temp:
            folder = Path(temp) / identity
            LegacyLayout.write_folder(folder, original)
            self._active()[identity] = _Workspace(folder, base, write)
            try:
                yield folder
            finally:
                session = self._active().pop(identity)
                if session.writable:
                    original = LegacyLayout.encode(session.base)
                    current = LegacyLayout.read_folder(folder)
                    if current != original:
                        self._publish(identity, session.base, original, current)

    def _publish(self, identity, base, original, current):
        try:
            latest = self.backend.get(identity)
        except FileNotFoundError:
            if base.revision is not None:
                raise ConflictError(f"Document was removed: {identity}") from None
            latest = Document(identity)
        newer = LegacyLayout.encode(latest)
        changed = {k for k in original.keys() | current.keys() if original.get(k) != current.get(k)}
        for key in changed:
            if newer.get(key) != original.get(key) and newer.get(key) != current.get(key):
                raise ConflictError(f"Concurrent document edit: {identity}")
        for key in changed:
            if key in current:
                newer[key] = current[key]
            else:
                newer.pop(key, None)
        if newer:
            saved = LegacyLayout.decode(identity, newer, revision=latest.revision)
            self.backend.save(saved)
            return saved
        elif latest.revision is not None:
            self.backend.delete(identity)

    def local_path(self, identity):
        checked_id(identity)
        if identity in self._active():
            return self._active()[identity].folder
        if isinstance(self.backend, LocalDirectoryBackend):
            return self.backend._path(identity)
        raise RuntimeError("A local document path requires a document workspace")
