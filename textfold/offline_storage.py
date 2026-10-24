"""Cloud authority with a durable SQLite working copy, outbox and safe cache."""
import copy
import hashlib
import io
import json
import threading
import time
import uuid
import zipfile

from .cloud_storage import StorageError, StorageAuthenticationError, StorageIdentityError
from .change_notifications import ChangeNotifications
from .sync_schedule import SyncSchedule
from .documents import ConflictError
from .sqlite_storage import SQLiteBackend, LIBRARY
from . import sync_records as records


def blob_refs(value):
    refs = {}
    def visit(item):
        if isinstance(item, dict):
            if set(item) == {"sha256", "size"} and isinstance(item["sha256"], str) and len(item["sha256"]) == 64 and all(c in "0123456789abcdef" for c in item["sha256"]) and type(item["size"]) is int and 0 <= item["size"] <= 100 * 1024 * 1024:
                refs[item["sha256"]] = item["size"]
            else:
                for part in item.values():
                    visit(part)
        elif isinstance(item, list):
            for part in item:
                visit(part)
    visit(value)
    return refs


class OfflineBackend:
    name = "cloud"

    def __init__(self, root, remote, *, clock=time.monotonic, jitter=None):
        self.cache = SQLiteBackend(root, tracking=True)
        self.remote = remote
        self.url = remote.url
        self.configuration_revision = getattr(remote, "configuration_revision", None)
        self.configuration_error = getattr(remote, "configuration_error", "")
        self._sync_lock = threading.Lock()
        self._remote_lock = threading.RLock()  # snapshot fetch + installation stay ordered
        options = {'clock': clock, **({'jitter': jitter} if jitter is not None else {})}
        self._schedule = SyncSchedule(**options)
        self._notifications = ChangeNotifications(remote, self._notification, **options)
        self._lifecycle = threading.Lock()
        self._closed = False
        self._cycle_changed = False
        self._sync_enabled = False
        self._blocked = False
        self._thread = None
        self._online, self._error, self._last_sync = False, "", 0
        self._import = None
        self._job = None

    def start(self):
        with self._lifecycle:
            if self._closed or self._thread and self._thread.is_alive():
                return
            self._notifications.start()
            self._thread = threading.Thread(target=self._run, name="textfold-storage-sync", daemon=True)
            self._thread.start()

    def close(self):
        with self._lifecycle:
            self._closed = True
            self._schedule.close()
        self._notifications.close()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(12)

    def _run(self):
        while not self._closed:
            generation = self._schedule.take()
            if generation is None:
                self._schedule.wait()
                continue
            state = self.sync()
            self._schedule.finish(generation, error=bool(state['error']), blocked=self._blocked,
                                  healthy=self._sync_enabled and self._notifications.status()['state'] == 'connected',
                                  changed=self._cycle_changed, pending=bool(state['pending']))

    def request_sync(self, reason='local'):
        self._schedule.request(reason, healthy=self._notifications.status()['state'] == 'connected')

    def _notification(self, reason, cursor):
        if self._closed:
            return
        if reason == 'changed':
            with self.cache.transaction() as db:
                stored = db.execute("SELECT value FROM sync_state WHERE key='cursor'").fetchone()
            if stored and cursor <= int(stored[0]):
                return
        self.request_sync(reason)

    def status(self):
        return {**self.cache.status(), "online": self._online, "syncing": self._sync_lock.locked(),
                "error": self._error or self.configuration_error, "lastSync": self._last_sync,
                "import": self._import, "job": self._job, "notifications": self._notifications.status()}

    def background(self, action, source=None):
        if not self._sync_lock.acquire(blocking=False):
            raise ValueError("Storage is busy; wait for the current synchronization")
        self._job = {"action": action, "done": 0, "total": 0, "error": ""}
        def run():
            self._remote_lock.acquire()
            try:
                self._health()
                self._index(self.remote.sync_index())
                if action == "import":
                    for info in source.list():
                        with self.cache.transaction() as db:
                            row = db.execute("SELECT loaded,state FROM documents WHERE id=?", (info.id,)).fetchone()
                        if row and not row[0] and row[1] == "active":
                            self.get(info.id)
                    self._import = self.cache.import_library(source, force=True)
                elif action == "download":
                    indexed = self.cache.list()
                    self._job["total"] = len(indexed)
                    for info in indexed:
                        self.get(info.id)
                        self._job["done"] += 1
                else:
                    raise ValueError("Unknown storage action")
            except (OSError, ValueError, ConflictError) as error:
                self._job["error"] = self._error = str(error)
            finally:
                self._remote_lock.release()
                self._sync_lock.release()
                self.request_sync()
        threading.Thread(target=run, name="textfold-storage-" + action, daemon=True).start()
        return self.status()

    def export_pending(self):
        """Explicit recovery export; contains no endpoint credentials."""
        output = io.BytesIO()
        with self.cache.transaction() as db, zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            data, refs = [], {}
            for row in db.execute("SELECT d.* FROM documents d WHERE d.id IN (SELECT document_id FROM pending_changes UNION SELECT document_id FROM records WHERE record_key LIKE 'conflict/%' AND deleted=0) ORDER BY d.id"):
                values = self.cache.values(db, row["id"])
                data.append({"document": row["id"], "state": row["state"], "records": values,
                             "base_revision": row["remote_revision"], "placement": json.loads(row["placement"])})
                refs.update(blob_refs(values))
            for row in db.execute("SELECT payload,target_records FROM pending_requests"):
                data.append({"uncertain_request": json.loads(row[0]), "target_records": json.loads(row[1])})
                refs.update(blob_refs(json.loads(row[1])))
            archive.writestr("pending.json", records.canonical({"format": "textfold/pending-v1", "documents": data}))
            for digest in refs:
                archive.write(self.cache.blobs / digest, "blobs/" + digest)
        return output.getvalue()

    def _health(self):
        health = self.remote.sync_health()
        with self.cache.transaction() as db:
            row = db.execute("SELECT value FROM sync_state WHERE key='library_id'").fetchone()
            if row and row[0] != health["library_id"]:
                raise StorageIdentityError("Cloud library identity changed; keep this workspace and select a new cache folder")
            cursor = db.execute("SELECT value FROM sync_state WHERE key='cursor'").fetchone()
            if cursor and int(cursor[0]) > health.get("cursor", 0):
                raise StorageIdentityError("Cloud history moved backwards; keep this workspace and select a new cache folder for recovery")
            db.execute("INSERT OR IGNORE INTO sync_state VALUES ('library_id',?)", (health["library_id"],))
            if health.get('sync_enabled') is not True:
                db.execute("DELETE FROM sync_state WHERE key='v2_baseline'")
        self._sync_enabled = health.get('sync_enabled') is True
        self._notifications.configure(health)
        return health

    def _bytes(self, values):
        result = {}
        for digest, size in blob_refs(values).items():
            path = self.cache.blobs / digest
            if path.exists() and path.stat().st_size == size and hashlib.sha256(path.read_bytes()).hexdigest() == digest:
                continue
            raw = self.remote._download(digest, size)
            if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
                raise StorageError("Remote content failed checksum verification")
            result[digest] = raw
        return result

    def _snapshot(self, identity):
        try:
            return self.remote.sync_snapshot(identity)
        except FileNotFoundError:
            return {"records": {}, "revision": None, "state": "active", "placement": {}, "info": {}}

    def _accept(self, identity, snapshot, *, captured=None, payload=None):
        """Install a cloud acknowledgement without clearing edits made in flight."""
        values = snapshot["records"]
        blobs = self._bytes(values)
        with self.cache.transaction() as db:
            self.cache._blobs(blobs)
            row = db.execute("SELECT * FROM documents WHERE id=?", (identity,)).fetchone()
            pending = db.execute("SELECT * FROM pending_changes WHERE document_id=?", (identity,)).fetchone()
            current = self.cache.values(db, identity)
            state, placement = snapshot["state"], snapshot.get("placement", {})
            merged = values
            if row and pending:
                base = json.loads(captured["local_records"]) if captured else json.loads(row["remote_records"])
                merged, _ = records.reconcile(base, current, values)
                if captured:
                    if row["state"] != captured["local_state"] or (payload and json.loads(row["placement"]) != payload["placement"]):
                        state, placement = row["state"], json.loads(row["placement"])
                elif row["state"] != row["remote_state"]:
                    state, placement = row["state"], json.loads(row["placement"])
            dirty = merged != values or state != snapshot["state"] or placement != snapshot.get("placement", {})
            self.cache.write(db, identity, merged, state=state, placement=placement, dirty=dirty,
                             created=snapshot.get("info", {}).get("created"))
            db.execute("UPDATE documents SET remote_revision=?,remote_records=?,remote_state=? WHERE id=?",
                       (snapshot["revision"], records.canonical(values), snapshot["state"], identity))
            if not dirty:
                db.execute("DELETE FROM pending_changes WHERE document_id=?", (identity,))
            if captured:
                db.execute("DELETE FROM pending_requests WHERE document_id=? AND operation_id=?", (identity, captured["operation_id"]))

    def _index(self, entries, *, complete=False):
        present = {entry["id"] for entry in entries} if complete else set()
        for entry in entries:
            identity = entry["id"]
            with self.cache.transaction() as db:
                row = db.execute("SELECT * FROM documents WHERE id=?", (identity,)).fetchone()
                pending = db.execute("SELECT 1 FROM pending_changes WHERE document_id=?", (identity,)).fetchone()
                if pending:
                    continue
                if row and row["loaded"]:
                    refresh = row["remote_revision"] != entry["revision"]
                else:
                    revision = str(uuid.uuid4())
                    info = {**entry["info"], "revision": revision}
                    db.execute("INSERT INTO documents(id,revision,created,updated,info_json,state,loaded,remote_revision,remote_state) VALUES (?,?,?,?,?,?,0,?,?) "
                               "ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,info_json=excluded.info_json,state=excluded.state,remote_revision=excluded.remote_revision,remote_state=excluded.remote_state",
                               (identity, revision, info.get("created", 0), info.get("updated", 0), records.canonical(info), entry["state"], entry["revision"], entry["state"]))
                    refresh = False
            if refresh:
                self._accept(identity, self._snapshot(identity))
        if complete:
            # Legacy clients had no change feed/tombstones. Until their writes
            # are fenced, a complete scan also recognizes disappeared entries.
            with self.cache.transaction() as db:
                for row in db.execute("SELECT id FROM documents WHERE id!=? AND remote_revision IS NOT NULL AND state='active' AND id NOT IN (SELECT document_id FROM pending_changes)", (LIBRARY,)).fetchall():
                    if row[0] not in present:
                        db.execute("UPDATE documents SET state='deleted',remote_state='deleted' WHERE id=?", (row[0],))

    def _upload(self, values):
        for digest in blob_refs(values):
            try:
                self.remote._request("HEAD", "blobs/" + digest)
            except FileNotFoundError:
                raw = self.cache._download(digest, 0)
                self.remote._request("PUT", "blobs/" + digest, raw, binary=True)

    def _send(self, request):
        payload, values = json.loads(request["payload"]), json.loads(request["target_records"])
        self._upload(values)
        reply = self.remote.sync_commit(request["document_id"], payload)
        snapshot = {"records": values, "revision": reply["revision"], "state": reply["state"],
                    "placement": payload["placement"], "info": reply["info"]}
        self._accept(request["document_id"], snapshot, captured=request, payload=payload)

    def _push(self, identity):
        # Retry an uncertain write byte-for-byte before forming another request.
        with self.cache.transaction() as db:
            request = db.execute("SELECT * FROM pending_requests WHERE document_id=?", (identity,)).fetchone()
        if request:
            try:
                self._send(request)
                return
            except ConflictError:
                with self.cache.transaction() as db:
                    db.execute("DELETE FROM pending_requests WHERE document_id=?", (identity,))
        for _ in range(4):
            snapshot = self._snapshot(identity)
            with self.cache.transaction() as db:
                row = db.execute("SELECT * FROM documents WHERE id=?", (identity,)).fetchone()
                pending = db.execute("SELECT * FROM pending_changes WHERE document_id=?", (identity,)).fetchone()
                if not row or not pending:
                    return
                local = self.cache.values(db, identity)
                base = json.loads(row["remote_records"])
                merged, _ = records.reconcile(base, local, snapshot["records"])
                state, placement = row["state"], json.loads(row["placement"])
                remote_state = snapshot["state"]
                if snapshot["revision"] is None and row["remote_revision"] is not None:
                    raise StorageError("Previously synchronized document disappeared; restore or export local changes before retrying")
                if row["remote_state"] == "active":
                    edited = None
                    if remote_state != "active" and state == "active":
                        state, placement = remote_state, snapshot.get("placement", {})
                        edited = local
                    elif state != "active" and remote_state == "active":
                        edited = snapshot["records"]
                    if edited is not None and edited != base:
                        conflict = {"key": "document-state", "base": row["remote_state"], "variants": ["active", state],
                                    "deleted": True, "changes": records.changes(base, edited)}
                        conflict["id"] = "c-" + records.digest(conflict)[:32]
                        merged["conflict/" + conflict["id"]] = conflict
                if identity != LIBRARY and not records.original_unchanged(snapshot["records"], merged):
                    raise StorageError("Original source differs; import this content as a new document")
                delta = records.changes(snapshot["records"], merged)
                payload = {"operation_id": str(uuid.uuid4()), "revision": snapshot["revision"], "changes": delta,
                           "state": state, "placement": placement, "created": row["created"]}
                if not delta and state == remote_state and placement == snapshot.get("placement", {}):
                    request = {"local_records": records.canonical(local), "local_state": row["state"], "operation_id": "", "document_id": identity}
                else:
                    db.execute("INSERT OR REPLACE INTO pending_requests VALUES (?,?,?,?,?,?,?)",
                               (identity, payload["operation_id"], records.canonical(payload), records.canonical(local), pending["generation"], records.canonical(merged), row["state"]))
                    request = db.execute("SELECT * FROM pending_requests WHERE document_id=?", (identity,)).fetchone()
            if not delta and state == remote_state and placement == snapshot.get("placement", {}):
                self._accept(identity, snapshot, captured=request, payload=payload)
                return
            try:
                self._send(request)
                return
            except ConflictError:
                with self.cache.transaction() as db:
                    db.execute("DELETE FROM pending_requests WHERE document_id=?", (identity,))
        raise ConflictError("Cloud kept changing; local edits remain pending")

    def sync(self):
        if self._closed or not self._sync_lock.acquire(blocking=False):
            return self.status()
        self._remote_lock.acquire()
        try:
            health = self._health()
            self._online, self._error, self._blocked = True, "", False
            with self.cache.transaction() as db:
                cursor = db.execute("SELECT value FROM sync_state WHERE key='cursor'").fetchone()
                pending = [row[0] for row in db.execute("SELECT document_id FROM pending_changes UNION SELECT document_id FROM pending_requests")]
                library_ready = db.execute("SELECT 1 FROM documents WHERE id=? AND loaded=1", (LIBRARY,)).fetchone()
                baseline = db.execute("SELECT 1 FROM sync_state WHERE key='v2_baseline' AND value='complete'").fetchone()
            # Legacy writes have no feed entries. A loaded library/cursor from
            # an older build is not proof of a baseline after the v2 fence.
            refresh_baseline = self._sync_enabled and not baseline
            self._cycle_changed = bool(pending) or cursor is None or int(cursor[0]) != health['cursor']
            if self._sync_enabled and baseline and cursor is not None and int(cursor[0]) == health['cursor'] and not pending and library_ready:
                self._last_sync = time.time()
                return self.status()
            if cursor is None:
                self._index(self.remote.sync_index(), complete=True)
                # Capture before indexing: commits racing with the initial scan
                # will still be replayed from the change feed.
                with self.cache.transaction() as db:
                    db.execute("INSERT INTO sync_state VALUES ('cursor',?)", (str(health["cursor"]),))
            elif health.get("sync_enabled") is False or refresh_baseline:
                self._index(self.remote.sync_index(), complete=True)  # compatibility until the first v2 commit fences v1 writers
            self._pull()
            failures = []
            for identity in pending:
                try:
                    self._push(identity)
                except (OSError, ValueError, ConflictError) as error:
                    failures.append(str(error))
                    self._blocked = self._blocked or isinstance(error, (StorageAuthenticationError, StorageIdentityError))
            with self.cache.transaction() as db:
                dirty_library = db.execute("SELECT 1 FROM pending_changes WHERE document_id=?", (LIBRARY,)).fetchone()
            if not dirty_library and (not self._sync_enabled or not library_ready or refresh_baseline):
                library = self._snapshot(LIBRARY)
                with self.cache.transaction() as db:
                    old = db.execute("SELECT remote_revision FROM documents WHERE id=?", (LIBRARY,)).fetchone()
                if not library_ready or not old or old[0] != library["revision"]:
                    self._accept(LIBRARY, library)
                if refresh_baseline:
                    # Set only after index/feed and the post-fence library read
                    # succeed. An interruption retries; no existing data resets.
                    with self.cache.transaction() as db:
                        db.execute("INSERT OR REPLACE INTO sync_state VALUES ('v2_baseline','complete')")
            if failures:
                self._error = failures[0]
            else:
                if health.get("sync_enabled") is False:
                    self._index(self.remote.sync_index(), complete=True)
                if pending or not self._sync_enabled:
                    self._pull()
                self._last_sync = time.time()
        except (OSError, ValueError, ConflictError) as error:
            self._online, self._error = False, str(error)
            self._blocked = isinstance(error, (StorageAuthenticationError, StorageIdentityError))
            self._notifications.suspend()
        finally:
            self._remote_lock.release()
            self._sync_lock.release()
        return self.status()

    def _pull(self):
        while True:
            with self.cache.transaction() as db:
                cursor = int(db.execute("SELECT value FROM sync_state WHERE key='cursor'").fetchone()[0])
            page = self.remote.sync_changes(cursor)
            following = page.get("cursor")
            if type(following) is not int or following < cursor or (page.get("more") and following == cursor):
                raise StorageError("Invalid synchronization cursor")
            for change in page["changes"]:
                identity = change["document_id"]
                with self.cache.transaction() as db:
                    pending = db.execute("SELECT 1 FROM pending_changes WHERE document_id=?", (identity,)).fetchone()
                if pending:
                    # Its durable outbox will reconcile against the latest
                    # snapshot before upload; advancing the cursor loses no edit.
                    continue
                snapshot = self._snapshot(identity)
                if identity == LIBRARY:
                    self._accept(identity, snapshot)
                else:
                    with self.cache.transaction() as db:
                        loaded = db.execute("SELECT loaded FROM documents WHERE id=?", (identity,)).fetchone()
                    if loaded and loaded[0]:
                        self._accept(identity, snapshot)
                    else:
                        self._index([{"id": identity, "revision": snapshot["revision"], "info": snapshot["info"], "state": snapshot["state"]}])
            # An interrupted page can be replayed; never acknowledge unapplied
            # events before their local transactions have committed.
            with self.cache.transaction() as db:
                db.execute("UPDATE sync_state SET value=? WHERE key='cursor'", (str(following),))
            if not page.get("more"):
                return

    def get(self, identity):
        try:
            return self.cache.get(identity)
        except FileNotFoundError:
            with self._remote_lock:
                # Another reader/sync may have populated it while we waited.
                try:
                    return self.cache.get(identity)
                except FileNotFoundError:
                    pass
                with self.cache.transaction() as db:
                    row = db.execute("SELECT state,loaded FROM documents WHERE id=?", (identity,)).fetchone()
                if row and row["state"] != "active":
                    raise FileNotFoundError(identity)
                try:
                    self._health()
                    snapshot = self._snapshot(identity)
                    if snapshot["revision"] is None:
                        raise FileNotFoundError(identity)
                    self._accept(identity, snapshot)
                    return self.cache.get(identity)
                except StorageError:
                    if row is None:
                        # A new import can be created offline. Publication still
                        # reconciles with the cloud and checks immutable source.
                        raise FileNotFoundError(identity) from None
                    raise StorageError("This document is not downloaded; connect to the cloud to open it") from None

    def info(self, identity):
        return self.cache.info(identity)

    def list(self):
        return self.cache.list()

    def save(self, document):
        self.cache.save(document)
        self.request_sync()

    def delete(self, identity):
        self.get(identity)
        self.cache.delete(identity)
        self.request_sync()

    def trash(self, identity, placement=None):
        self.get(identity)
        token = self.cache.trash(identity, placement)
        self.request_sync()
        return token

    def restore(self, identity):
        with self._remote_lock:
            with self.cache.transaction() as db:
                row = db.execute("SELECT loaded FROM documents WHERE id=?", (identity,)).fetchone()
            if row and not row[0]:
                self._health()
                self._accept(identity, self._snapshot(identity))
            placement = self.cache.restore(identity)
        self.request_sync()
        return placement

    def purge(self, before):
        result = self.cache.purge(before)
        self.request_sync()
        return result

    def get_library(self):
        return self.cache.get_library()

    def save_library(self, state):
        self.cache.save_library(state)
        self.request_sync()

    def conflicts(self):
        with self.cache.transaction() as db:
            return [{"document": row["document_id"], "title": (json.loads(row["info_json"]).get("meta") or {}).get("title", ""), **json.loads(row["value_json"])} for row in db.execute(
                "SELECT r.document_id,r.value_json,d.info_json FROM records r JOIN documents d ON d.id=r.document_id WHERE r.deleted=0 AND r.record_key LIKE 'conflict/%' ORDER BY r.document_id,r.record_key")]

    def resolve(self, identity, conflict_id, choice, text=None):
        with self.cache.transaction() as db:
            values = self.cache.values(db, identity)
            key = "conflict/" + conflict_id
            conflict = values.get(key)
            if conflict is None:
                raise FileNotFoundError(conflict_id)
            row = db.execute("SELECT * FROM documents WHERE id=?", (identity,)).fetchone()
            state = row["state"]
            if conflict["key"] == "document-state":
                if choice == "restore":
                    state = "active"
                elif choice != "delete":
                    raise ValueError("Choose restore or delete")
                values.pop(key)
            else:
                values = records.resolve_conflict(values, conflict_id, choice, text)
            self.cache.write(db, identity, values, state=state, placement=json.loads(row["placement"]), dirty=True)
        self.request_sync()

    def clear_cache(self):
        with self.cache.transaction() as db:
            rows = db.execute("SELECT id FROM documents WHERE id!=? AND loaded=1 AND id NOT IN (SELECT document_id FROM pending_changes) AND id NOT IN (SELECT document_id FROM pending_requests) AND id NOT IN (SELECT document_id FROM records WHERE record_key LIKE 'conflict/%' AND deleted=0)", (LIBRARY,)).fetchall()
            for row in rows:
                db.execute("DELETE FROM records WHERE document_id=?", (row[0],))
                db.execute("UPDATE documents SET loaded=0,remote_records='{}' WHERE id=?", (row[0],))
            refs = set()
            for row in db.execute("SELECT value_json FROM records UNION ALL SELECT remote_records FROM documents UNION ALL SELECT target_records FROM pending_requests UNION ALL SELECT local_records FROM pending_requests"):
                refs.update(blob_refs(json.loads(row[0])))
            for path in self.cache.blobs.iterdir():
                if len(path.name) == 64 and all(c in '0123456789abcdef' for c in path.name) and path.name not in refs:
                    path.unlink(missing_ok=True)
            count = len(rows)
        return {"cleared": count, **self.cache.status()}
