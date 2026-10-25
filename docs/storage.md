# Document storage

The storage backend interface is **document-oriented**. Document ID is the
primary index and the read/save unit. A backend decides whether a document is a
folder, a database record, an archive, or several objects in remote storage.
Callers never supply storage keys, filenames, prefixes, or physical paths.

The generic cloud client and its interoperable HTTP protocol are documented in
[storage-api.md](storage-api.md). The first server implementation uses
[Cloudflare D1/R2](../cloudflare/storage/README.md). Local folders remain the
default, and selecting a cloud service never automatically migrates documents.

## Document values

`textfold.documents.Document` contains the complete persisted document:

- `id`, `meta`: stable identity and source metadata.
- `source`: normalized HTML, Markdown/XML, original PDF, other original formats,
  and the source reference when fetching is pending.
- `layers`: generated English and translation layers, keyed by layer identity.
- `notes`: highlights, notes, questions and conversations.
- `images`: resource IDs referenced by the document mapped to bytes.
- `processing`: layout, structured extraction and cached model replies.
- `previous`: the previous source/layers when a document was replaced.
- `extensions`: opaque extension content that must survive round trips.
- `created`, `updated`, `revision`: timestamps and an opaque concurrency token.

`_legacy` is private round-trip information used by the built-in codec to retain
old JSON formatting/encodings and unchanged bytes. Backends do not interpret it.
The domain fields, rather than `_legacy`, determine the saved document.

`DocumentInfo` is a lightweight index row: ID, metadata, translated titles,
languages, note count, timestamps, readability and revision. Index lookups must not load original
PDFs/images. A remote adapter can maintain a database index or manifest.

## Backend interface

`StorageBackend` is a Python protocol in `textfold/documents.py`:

```python
class StorageBackend(Protocol):
    def get(self, document_id: str) -> Document: ...
    def info(self, document_id: str) -> DocumentInfo: ...
    def save(self, document: Document) -> None: ...
    def prepare_import(self, document_id: str) -> None: ...
    def list(self) -> Iterable[DocumentInfo]: ...
    def delete(self, document_id: str) -> None: ...
    def trash(self, document_id: str, placement: dict | None = None) -> str: ...
    def list_trash(self) -> list[dict]: ...
    def restore(self, document_id: str, revision: str | None = None) -> dict | None: ...
    def delete_trashed(self, document_id: str, revision: str | None = None) -> bool: ...
    def purge(self, before: float) -> list[str]: ...
    def get_library(self) -> LibraryState: ...
    def save_library(self, state: LibraryState) -> None: ...
```

`get` raises `FileNotFoundError` for a missing ID and returns a detached snapshot.
Changing it does not mutate storage until `save` succeeds. `save` replaces the
complete document, including removals from its fields; it must compare the
snapshot's `revision` with the current token. `None` means create only. Stale
writes raise `ConflictError` without overwriting newer data. Successful saves
refresh the passed object's revision and timestamps. Tokens may be integers,
ETags or opaque comparable values; callers do not interpret them.

`info` looks up one active document's index row without loading its original or
attachment payloads; a missing/unindexed ID raises `FileNotFoundError`. It powers
existence/readability checks and page-cache validation. `list` returns the same
rows for the whole library, so first-time added dates reuse one index snapshot.

`delete` permanently removes a document (missing ID: `FileNotFoundError`).
`trash` retains the whole document and optional library placement, removes it
from the active index and returns an opaque trash token. `restore` returns the
latest saved placement, or `None` if nothing can be restored or that ID is active
again. `purge` permanently deletes trash older than a Unix timestamp and returns
its removed tokens. Trash timestamps and storage locations belong to the adapter.
`list_trash` and `delete_trashed` are part of this same interface; see
[trash.md](trash.md) for row fields, concurrency tokens and cloud safety rules.
Cloud retention is explicit: the offline adapter never ages out trash on startup.
`prepare_import` is used only for an explicit import or generation request. It
allows the same ID to be imported after permanent deletion, without restoring
old contents or replacing protected recovery data. Directory backends need no
reset; the application keeps a no-op fallback for older plugins. Ordinary saves
and background synchronization never call this method.

