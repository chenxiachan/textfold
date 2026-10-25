# TextFold Storage API

This is the wire protocol used by the generic Python `CloudStorageBackend`.
The application-facing backend interface remains whole-document
[`StorageBackend`](storage.md). Clients do not inspect a server's physical file
layout. The [D1/R2 Worker](../cloudflare/storage/) is one implementation.

## Authentication and errors

Settings takes an HTTPS **origin** (for example `https://storage.example.com`),
username and password. Requests use HTTP Basic authentication with the UTF-8
bytes of `username:password`; usernames cannot contain `:`. All routes require
authentication, including health and binary objects. HTTP is allowed only on
loopback during development. Redirects are rejected to avoid forwarding secrets.

Errors are JSON `{ "error": "safe explanation" }`. Use 400 for invalid input,
401/403 for failed authentication, 404 for a missing document/object, 409 for a
stale revision or incomplete upload, 413 for size limits and 503 for unavailable
storage. Never return credentials or raw exception/request dumps. JSON responses
are objects and use `Cache-Control: no-store`.

## Routes

All paths start with `/v1`. IDs are URL-encoded path segments. A successful
mutation returns HTTP 200. `commit` and non-null `revision` are opaque UUID
strings. A `revision: null` save is create-only; other saves must compare the
supplied revision atomically with the current revision and return 409 on conflict.

| Method and path | Request | Successful response |
| --- | --- | --- |
| `GET /health` | — | `{ "format": "textfold/storage-v1" }` |
| `GET /documents?after=<id>` | — | `{ "documents": [DocumentInfo], "next": "last-id-or-null" }` |
| `GET /documents/<id>/info` | — | `DocumentInfo` |
| `GET /documents/<id>` | — | `{ "document": DocumentManifest, "info": DocumentInfo }` |
| `PUT /documents/<id>` | `{ "revision": "old-or-null", "commit": "new-uuid", "document": DocumentManifest }` | New `DocumentInfo` |
| `DELETE /documents/<id>` | — | `{ "deleted": "id" }` |
| `POST /documents/<id>/trash` | `{ "commit": "new-uuid", "placement": {} }` | `{ "token": "uuid" }` |
| `POST /documents/<id>/restore` | `{}` | `{ "placement": {} }`, or null placement if absent/already active |
| `POST /trash/purge` | `{ "before": 1234567890.5 }` | `{ "removed": ["trash-token"] }` |
| `GET /library` | — | `{ "catalog": {}, "zotero": {}, "revision": "uuid-or-null" }` |
| `PUT /library` | `{ "catalog": {}, "zotero": {}, "revision": "old-or-null", "commit": "new-uuid" }` | `{ "revision": "new-uuid" }` |
| `HEAD /blobs/<sha256>` | — | 200 if uploaded/registered, otherwise 404 |
| `PUT /blobs/<sha256>` | Raw bytes, `Content-Length` required | `{ "sha256": "hex", "size": 123 }` |
| `GET /blobs/<sha256>` | — | Raw bytes, `application/octet-stream` |

Listing is ordered by document ID with an exclusive `after` cursor; `next` is
null at the end. It must not load binary originals or manifests. Documents with
`meta: null` represent pending source references: `get` works, but listing omits
them and `info` returns 404. The client updates the passed document/state token
only after a successful save. Restore refreshes the revision, avoiding reuse of
an obsolete save token. Restore chooses the newest trash entry and never
overwrites an active document. Purge uses a strict `deleted_at < before` cutoff.

## Document manifests

The JSON manifest is a complete semantic document snapshot. A `BlobRef` is
`{ "sha256": "64-lowercase-hex-characters", "size": 123 }`; maps below use logical
resource identifiers, not physical object paths. Binary slots are explicit, so
ordinary user JSON cannot be confused with encoded bytes.

```json
{
  "format": "textfold/document-v1",
  "id": "md-example",
  "meta": { "id": "md-example", "title": "Example" },
  "source": {
    "html": "<article>Example</article>", "markdown": "# Example",
    "xml": null, "wiki_html": null, "reference": null,
    "pdf": null, "originals": {}
  },
  "layers": {}, "notes": null, "images": {},
  "processing": { "layout": null, "structured": null, "replies": {} },
  "previous": null, "extensions": {}, "legacy": {},
  "created": 0, "updated": 0
}
```

