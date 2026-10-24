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

from .cloud_storage import _pack, _unpack
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
                "SELECT info_json FROM documents WHERE state='active' AND id!=? AND json_type(info_json,'$.meta')!='null' ORDER BY id", (LIBRARY,))]

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

    def restore(self, identity):
        self._identity(identity)
        with self.transaction() as db:
            row = db.execute("SELECT * FROM documents WHERE id=?", (identity,)).fetchone()
            if not row or row["state"] != "trash":
                return None
            placement = json.loads(row["placement"])
            self.write(db, identity, self.values(db, identity), dirty=True, placement=placement)
            return placement

    def purge(self, before):
        with self.transaction() as db:
            rows = db.execute("SELECT id FROM documents WHERE state='trash' AND updated<?", (before,)).fetchall()
            for row in rows:
                self.write(db, row[0], self.values(db, row[0]), state="deleted", dirty=True)
            return [row[0] for row in rows]

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