`LibraryState` holds library-wide organization (`catalog`: folders, names, order,
added/opened timestamps) and external bindings (`zotero`). These are not document
assets or the primary document index. `get_library` returns an empty state for a
new library. `save_library` uses the same revision/conflict convention.

## Application usage

```python
from textfold import store

with store.use_backend(my_backend):
    document = store.document(document_id)
    print(document.meta["title"])
    print(document.source.pdf)
    with store.edit(document_id) as document:
        document.notes = updated_notes
    index = store.repository().list()
```

`DocumentStore` provides an internal parser bridge for file-based PDF/image
engines and older importers. It materializes a temporary working copy from a
Document, then publishes the completed changes through `get`/`save`. Nested
same-thread calls share a working copy. Generation publishes a complete document
checkpoint after fetching and after completed layers, so other requests can read
the original/English while model work continues. Completed model caches survive a failed
or stopped generation. Non-overlapping concurrent changes (for example notes
saved while generating) are merged; overlapping edits raise `ConflictError`.
The outermost workspace controls whether it is writable; nested calls cannot
promote a read-only workspace, and its checkpoints never publish changes.
Uploaded originals are deleted from temporary uploads only after a durable
checkpoint has committed them to the document backend.
Backends **do not implement** `workspace`, file IO, directory scans or synchronization
of temporary directories. `store.pdir`, `asset` and filename-based helpers remain
compatibility details for these integrations, not the backend interface.

In cloud mode, the outer writable workspace defers publication of that document
until generation finishes or stops. Checkpoints still commit locally to SQLite,
so another reader can open completed stages and failed generations retain their
completed replies. A pending document stays in the outbox and survives restart;
closing the workspace requests one synchronization. Manual/periodic sync skips
that document while it is being generated; other documents continue syncing.
Nested workspaces share the deferral. Active documents cannot be evicted or
silently left behind by switching libraries. Small parser reads and layer writes
use the local working files directly instead of decoding PDFs and images.

`tests/test_storage.py` runs the same semantic operations against disk and a
memory adapter storing complete Document objects, with no file/path methods.
It tests originals, notes, layers, PDF parsing, generation, replacement, trash,
stale-write rejection and interrupted parser processing.

Backend selection is explicit. Settings supports local storage
and any HTTPS service implementing the versioned [Storage API](storage-api.md).

## Local compatibility

The default `LocalDirectoryBackend` opens existing documents **in place**. Opening
or listing does not reserialize existing UTF-8 files. The upstream one-time
repair of legacy Windows-codepage text remains supported.
Its private codec retains the current layout and unknown extension files:

```
<library>/
  papers/<id>/
    source.pdf | source.md | original.<extension>
    source.html, meta.json, img/, layout.json, structured_content.json
    layers/en.json, layers/tr-<language>.json
    notes.json, llm-cache/, previous/
  papers/.trash/
  library.json
  zotero.json
```

Local saves stage one complete document and swap directories, rolling back if
publication fails. Unchanged asset bytes and modification times are retained.
Each swap is journaled before renaming the original. Interruptions retain the
backup, and constructing the adapter recovers abandoned swaps from dead processes.
It leaves ambiguous backups and transactions of live processes untouched.
The adapter serializes in-process access to avoid exposing the swap window;
this does not add distributed synchronization or cross-process transactions.

The runtime directory `store.DATA` remains separate: uploads, exports,
`server.json`, logs and Electron caches stay there. Credentials/model settings
stay in `~/.textfold/`; the PDF engine stays in the system application data
folder. `TEXTFOLD_DATA` supplies the default library location; an explicitly
saved document-library selection takes precedence for documents.

## Changing the local library in Settings

Settings → Document library shows the active path. The desktop app offers a
native directory picker; a browser allows the path to be typed. The first version
accepts an empty, absolute destination outside the current library.

`GET /api/storage` returns `backend`, active `path`, `pending` destination and a
previous migration `error`. `POST /api/storage` with `{path}` schedules a change;
`{cancel: true}` cancels it. Existing host/origin protections apply. No live
document is moved or selected when the setting is saved.