`source.pdf` is null or a BlobRef. `source.originals`, `images`,
`processing.replies`, `extensions` and `legacy` map identifiers to BlobRefs.
`previous` is null or another manifest for the same ID, up to eight levels deep.
All fields above are required. `layers` maps layer names (`en`, `tr-zh`, etc.) to
JSON objects. Notes and processing values retain their application's JSON schema.
`legacy` is opaque byte-preservation data for lossless local-library round trips;
servers preserve it without interpreting it.

Upload and verify all referenced blobs before publishing a manifest. Existing
content addressed by a SHA-256 hash is immutable. The server must prevent
publication with missing blobs, publish index and revision atomically, and leave
the old document readable on failure. SHA-256 and sizes are checked on download.
The physical storage arrangement is entirely the server's choice.

`DocumentInfo` has `id`, `meta`, `titles` (translation language → title),
`languages` (`en` first if present, then sorted translation languages),
`note_count`, `created`, `updated`, `revision`, `readable`. Times are Unix seconds.
`readable` is true when metadata and HTML source exist. On read, root timestamps
and revision come from `info`, not the immutable manifest's original timestamps.

## Desktop settings and credentials

Settings → Cloud storage configures, tests and selects an endpoint. Selection
takes effect on server restart. It does not migrate any local or remote data.
Returning to local restores the previously selected local library location.

`~/.textfold/cloud-storage.json` contains selection, URL, username and an opaque
credential reference. Passwords use Python `keyring`, under service
`TextFold cloud storage v1`. Credentials are outside document storage and local
library migration. No password is returned by Settings APIs. A blank password
can reuse the saved password only for the same URL and username. Keyring errors
are explicit; no plaintext fallback is provided. On a locked/unavailable keyring,
the library remains unavailable and Settings can reconnect or switch to local.

`GET /api/storage/cloud` returns redacted settings including `enabled`, `active`,
`url`, `username`, `hasPassword`, `restartRequired`, `error`.
`POST /api/storage/cloud` takes `{enabled, url, username, password}`; enabling
requires a successful health check before credentials/selection are saved.
`{enabled: false}` selects local for the next restart. Credentials stay available
for a later reconnection. `POST /api/storage/cloud/test` tests the supplied
connection without saving it. The local server's existing host/origin protections
apply to all these routes.

For pip/source installs, enable this optional feature with `pip install "textfold[cloud]"`.

