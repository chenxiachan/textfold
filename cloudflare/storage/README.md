# D1/R2 document storage

This Worker implements [TextFold Storage API v1/v2](../../docs/storage-api.md).
The desktop client uses the generic `CloudStorageBackend`, so other servers
can implement the same API without using Cloudflare.

D1 stores the document index, individual records/tombstones, idempotent operations
and a monotonic change log. R2 stores immutable document recovery snapshots, large
record values and deduplicated binary objects. Uploads verify SHA-256 before a D1
compare-and-swap publishes the complete snapshot. A failed upload or stale save
cannot replace the previous revision. Listing reads only D1, never original PDFs.

## Develop and test

Use Node.js 22+ and the Python project dependencies, including `keyring` and
`websockets>=15,<16`.

```sh
cd cloudflare/storage
npm ci
npm test
```

Tests use workerd/Miniflare's actual local D1 and R2 implementations and run the
shared Python backend contract over HTTP, plus application Settings/restart
tests. Set `TEXTFOLD_TEST_PYTHON` if the Python environment is outside `.venv`.
Test credentials and the explicit test keyring stay inside disposable fixtures.
Notification tests also run real WebSocket subscriptions, explicit DO hibernation,
cross-device Python reconciliation, missed-hint recovery and idle request counting.

For interactive local development, create an ignored `.dev.vars` containing
`STORAGE_USERNAME` and `STORAGE_PASSWORD`, then run:

```sh
npx wrangler d1 migrations apply DB --local
npm run dev
```

Use `http://127.0.0.1:8787` in Settings. HTTP is accepted only on loopback.

## Deploy your own instance

Each deployment is one private library with one username/password. Choose
dedicated resources; do not bind an unrelated database or public bucket.

```sh
npx wrangler login
npx wrangler d1 create textfold-storage
npx wrangler r2 bucket create textfold-storage
```

Put the returned D1 ID in `wrangler.jsonc`, replacing the all-zero placeholder.
Adjust names if those resources already exist. To use a custom domain, add:

```json
"workers_dev": false,
"preview_urls": false,
"routes": [{ "pattern": "storage.example.com", "custom_domain": true }]
```

The domain must be in your Cloudflare account. Configure credentials using
interactive secret entry; neither value belongs in source control:

```sh
npx wrangler d1 migrations apply DB --remote
npm run deploy
npx wrangler secret put STORAGE_USERNAME
npx wrangler secret put STORAGE_PASSWORD
```

Until both secrets exist, the Worker denies all requests. R2 stays private.
After deployment, enter the HTTPS origin, username and password in TextFold
Settings → Document storage. Test the connection, select Cloud storage, save and
restart. Existing local documents are not uploaded or deleted. Use “Upload local
library” explicitly; use “Download all for offline use” on other devices.

## Change notifications

The `NOTIFICATIONS` binding uses one `LibraryNotifications` Durable Object class
and one instance per stable whole-library ID. The `notifications-v1` migration
in `wrangler.jsonc` creates the SQLite-backed class during deployment; it does
not migrate or remove D1/R2 document data. Preserve this migration entry in later
releases. Existing deployment configurations need the binding, class export and
migration to advertise the optional `/v2/events` capability.

Only authenticated subscriptions reach the DO. After a D1 commit, a managed
background RPC broadcasts a content-free cursor hint. HTTP writes remain
successful if delivery fails. Hibernation uses platform-managed connections,
not a resident subscription map or recurring DO timers. Clients send WebSocket
protocol pings and perform a jittered five-minute health fallback; missed hints
are repaired through the existing durable feed.

To disable notifications temporarily, set Worker variable
`NOTIFICATIONS_DISABLED` to the string `"true"`. Missing bindings also omit the
capability. Clients then continue HTTP synchronization. Deploying only the new
client against an old service is supported; enabling notifications requires
deploying the updated Worker and restarting clients. No document migration or
cache reset is required.

Idle request-count tests use a virtual clock. Production Worker requests, DO
requests and DO duration must be measured separately; there is no promised fixed
billing reduction. See the [design](../../docs/storage-change-notifications-design.md)
and [Cloudflare WebSocket hibernation guidance](https://developers.cloudflare.com/durable-objects/best-practices/websockets/).

## Operational limits

- Cloud mode uses a durable local SQLite working copy and outbox, with record
  reconciliation, offline reading/editing, manual import/download and automatic
  reconnect sync. The cloud remains authoritative after acknowledgement.
- Manifests/library state are at most 8 MiB; document index rows 256 KiB;
  each binary object 100 MiB; at most 10,000 unique binary objects per document.
- Saves use atomic sparse record commits and optimistic concurrency. A durable
  operation ID makes retries safe after response loss. Independent annotations
  merge; ambiguous edits retain revisions; concurrent conversations branch.
- Apply all migrations (including 0002 and 0003) before the new Worker. Back up D1 first. On the first v2
  write, old v1 clients become read-only (409 on mutations). Existing snapshots
  remain readable and migrate lazily; source files are never replaced.
- Tombstones, operation acknowledgements and change logs are retained in this
  first release. Physical garbage collection and automatic history expiry are
  intentionally deferred.
- Trash/purge/delete remove D1 references. **R2 snapshots and blobs are retained**,
  including superseded versions and interrupted uploads. Automatic physical
  garbage collection is deferred to avoid deleting shared content or content
  being read concurrently. Plan for retention/storage growth. Do not add an R2
  age-based lifecycle rule: old blobs can still belong to current documents.
- Back up D1 and R2 together. Restoring just one can leave dangling references.
- Protect the service password as a library-wide read/write credential. This
  implementation provides one library per deployment, not multi-user tenancy.

Cloudflare references: [D1 sessions and batches](https://developers.cloudflare.com/d1/worker-api/d1-database/),
[R2 Worker API](https://developers.cloudflare.com/r2/api/workers/workers-api-reference/),
[custom domains](https://developers.cloudflare.com/workers/configuration/routing/custom-domains/).
