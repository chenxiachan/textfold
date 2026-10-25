"""Transactional document workspaces with record-level storage and a durable outbox."""
import contextlib
import json
import os
import hashlib
import sqlite3
import tempfile
import threading
import time
import uuid
from pathlib import Path

from .document_wire import pack as _pack, unpack as _unpack
from .documents import ConflictError, DocumentInfo
from .storage import document_info
from ._storage_layout import checked_id
from . import sync_records as records
from .sync_records import library_records, library_value

LIBRARY = "@library"
SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
 id TEXT PRIMARY KEY, revision TEXT NOT NULL, created REAL NOT NULL,
 updated REAL NOT NULL, info_json TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'active',
 loaded INTEGER NOT NULL DEFAULT 1, remote_revision TEXT,
 remote_records TEXT NOT NULL DEFAULT '{}', remote_state TEXT NOT NULL DEFAULT 'active',
 placement TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS records (
 document_id TEXT NOT NULL REFERENCES documents(id), record_key TEXT NOT NULL,
 value_json TEXT NOT NULL, deleted INTEGER NOT NULL DEFAULT 0, revision TEXT NOT NULL,
 PRIMARY KEY(document_id, record_key)
);
CREATE VIEW IF NOT EXISTS annotations AS
 SELECT document_id, substr(record_key, 12) AS annotation_id, value_json, deleted, revision
 FROM records WHERE record_key LIKE 'annotation/%';
CREATE VIEW IF NOT EXISTS messages AS
 SELECT document_id, record_key, value_json, deleted, revision
 FROM records WHERE record_key LIKE 'message/%';
