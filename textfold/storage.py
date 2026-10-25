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

from .documents import ConflictError, Document, DocumentInfo, LibraryState
from ._storage_layout import DirectoryFiles, LegacyLayout, checked_id, checked_key, decode_text, rename_directory


def document_info(doc):
    from . import langs, store
    base = (doc.layers.get("en") or {}).get("lang")
    if base not in langs.LANGS:
        base = store._detect(doc.source.html or "")
    translated = sorted(k[3:] for k in doc.layers if k.startswith("tr-"))
    languages = list(dict.fromkeys(([base] if "en" in doc.layers else []) + translated))
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
            for attempt in range(4):
                before = self._revision(self._entries(document_id))
                if before is None:
                    raise FileNotFoundError(document_id)
                raw = self._read_legacy(self._path(document_id))
                entries = self._entries(document_id)
                if before == self._revision(entries):
                    return LegacyLayout.decode(document_id, raw, revision=before, entries=entries)
            raise ConflictError(f"Document changed while reading: {document_id}")

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
            # A matching revision makes the detached codec snapshot a valid
            # base. Reuse unchanged bytes; only changed files reach the disk.
            old = document._legacy if document._legacy else LegacyLayout.read_folder(folder)
            files = DirectoryFiles(folder)
            for key in old.keys() - raw.keys():
                path = files.path(key)
                path.unlink(missing_ok=True)
                parent = path.parent
                while parent != folder:
                    try: parent.rmdir()
                    except OSError: break
                    parent = parent.parent
            for key, value in raw.items():
                if old.get(key) != value:
                    files.write(key, value)
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

    def prepare_import(self, document_id):
        checked_id(document_id)  # Deleted local folders already leave no index.

    def delete(self, document_id):
        with self._lock:
            path = self._path(document_id)
            if not path.exists():
                raise FileNotFoundError(document_id)
            shutil.rmtree(path)

    def trash(self, document_id, placement=None):
        # The paper's own folder moves, by its name: a link to a folder kept elsewhere goes to the trash as a link
        with self._lock:
            folder = self.root / "papers" / checked_id(document_id)
            target = self.root / "papers" / ".trash" / f"{document_id}-{time.strftime('%Y%m%d-%H%M%S')}"
            target.parent.mkdir(parents=True, exist_ok=True)
            rename_directory(folder, target)
            if placement:
                DirectoryFiles(target).write("library.json", json.dumps(placement, ensure_ascii=False).encode("utf-8"))
            return str(target)

    def _trash_revision(self, document_id):
        root = self.root / "papers" / ".trash"
        entries = []
        for folder in sorted(root.glob(f"{checked_id(document_id)}-????????-??????")):
            # Metadata only. Include every retained incarnation, so a stale
            # confirmation cannot erase a newly deleted copy of the same ID.
            entries.append((folder.name, folder.lstat().st_mtime_ns))
            if not folder.is_symlink() and not getattr(folder, "is_junction", lambda: False)():
                entries.extend((str(p.relative_to(root)), p.stat().st_mtime_ns, p.stat().st_size)
                               for p in sorted(folder.rglob("*")) if p.is_file() and not p.is_symlink())
        return hashlib.sha256(repr(entries).encode()).hexdigest()

    def _check_trash(self, document_id, revision):
        if revision is not None and revision != self._trash_revision(document_id):
            raise ConflictError("Trash changed; refresh before trying again")

    def restore(self, document_id, revision=None):
        from .store import valid_id
        if not valid_id(document_id):
            return None
        with self._lock:
            self._check_trash(document_id, revision)
            folder = self.root / "papers" / document_id
            if folder.exists() or folder.is_symlink() or getattr(folder, "is_junction", lambda: False)():
                return None
            pattern = re.compile(re.escape(document_id) + r"-\d{8}-\d{6}")
            gone = sorted(d for d in (self.root / "papers" / ".trash").glob(f"{document_id}-*")
                          if pattern.fullmatch(d.name) and (d / "meta.json").exists())   # (a linked paper comes back as its link)
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

    def list_trash(self):
        """The latest retained copy of each document, without opening its payloads."""
        from .store import valid_id
        with self._lock:
            root = self.root / "papers" / ".trash"
            rows = {}
            for folder in sorted(root.iterdir()) if root.is_dir() else []:
                match = re.fullmatch(r"(.+)-(\d{8}-\d{6})", folder.name)
                if not (match and valid_id(match[1]) and folder.is_dir()):
                    continue
                try:
                    deleted = time.mktime(time.strptime(match[2], "%Y%m%d-%H%M%S"))
                    meta = json.loads(decode_text((folder / "meta.json").read_bytes()))
                    if not isinstance(meta, dict):
                        continue
                except (OSError, ValueError, OverflowError):
                    continue
                identity = match[1]
                rows[identity] = {"id": identity, "title": meta.get("title") or identity,
                                  "deleted_at": deleted, "restorable": not (self.root / "papers" / identity).exists(),
                                  "revision": self._trash_revision(identity), "can_delete": True}
            return sorted(rows.values(), key=lambda row: row["deleted_at"], reverse=True)

    def delete_trashed(self, document_id, revision=None):
        """Erase retained copies only; a linked original and an active copy stay intact."""
        from .store import valid_id
        if not valid_id(document_id):
            raise ValueError("Invalid document id")
        with self._lock:
            self._check_trash(document_id, revision)
            root = self.root / "papers" / ".trash"
            if root.resolve().parent != (self.root / "papers").resolve():
                raise ValueError("Trash folder escapes the library")
            pattern = re.compile(re.escape(document_id) + r"-\d{8}-\d{6}")
            removed = False
            for folder in list(root.iterdir()) if root.is_dir() else []:
                if not pattern.fullmatch(folder.name):
                    continue
                try:
                    time.strptime(folder.name[-15:], "%Y%m%d-%H%M%S")
                except ValueError:
                    continue
                if folder.is_symlink():
                    folder.unlink()
                elif getattr(folder, "is_junction", lambda: False)():
                    os.rmdir(folder)   # remove the junction, never traverse its target
                elif folder.is_dir():
                    if folder.resolve().parent != root.resolve():
                        raise ValueError("Trash entry escapes its folder")
                    shutil.rmtree(folder)
                else:
                    continue
                removed = True
            return removed

    def purge(self, before):
        from .store import valid_id
        with self._lock:
            root = self.root / "papers" / ".trash"
            gone = []
            if root.resolve().parent != (self.root / "papers").resolve():
                return gone
            for folder in sorted(root.iterdir()) if root.is_dir() else []:
                match = re.fullmatch(r"(.+)-(\d{8}-\d{6})", folder.name)
                if not (match and valid_id(match[1]) and folder.is_dir() and not folder.is_symlink() and
                        not getattr(folder, "is_junction", lambda: False)()):
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

    def list_trash(self):
        return self.backend.list_trash()

    def prepare_import(self, document_id):
        """Explicit user import/generation; never used by ordinary saves/sync."""
        checked_id(document_id)
        prepare = getattr(self.backend, "prepare_import", None)
        if prepare:
            prepare(document_id)

    def restore(self, document_id, revision=None):
        return self.backend.restore(document_id) if revision is None else self.backend.restore(document_id, revision)

    def delete_trashed(self, document_id, revision=None):
        return self.backend.delete_trashed(document_id, revision)

    def _active(self):
        if not hasattr(self._sessions, "paths"):
            self._sessions.paths = {}
        return self._sessions.paths

    def in_workspace(self, identity):
        return identity in self._active()

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

    # Compatibility for old importers only. A backend doesn't implement these.
    def has(self, identity, name="meta.json"):
        if identity in self._active():
            return DirectoryFiles(self._active()[identity].folder).exists(name)
        if isinstance(self.backend, LocalDirectoryBackend) and identity not in self._active():
            return DirectoryFiles(self.backend._path(identity)).exists(name)
        try:
            doc = self.get(identity)
        except FileNotFoundError:
            return False
        return checked_key(name) in LegacyLayout.encode(doc)

    def read_asset(self, identity, name):
        if identity in self._active():
            return DirectoryFiles(self._active()[identity].folder).read(name)
        if isinstance(self.backend, LocalDirectoryBackend) and identity not in self._active():
            return DirectoryFiles(self.backend._path(identity)).read(name)
        raw = LegacyLayout.encode(self.get(identity))
        try:
            return raw[checked_key(name)]
        except KeyError:
            raise FileNotFoundError(name) from None

    def write_asset(self, identity, name, data):
        if identity in self._active():
            DirectoryFiles(self._active()[identity].folder).write(checked_key(name), data)
            return
        if isinstance(self.backend, LocalDirectoryBackend) and identity not in self._active():
            with self.backend._lock:
                DirectoryFiles(self.backend.root / 'papers' / checked_id(identity)).write(checked_key(name), data)
            return
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
            # Refresh only concurrent changes; large originals already on disk
            # need neither a second read nor another copy at every checkpoint.
            updated = LegacyLayout.encode(base)
            files = DirectoryFiles(session.folder)
            for key in current.keys() - updated.keys():
                files.path(key).unlink()
            for key, value in updated.items():
                if current.get(key) != value:
                    files.write(key, value)
            session.base = base
        return True

    @contextlib.contextmanager
    def workspace(self, identity, write=True):
        checked_id(identity)
        defer = getattr(self.backend, "defer_sync", None)
        outer = write and identity not in self._active() and defer is not None
        with defer(identity) if outer else contextlib.nullcontext():
            with self._workspace(identity, write) as folder:
                yield folder

    @contextlib.contextmanager
    def _workspace(self, identity, write=True):
        """Parser bridge: materialize a document and merge completed changes.

        This is application code, never something a backend provides. Model replies
        survive failed generations; conflicting concurrent edits are rejected.
        """
        checked_id(identity)
        if identity in self._active():
            yield self._active()[identity].folder
            return
        if isinstance(self.backend, LocalDirectoryBackend) and write:
            # Local generators write each paid reply to its durable folder as
            # it arrives. SIGTERM does not need a finally/checkpoint to save it.
            yield self.backend._path(identity)
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
            return self.backend.root / "papers" / identity
        raise RuntimeError("A local document path requires a document workspace")