The selection is saved separately from credentials in
`~/.textfold/storage.json`. At the next **server start**, before job queues or
HTTP requests start, TextFold copies `papers/` (including notes, caches, previous
versions and trash), `library.json` and `zotero.json`. It verifies SHA-256 checksums
and checks that the source did not change during copying before selecting the
new library. Use one TextFold instance during migration. The old library is
retained. Other directories and model settings are not copied.

A copy failure, conflicting destination or configuration write failure keeps the
old library active. A marker allows an interrupted copy to resume; different
user-authored bytes are never overwritten. Its checksum manifest records copies
owned by the migration. Retries update/remove only unchanged owned copies and
verify the entire destination file set, so source deletions cannot reappear.
Symbolic links are refused during copying
rather than followed outside the selected library. Errors appear in Settings,
where a pending move can be cancelled. CLI reads use the active library; pending
moves are activated by the next server start.

## Cloud working copies and offline changes

Settings → Document storage combines local and cloud selection. The cloud form
contains service origin, username, password and an optional absolute cache base.
Passwords use Python `keyring`; configuration stores only a credential reference.
Selections take effect on server restart. Enabling cloud storage never uploads
an existing local directory automatically.

`OfflineBackend` exposes the same whole-document `StorageBackend` interface.
It composes the generic HTTPS client with `SQLiteBackend`; application callers
still do not read individual files or issue SQL. A SQLite transaction persists
records, a fresh local revision, modification time and the outbox together.
Cloud acknowledgements do not clear changes made while a request was in flight.
Transport failures retry a durable operation ID and exactly the same payload.

Cloud services may advertise optional authenticated WebSocket change hints.
One Python connection per running workspace wakes the existing sync executor;
notifications never acknowledge outbox items or move durable cursors. Healthy
idle workspaces make a low-frequency health check and skip unchanged snapshots.
Unsupported or failed notifications fall back to HTTP polling with backoff.
Source immutability, offline saves and conflict handling remain the same. See
the [protocol](storage-api.md#optional-change-notifications) and
[design and validation boundaries](storage-change-notifications-design.md).

The cloud is authoritative. Clean local copies can be discarded; pending edits,
uncertain requests, conflicts and their blobs cannot. Cache bases are namespaced
by endpoint/account and pin the remote library identity. A locked keyring still
allows previously cached reading/editing, while synchronization reports an error.
Changing endpoint, account, mode or cache location is blocked until pending edits
and unresolved conflicts have been handled. Recovery ZIP export is available;
it does not mark changes synchronized or erase the old working copy.

The default base is `<runtime>/cloud-cache/`; each namespace has `library.sqlite3`
and `blobs/<sha256>`. This directory is separate from the legacy local library.
The settings screen displays the exact active cache path.

Manual actions: upload the active legacy library, download all active cloud
originals for offline use, sync now, clear clean cache, export pending changes,
and resolve retained revisions. Imports are resumable and repeatable, preserve
the source directory and skip/report IDs whose originals differ. The first
version imports active legacy documents; old local trash remains in the source.

The local server exposes `GET /api/storage/sync`, `GET /api/storage/conflicts`,
`GET /api/storage/export` and `POST /api/storage/sync` with actions `sync`,
`import`, `download`, `clear`, `resolve`. Conflict resolution takes `document`,
`conflict`, `choice` (0/1; or restore/delete for document state), and optional
manual annotation `text`. A successful note save reports local/cloud state in
`X-TextFold-Save-State` and `X-TextFold-Online`; the UI keeps a pending indicator.

Resolving an old conflict retains subsequent edits of its ambiguous fields as a
new conflict against the selected result. Delete/edit conflicts are recoverable
in either synchronization order. Failed browser writes retain their unconfirmed
field changes for retry, and background refresh does not replace those drafts.

Cloud target changes retain the active workspace selection and its keyring
reference until startup checks it again. Pending edits, uncertain requests or
unresolved conflicts cancel the switch and keep the previous workspace active;
the Settings error explains why. No document data is moved or removed.

See the [approved design](storage-offline-sync-design.md) for merging and
retention rules. SQL schemas are internal adapter details, not backend interfaces.