CREATE TABLE IF NOT EXISTS pending_changes (
 document_id TEXT PRIMARY KEY REFERENCES documents(id), generation TEXT NOT NULL, modified_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_requests (
 document_id TEXT PRIMARY KEY REFERENCES documents(id), operation_id TEXT NOT NULL,
 payload TEXT NOT NULL, local_records TEXT NOT NULL, generation TEXT NOT NULL,
 target_records TEXT NOT NULL, local_state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS import_journal (source TEXT PRIMARY KEY, completed INTEGER NOT NULL DEFAULT 0);
"""


class SQLiteBackend:
    name = "sqlite"

    def __init__(self, root, *, tracking=False):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.blobs = self.root / "blobs"
        self.blobs.mkdir(exist_ok=True)
        self.database = self.root / "library.sqlite3"
        self.tracking = tracking
        self.lock = threading.RLock()
        with contextlib.closing(self._connect()) as db:
            app_id = db.execute("PRAGMA application_id").fetchone()[0]
            if app_id not in (0, 0x54464C44):
                raise ValueError("This folder contains a database belonging to another application")
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if app_id == 0 and tables:
                raise ValueError("This folder contains an unrecognized database")
            db.execute("PRAGMA application_id=1413893188")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(documents)")}
            for column, definition in (("purged", "INTEGER NOT NULL DEFAULT 0"), ("purge_base", "TEXT"), ("reimport_revision", "TEXT")):
                if column not in columns:
                    db.execute(f"ALTER TABLE documents ADD COLUMN {column} {definition}")
            db.commit()

    def _connect(self):
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA synchronous=FULL")
        return db

    @contextlib.contextmanager
    def transaction(self):
        with self.lock, contextlib.closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _identity(identity):
        checked_id(identity)
        if identity == LIBRARY:
            raise ValueError("Reserved document ID")
        return identity

    @staticmethod
    def values(db, identity):
        return {row["record_key"]: json.loads(row["value_json"])
                for row in db.execute("SELECT record_key,value_json FROM records WHERE document_id=? AND deleted=0", (identity,))}

    def _blobs(self, blobs):
        # Caller holds a write transaction: cache GC in another process cannot
        # remove a just-staged blob before its record references are committed.
        for digest, raw in blobs.items():
            destination = self.blobs / digest
            if destination.exists() and hashlib.sha256(destination.read_bytes()).hexdigest() == digest:
                continue
            fd, temporary = tempfile.mkstemp(dir=self.blobs, suffix=".tmp")
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(raw)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, destination)
            finally:
                Path(temporary).unlink(missing_ok=True)

    def _download(self, digest, size):
        return (self.blobs / digest).read_bytes()

    def _document(self, db, row):
        if row is None or row["state"] != "active" or not row["loaded"]:
            raise FileNotFoundError(row["id"] if row else "document")
        manifest = records.join(row["id"], self.values(db, row["id"]), row["created"], row["updated"])
        document = _unpack(manifest, self._download)
        document.revision = row["revision"]
        return document

    def get(self, identity):
        self._identity(identity)
        with self.transaction() as db:
            return self._document(db, db.execute("SELECT * FROM documents WHERE id=?", (identity,)).fetchone())

    def info(self, identity):
        self._identity(identity)
        with self.transaction() as db:
            row = db.execute("SELECT info_json,state FROM documents WHERE id=?", (identity,)).fetchone()
            if row is None or row["state"] != "active" or json.loads(row["info_json"])["meta"] is None:
                raise FileNotFoundError(identity)
            return DocumentInfo(**json.loads(row["info_json"]))

    def list(self):
        with self.transaction() as db:
            return [DocumentInfo(**json.loads(row[0])) for row in db.execute(
                "SELECT info_json FROM documents WHERE state='active' AND purged=0 AND id!=? AND json_type(info_json,'$.meta')!='null' ORDER BY id", (LIBRARY,))]

    def write(self, db, identity, values, *, state="active", placement=None, dirty=False, created=None):
        row = db.execute("SELECT * FROM documents WHERE id=?", (identity,)).fetchone()
        revision, now = str(uuid.uuid4()), time.time()
        created = row["created"] if row else (created or now)
        if identity == LIBRARY:
            info = {}
        else:
            doc = _unpack(records.join(identity, values, created, now), self._download)
            doc.revision = revision
            info = document_info(doc).__dict__
            if doc.meta is None:
                info["meta"] = None
        db.execute("INSERT INTO documents(id,revision,created,updated,info_json,state,placement) VALUES (?,?,?,?,?,?,?) "
                   "ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,updated=excluded.updated,info_json=excluded.info_json,state=excluded.state,loaded=1,placement=excluded.placement",
                   (identity, revision, created, now, records.canonical(info), state, records.canonical(placement or {})))
        old = self.values(db, identity)
        for change in records.changes(old, values):
            value = old[change["key"]] if change["deleted"] else change["value"]
            db.execute("INSERT INTO records VALUES (?,?,?,?,?) ON CONFLICT(document_id,record_key) DO UPDATE SET value_json=excluded.value_json,deleted=excluded.deleted,revision=excluded.revision",
                       (identity, change["key"], records.canonical(value), int(change["deleted"]), revision))
        if dirty and self.tracking:
            db.execute("INSERT INTO pending_changes VALUES (?,?,?) ON CONFLICT(document_id) DO UPDATE SET generation=excluded.generation,modified_at=excluded.modified_at", (identity, revision, now))
        return revision, created, now

    def save(self, document):
        self._identity(document.id)
        manifest, blobs = _pack(document)
        value = records.split(manifest)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (document.id,)).fetchone()
            if row and row["state"] != "active":
                raise ConflictError("Document was deleted; restore it explicitly before editing")
            if document.revision != (row["revision"] if row and row["state"] == "active" else None):
                raise ConflictError(document.id)
            if row and row["loaded"] and not records.original_unchanged(self.values(db, document.id), value):
                raise ValueError("Original source is immutable; import changed content as a new document")
            self._blobs(blobs)
            document.revision, document.created, document.updated = self.write(db, document.id, value, dirty=True, created=document.created)

    def prepare_import(self, identity):
        self._identity(identity)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (identity,)).fetchone()
            if not row or not row["purged"]:
                return
            protected = db.execute("SELECT 1 FROM pending_changes WHERE document_id=? UNION SELECT 1 FROM pending_requests WHERE document_id=? UNION SELECT 1 FROM records WHERE document_id=? AND record_key LIKE 'conflict/%' AND deleted=0", (identity, identity, identity)).fetchone()
            if protected:
                raise ConflictError("Local recovery data are protected; export them before importing this document again")
            # Explicitly reuse the ID, not the deleted contents. Ordinary save
            # still rejects deleted documents; only this entry point resets it.
            db.execute("UPDATE documents SET created=?,remote_records='{}',remote_state='active',purge_base=NULL,purged=0,reimport_revision=?,remote_revision=NULL WHERE id=?", (time.time(), row["remote_revision"] if self.tracking else None, identity))
            self.write(db, identity, {}, state="active")

    def reconcile(self, base, local, remote):
        """Merge IDs individually; conflict variants retain all unrelated history."""
        key = "extensions/ids.json"
        b, l, r = (mapping.get(key) for mapping in (base, local, remote))
        if not (l != b and r != b and l != r):
            return records.reconcile(base, local, remote)
        try:
            values = [json.loads(self._download(ref["sha256"], ref["size"]).decode("utf-8")) if ref else {} for ref in (b, l, r)]
            if any(not isinstance(v, dict) or v.get("format", "textfold/ids") != "textfold/ids" or v.get("version", 1) != 1 or
                   any(not isinstance(v.get(group, {}), dict) for group in ("units", "sections")) for v in values):
                return records.reconcile(base, local, remote)
        except (OSError, ValueError, KeyError, TypeError):
            return records.reconcile(base, local, remote)
        absent = object()
        def flatten(value):
            out = {(group, identity): item for group in ("units", "sections") for identity, item in value.get(group, {}).items()}
            out.update({("header", name): item for name, item in value.items() if name not in ("units", "sections")})
            return out
        before, left, right = map(flatten, values)
        merged_left, merged_right = {}, {}
        for identity in before.keys() | left.keys() | right.keys():
            bv, lv, rv = (v.get(identity, absent) for v in (before, left, right))
            if lv == bv: lv = rv
            elif rv == bv: rv = lv
            if lv is not absent: merged_left[identity] = lv
            if rv is not absent: merged_right[identity] = rv
        def reference(items):
            value = {"units": {}, "sections": {}}
            for (group, identity), item in items.items():
                if group == "header": value[identity] = item
                else: value[group][identity] = item
            raw = records.canonical(value).encode("utf-8")
            digest = hashlib.sha256(raw).hexdigest()
            self._blobs({digest: raw})
            return {"sha256": digest, "size": len(raw)}
        lref, rref = reference(merged_left), reference(merged_right)
        local, remote, base = dict(local), dict(remote), dict(base)
        local[key], remote[key] = lref, rref
        # _legacy is only lossless byte-format information; the extension is
        # authoritative. Avoid a second opaque conflict for the same history.
        for mapping in (base, local, remote): mapping.pop("legacy/ids.json", None)
        return records.reconcile(base, local, remote)

    def _state(self, identity, state, placement=None):
        self._identity(identity)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (identity,)).fetchone()
            if not row or row["state"] != "active":
                raise FileNotFoundError(identity)
            self.write(db, identity, self.values(db, identity), state=state, placement=placement, dirty=True)

    def delete(self, identity):
        self._state(identity, "deleted")

    def trash(self, identity, placement=None):
        self._state(identity, "trash", placement)
        return identity

    def list_trash(self):
        with self.transaction() as db:
            rows = db.execute("SELECT d.*, EXISTS(SELECT 1 FROM pending_changes p WHERE p.document_id=d.id) OR EXISTS(SELECT 1 FROM pending_requests p WHERE p.document_id=d.id) AS pending FROM documents d WHERE state='trash' OR (purged=1 AND (loaded=1 OR purge_base IS NOT NULL)) ORDER BY updated DESC").fetchall()
            return [{"id": row["id"], "title": (json.loads(row["info_json"]).get("meta") or {}).get("title") or row["id"],
                     "deleted_at": row["updated"], "revision": row["revision"],
                     "restorable": not row["purged"], "can_delete": not row["purged"] and not row["pending"],
                     "pending": bool(row["pending"]), "recovery": bool(row["purged"] and not row["purge_base"])} for row in rows]

    def restore(self, identity, revision=None):
        self._identity(identity)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (identity,)).fetchone()
            if not row or row["state"] != "trash":
                return None
            if revision is not None and revision != row["revision"]:
                raise ConflictError("Trash changed; refresh before trying again")
            if row["purged"]:
                raise ConflictError("This document was permanently deleted")
            placement = json.loads(row["placement"])
            self.write(db, identity, self.values(db, identity), dirty=True, placement=placement)
            return placement

    def delete_trashed(self, identity, revision=None):
        self._identity(identity)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (identity,)).fetchone()
            if not row or row["state"] != "trash":
                return False
            if revision is not None and revision != row["revision"]:
                raise ConflictError("Trash changed; refresh before trying again")
            pending = db.execute("SELECT 1 FROM pending_changes WHERE document_id=? UNION SELECT 1 FROM pending_requests WHERE document_id=?", (identity, identity)).fetchone()
            conflict = db.execute("SELECT 1 FROM records WHERE document_id=? AND record_key LIKE 'conflict/%' AND deleted=0", (identity,)).fetchone()
            if row["purged"] or pending or conflict:
                raise ConflictError("Pending changes or recovery data are protected; export them from storage settings")
            if self.tracking:
                if row["remote_state"] != "trash" or not row["remote_revision"]:
                    raise ConflictError("Synchronize this document before permanently deleting it")
                # Retain bytes and outbox until the cloud acknowledges this
                # exact deletion. A stale confirmation must never be rebased.
                token, now = str(uuid.uuid4()), time.time()
                db.execute("UPDATE documents SET state='deleted',purged=1,purge_base=?,revision=?,updated=? WHERE id=?", (row["remote_revision"], token, now, identity))
                db.execute("INSERT INTO pending_changes VALUES (?,?,?)", (identity, token, now))
            else:
                # Non-tracking workspace, checked above to contain no pending
                # requests, changes or conflicts. Only this trash row is erased.
                db.execute("DELETE FROM records WHERE document_id=?", (identity,))
                db.execute("UPDATE documents SET state='deleted',purged=1,loaded=0,revision=?,updated=? WHERE id=?", (str(uuid.uuid4()), time.time(), identity))
            return True

    def purge(self, before):
        removed = []
        for entry in self.list_trash():
            if entry["deleted_at"] < before and not entry["recovery"]:
                try:
                    if self.delete_trashed(entry["id"], entry["revision"]):
                        removed.append(entry["id"])
                except ConflictError:
                    pass  # Never age out pending edits or unresolved conflicts.
        return removed

    def get_library(self):
        with self.transaction() as db:
            row = db.execute("SELECT revision FROM documents WHERE id=?", (LIBRARY,)).fetchone()
            return library_value(self.values(db, LIBRARY), row[0] if row else None)

    def save_library(self, state):
        with self.transaction() as db:
            row = db.execute("SELECT revision FROM documents WHERE id=?", (LIBRARY,)).fetchone()
            if state.revision != (row[0] if row else None):
                raise ConflictError("library")
            value = library_records(state)
            value.update({k: v for k, v in self.values(db, LIBRARY).items() if k.startswith("conflict/")})
            state.revision, _, _ = self.write(db, LIBRARY, value, dirty=True)

    def status(self):
        with self.transaction() as db:
            pending = db.execute("SELECT COUNT(*) FROM (SELECT document_id FROM pending_changes UNION SELECT document_id FROM pending_requests)").fetchone()[0]
            conflicts = db.execute("SELECT COUNT(*) FROM records WHERE record_key LIKE 'conflict/%' AND deleted=0").fetchone()[0]
            return {"pending": pending, "conflicts": conflicts, "cachePath": str(self.root)}

    def import_library(self, backend, *, force=False):
        """Resumable, verified copy; source bytes and target edits are untouched."""
        source = str(getattr(backend, "root", type(backend).__name__))
        with self.transaction() as db:
            row = db.execute("SELECT completed FROM import_journal WHERE source=?", (source,)).fetchone()
            if row and row[0] and not force:
                return {"imported": 0, "existing": 0, "conflicts": []}
        report = {"imported": 0, "existing": 0, "conflicts": []}
        for info in backend.list():
            incoming = backend.get(info.id)
            manifest, blobs = _pack(incoming)
            values = records.split(manifest)
            with self.transaction() as db:
                row = db.execute("SELECT * FROM documents WHERE id=?", (info.id,)).fetchone()
                if row:
                    old = self.values(db, info.id)
                    if not records.original_unchanged(old, values) or not records.original_unchanged(values, old):
                        report["conflicts"].append(info.id)
                        continue
                    merged, _ = records.reconcile({}, values, old)
                    if merged == old:
                        report["existing"] += 1
                        continue
                    if row["state"] != "active":
                        report["conflicts"].append(info.id)
                        continue
                    values = merged
                self._blobs(blobs)
                self.write(db, info.id, values, dirty=True, created=incoming.created)
                report["imported"] += 1
        incoming = library_records(backend.get_library())
        with self.transaction() as db:
            existing = self.values(db, LIBRARY)
            merged, _ = records.reconcile({}, incoming, existing)
            if merged != existing:
                self.write(db, LIBRARY, merged, dirty=True)
            if not report["conflicts"]:
                db.execute("INSERT OR REPLACE INTO import_journal VALUES (?,1)", (source,))
        return report
