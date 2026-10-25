# Document storage, offline synchronization and conflict handling

Status: implemented foundation and opt-in cloud phase. This document records the agreed rules; the protocol is specified in [Storage API v2](storage-api.md#record-synchronization-v2). The [Chinese discussion archive](storage-offline-sync-design.zh-CN.md) retains the original design rationale.

## Authority and identity

A document is the public identity, indexing and read/write unit. Backends exchange complete detached values and own their physical layout. The built-in local backend keeps existing directories and file formats in place. No automatic conversion of a local library to SQLite occurs.

Cloud storage is optional. When enabled, the cloud is the authoritative library; each device uses a separate SQLite working copy with a transactional outbox and content-addressed attachments. Unsynchronized data is durable user content, not disposable cache. Credentials use the system keyring and are outside document storage. Install cloud dependencies with `pip install 'textfold[cloud]'`; the desktop payload bundles these optional capabilities.

A source document published to the cloud is immutable. Different source bytes require a new document identity. Re-converting the same PDF can improve its derived HTML/layout without replacing the original. The established local Markdown update API remains available for editor integrations, retaining its ID, notes and previous generated content.

## Local behavior and library selection

Local generation writes directly into its document directory: every received model reply is durable immediately, including on SIGTERM. Notes and assets use single-file reads and atomic changed-file writes, with no whole-directory copy. The documented `thread` remains the canonical conversation; extra message ancestry is preserved beside it and cannot hide turns written by an older client.

Library selection is scoped per runtime identity. Users can copy to an empty directory, or explicitly open an existing library without copying or merging. Selection takes effect after restart. Copies verify hashes, journal ownership before creating temporary files, preserve the original, and refuse destination changes. Failure keeps the old library accessible; damaged configuration is preserved for recovery. Runtime files and credentials stay put.

## Offline reading and saving

Downloaded documents remain readable and editable without the service. A successful offline save commits the content, local modification time and outbox entry in one SQLite transaction before acknowledging it. The UI explicitly says that the change is saved only on this device and needs connectivity; the notice appears once per offline period, with a persistent pending/conflict status.

Device clocks never choose which content survives. Timestamps are for display and diagnostics. Baseline revisions, record identities and cloud compare-and-swap determine concurrent edits. Reopening after an offline save must retain the draft and pending state. A failed local transaction must never report a successful save.

## Synchronization and notifications

Startup establishes a complete metadata baseline; later pulls use a durable cursor. Saves, reconnection, resume/focus and manual synchronization schedule reconciliation. A hibernating WebSocket notification is only a hint that a newer cursor exists: notifications contain no document payload and never establish correctness. Missing hints are repaired by bounded reconnect/backoff and periodic catch-up. Idle connected clients avoid repeated cloud health/feed calls; local-only clients start no cloud status/conflict polling.

Before upload, the client performs a three-way merge of its last acknowledged baseline, local records and latest cloud records, then commits with CAS and an idempotency key. An uncertain acknowledgement retries the identical request before forming another one. Edits made while a request is in flight remain pending until separately acknowledged. Concurrent syncs cannot clear each other's newer outbox generation.

A legacy cursor or loaded v1 library is not evidence of a completed v2 baseline. Once the service fences legacy writes, every client must establish and persist the v2 baseline before relying on incremental notifications. Restarting during that transition must retry the baseline without deleting local edits or mistaking an incomplete catalog for an empty cloud library.

## Merge rules

- Independent annotations and independent fields merge automatically. An ambiguous edit to the same field retains both complete revisions; users may choose one or create a new merged text. Resolving an older conflict preserves later edits as another reviewable conflict.
- Equivalent highlights deduplicate only when target selectors and style agree exactly, with aliases preserving old references. Notes with different identities or text are not collapsed merely because they quote the same passage. Cross-language/level targets merge only when their original selector equivalence is established.
- Deletion versus editing preserves both the deletion intent and recoverable content, including hidden message ancestry. The UI offers explicit restore or keep-deleted; arrival order must not silently resurrect a deleted annotation/document or destroy the other device's edit.
- Conversations use immutable parent/child message identities. Concurrent follow-ups form sibling branches with their own context. The UI selects a conversation fork; completed request IDs never call the model again. Offline questions remain drafts and require an explicit resend after reconnecting.
- `thread` is canonical for interoperability. Importing or editing a thread-only note incorporates its turns into the graph while preserving other branches. A selected canonical head travels with the annotation; choosing a default display branch does not erase siblings or manufacture a content conflict.
- Stable paragraph/section ID histories merge per identity. Different IDs from both devices survive automatically. Where the same ID has ambiguous concurrent history, each retained candidate also contains all unrelated merged history, so choosing one cannot drop the other device's distinct IDs. Unknown/malformed extensions remain opaque and losslessly recoverable.
- Generated levels, translations, processing replies, unknown extensions, folder organization, display names and Zotero bindings participate in record synchronization. Independent records merge; ambiguous changes retain revisions. The first phase supplies generic revision selection rather than dedicated visual diffs for every generated artifact.

## Import, cache and switching

Upload of an existing local library is an explicit, repeatable action; enabling cloud mode does not upload automatically. The source is kept. An equal ID with different original source is skipped and reported. Download-all and clear-clean-cache are separate actions. Cache cleanup never removes unsent content, pending requests or unresolved conflicts.

Switching service/account, disabling cloud or changing its cache directory is blocked while pending content or conflicts remain. Startup rechecks the previous workspace because new edits may have appeared after settings were saved; if so, switching is cancelled and previous credentials/configuration stay available. A recovery ZIP contains pending records and their attachments. Export does not acknowledge the outbox or waive the switch guard.

Different services/accounts use isolated workspaces. A changed cloud library identity refuses writes rather than silently rebinding an existing cache. Authentication failures back off and require visible correction, never a successful empty-library result.

## Compatibility and boundaries

Trash uses the common backend interface and a confirmed revision for permanent
deletion. The cloud removes the document and its index entry, retaining a separate sync deletion receipt;
stale devices retain unsent work for recovery/export instead of resurrecting
the document. Local startup retention does not run against cloud trash or
recovery copies. Explicit reimport/generation reuses the same document ID, with
a CAS against the deletion revision; older merge bases remain fenced. See
[Trash](trash.md) for the implemented protocol and limits.

Old local libraries are read in place, preserving original bytes and unknown files. The v1 wire snapshots remain readable; after v2 synchronization is enabled, legacy writes receive 409 rather than overwriting message trees/revisions. Database migrations add tables and retain old snapshots. No physical cloud garbage collection of blobs, tombstones, history or change-log entries occurs in this phase; retention windows and full-resync rules must precede it.

The first phase does not provide multi-user authorization, scheduled history cleanup, automatic migration of every local library, or a specialized generated-version browser. Plain HTTP saved readers can create annotation IDs without `crypto.randomUUID`; authentication credentials always use the cryptographic/system mechanisms intended for them. Save errors appear inline and leave drafts visible.

## Acceptance scenarios

Regression coverage must include offline save then restart, reconnection without overwriting new edits, devices with skewed clocks, independent and identical highlights, same-field edits, delete/edit in either sync order, parallel conversation forks, uncertain acknowledgements, cache cleanup with pending data, source-ID mismatch, late changes during target switching, notification loss/reconnect, interrupted baseline establishment, and legacy local-thread edits.
