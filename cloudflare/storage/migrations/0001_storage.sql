CREATE TABLE documents (
  id TEXT PRIMARY KEY,
  revision TEXT NOT NULL,
  manifest_key TEXT NOT NULL,
  info_json TEXT NOT NULL,
  created REAL NOT NULL,
  updated REAL NOT NULL
);

CREATE TABLE trash (
  token TEXT PRIMARY KEY,
  id TEXT NOT NULL,
  manifest_key TEXT NOT NULL,
  info_json TEXT NOT NULL,
  created REAL NOT NULL,
  deleted_at REAL NOT NULL,
  placement_json TEXT NOT NULL
);
CREATE INDEX trash_document ON trash(id, deleted_at DESC, token DESC);
CREATE INDEX trash_expiry ON trash(deleted_at);

-- A blob is registered only after its checksum-verified R2 upload completes.
-- Snapshot commits can validate all their references in one D1 query.
CREATE TABLE blobs (
  hash TEXT PRIMARY KEY,
  size INTEGER NOT NULL CHECK(size >= 0)
);

CREATE TABLE library_state (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
  revision TEXT NOT NULL,
  manifest_key TEXT NOT NULL
);