Python keyring chooses the system backend. On Linux, install/unlock an available
Secret Service/KWallet backend as appropriate. See the
[keyring documentation](https://keyring.readthedocs.io/en/latest/).

## Record synchronization v2

New cloud clients require both the v1 blob transport and the v2 routes below.
Authentication, origin restrictions, errors and limits remain the same. This
protocol is independent of D1/R2: an alternative server can use any transactional
index and immutable blob store. The application-facing interface remains whole
Documents; the generic storage adapter owns record reconciliation.

| Method and path | Response / purpose |
| --- | --- |
| `GET /v2/health` | `{format: "textfold/storage-v2", library_id, cursor, sync_enabled}` |
| `GET /v2/index?after=<id>` | `{documents: [{id, revision, info, state}], next}`; includes trash/deleted metadata |
| `GET /v2/changes?cursor=<integer>` | `{changes: [{sequence, document_id, revision, state}], cursor, more}` |
| `GET /v2/documents/<id>` | `{records, revision, state, placement, info}` |
| `PUT /v2/documents/<id>` | Apply one atomic sparse record commit; return `{revision, state, info}` |
| `POST /v2/documents/<id>/purge` | Permanently delete a confirmed trash revision; separate sync receipt |
| `GET /v2/library` | Same snapshot envelope for the reserved organization document |
| `PUT /v2/library` | Same atomic commit for organization records |

### Trash and permanent deletion

Services supporting permanent deletion advertise `trash_purge: 1` in health.
Index rows and snapshots include `purged: boolean` (missing means false).
Trashed documents use the existing `state: "trash"` sparse commit, with library
placement retained; restoring commits `state: "active"` against that revision.
Legacy trash is indexed too, unless an active same-ID document exists.

The purge endpoint accepts `{operation_id, revision, state: "deleted", changes:
[], placement: {}, purge: true}`. It requires the exact current trash revision,
applies the same operation replay/hash rules, and returns `{revision, state,
info, purged: true}`. In one transaction it removes records and legacy trash
rows, records the idempotent operation, and appends a normal deleted feed event.
Subsequent snapshots have `records: {}`, `purged: true`, and the confirming
`operation_id`. Purged documents are absent from the document index; a separate
`document_deletions` receipt retains ID, deletion revision, sequence and time.
Ordinary future writes to that identity are rejected, even with the newest token.
Replay of the original successful operation still returns its saved result.
Failed CAS publishes nothing. Notifications are sent only after commit.

A separate endpoint ensures older servers reject the request instead of
silently treating it as soft deletion. Clients retain their local outbox until
the purge is confirmed and quarantine other devices' unsent work for explicit
recovery/export; they never merge it into a subsequently reimported document. See
[trash.md](trash.md) for storage/retention and shared-object limitations.

Services advertising `document_reimport: 1` allow explicit import/generation
using the same ID. A normal `PUT /v2/documents/<id>` may supply `reimport_from`
equal to the current deletion revision, `revision` equal to that revision,
`base_revision: null`, and `state: "active"` with freshly imported records.
This is a CAS creation after deletion, with normal idempotency; it never revives
the deleted records. The user intent is persisted before upload and must not
be inferred during automatic synchronization. Competing import/deletion rejects
the stale intent without replacing its local content.

After permanent deletion, ordinary commits require `base_revision`, naming the
cloud revision on which their local edits were based, before merging newer
remote records. Its change-log sequence must be newer than the deletion receipt.
A missing, unknown or pre-deletion base returns 409, even when `revision` is the
newest head. This fences stale clients that would otherwise merge old notes into
a fresh import. `GET /v2/documents/<id>?base_revision=<revision>` reports
`base_deleted`; an empty query value represents an unknown base. Clients keep
unacknowledged changes in recovery rather than merging when it is true. Snapshots
also report the latest `operation_id` so acknowledgements can be reconciled.

### Optional change notifications

`GET /v2/health` may additionally return
`"notifications": {"protocol": "textfold/changes-ws-v1", "path": "/v2/events"}`.
Omitting this capability keeps HTTP polling available. The path is resolved
relative to the configured origin with a trailing slash; a leading slash is
origin-relative. An implementation behind a prefix can advertise
`/prefix/v2/events`. Absolute URLs, queries, fragments and cross-origin targets
are rejected. Endpoint settings still accept an origin, not an API base path.

`GET` that path with `Upgrade: websocket` uses the same Basic Authorization
header as the HTTP API. HTTPS uses WSS; WS is permitted only for an already
validated loopback HTTP origin. Redirects are rejected; credentials never belong
in URLs, query parameters or subprotocols. Missing support returns 404; a request
without upgrade returns 426. Notification authentication and transport errors
are reported separately from HTTP synchronization status.

After authentication, the server registers the subscription **before** sending:

```json
{"type":"ready","protocol":"textfold/changes-ws-v1","library_id":"stable-library-id"}
{"type":"changed","library_id":"stable-library-id","cursor":123}
```

Messages are UTF-8 JSON text, limited to 4 KiB. `cursor` must be a nonnegative
JavaScript-safe integer. The stream is server-to-client only; clients use
protocol ping/pong for liveness. No document content or credentials are sent.
The server derives the library identity from authenticated context, not client
headers. A library mismatch stops that subscription without rebinding the cache.

Every `ready`, including reconnects, requests normal synchronization to cover
the handshake window. A `changed` cursor is only a hint: it never advances the
persisted cursor or acknowledges an outbox item. Duplicate, reordered and lost
hints are harmless; the durable change feed remains authoritative. Broadcast
only after a committed transaction, and keep a committed HTTP response
successful if notification delivery fails.

The client keeps one subscription per running workspace and serializes sync
passes. With a healthy connection it checks health about every five minutes
with jitter; an unchanged cursor skips change/snapshot reads only after initial
indexing and the library baseline, with no pending or uncertain writes and
`sync_enabled: true`. Old services and broken notification connections use
backed-off HTTP polling. Local saves request sync immediately; network errors
retain exponential backoff, while authentication errors use controlled retries.
See the [notification design](storage-change-notifications-design.md).

Legacy v1 writes have no change-feed entries. Before enabling the fast return,
the client must persist proof that it refreshed both the index and library
baseline **after** observing the v2 write fence. An already loaded library and
matching cursor in an older cache are insufficient, including after restart.
The local `sync_state.v2_baseline` marker is written only after those reads
succeed; missing markers retry without clearing documents or outbox items.

### Identity, commits and compatibility

`library_id` is a persistent random identity. A client pins it in its workspace
and refuses to submit old-library operations to a replacement service. The
first version retains its complete change feed and tombstones; it never silently
expires a cursor. Clients must commit a pulled page locally before advancing
its cursor. Initial indexing records the health cursor **before** scanning and
then replays changes from that position, including changes racing with the scan.
Unknown originals stay lazy until opened or downloaded explicitly.

`@library` is reserved and may appear as a `document_id` in the change feed;
it is addressed only through `/v2/library`. Every record key is scoped to its
document. State is `active`, `trash`, or `deleted`; deletion retains the ID and
its last records so stale create requests cannot resurrect it. Explicit restore
is a new CAS commit setting state to active.

A commit body is:

```json
{
  "operation_id": "a fresh UUID, stable across retries",
  "revision": "the last cloud revision, or null for creation",
  "changes": [
    {"key": "annotation/n-example", "deleted": false,
     "value": {"id": "n-example", "anchor": {"unit": "p1"}, "text": "My note"}},
    {"key": "alias/n-duplicate", "deleted": false, "value": "n-example"}
  ],
  "state": "active",
  "placement": {},
  "created": 1792700000
}
```

`revision: null` is create-only. A revision mismatch returns 409 and writes
nothing. The operation ID identifies the complete request **and target document**;
a byte-equivalent semantic replay returns its original acknowledgement, while
reusing an ID with different content or target returns 409. Clients persist the
request before sending, retry uncertain requests first, and generate a new ID
only when deliberately rebasing against a newer cloud revision. JSON key order
does not change request identity.

The server validates all changes, immutable source and message ancestry, writes
immutable object data, then atomically commits records, tombstones, document
index, operation acknowledgement and change log. Orphan immutable uploads are
safe to retain after failure. No acknowledgement is returned before SQL commits.
A failed CAS must not publish even an unchanged legacy record.

Record families map the document-v1 manifest without exposing physical paths:

- `meta`, `previous`, `notes` hold document metadata, prior export content and
  notes header respectively.
- `source/<field>`, `source.originals/<format>`, `processing/<field>`,
  `processing.replies/<id>`, `layers/<language>`, `images/<id>`,
  `extensions/<id>`, `legacy/<id>` retain the corresponding manifest values.
- `annotation/<id>` stores note/highlight fields without embedded conversations.
- `message/<annotation-id>/<message-id>` stores `{id, parent, role, text, ...}`.
  Parent IDs are within that annotation. All branches must be acyclic with
  existing parents. Confirmed messages are immutable; pending generation nodes
  can complete without changing identity, parent or role. Deleting a whole
  conversation can tombstone its messages.
- `conflict/<id>` retains `key`, `base`, two `variants`, deletion intent and
  ambiguous fields. Restoring a deleted conversation preserves its ancestry.
- `alias/<old-id>` keeps links to deduplicated equivalent highlights valid.
- Organization uses `catalog.folders/<id>`, `catalog.<map>/<id>`,
  `catalog/<field>` and `zotero/<id>`; `catalog/folder_order` and
  `zotero-order` retain their existing display order; conflicts use the same conflict family.

Blob references still use `{sha256, size}` and `/v1/blobs/<sha256>`; upload and
verify missing blobs before committing records that reference them. Large record
bodies may be stored privately outside SQL, but snapshots must return complete
record values. This implementation moves values above 128 KiB to immutable R2
objects and retains full recovery manifests as well as individual SQL records.

Clients reconcile base/local/remote records before CAS. Independent records and
annotation fields merge; same-field ambiguity retains revisions rather than
using device clocks. Completed AI branches union by message ID and parent.
Delete-versus-edit keeps deletion visible and retains the edited revision for
explicit recovery. Model calls are not sync operations and must never replay as
a consequence of reconnection.

The first v2 commit upgrades the library to record-aware writes. Existing v1
snapshots remain readable and are imported on their first v2 mutation. All
subsequent legacy document/library/trash mutations return 409; v1 immutable blob
transport remains available. Deploy all migrations before deploying the Worker.
Upgrading the schema does not delete any v1 manifest, original or local document.
