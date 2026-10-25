CREATE TABLE storage_identity (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1), library_id TEXT NOT NULL,
 sync_enabled INTEGER NOT NULL DEFAULT 0
);
INSERT INTO storage_identity VALUES (1, lower(hex(randomblob(16))), 0);
CREATE TABLE sync_documents (
 id TEXT PRIMARY KEY, revision TEXT NOT NULL, manifest_key TEXT NOT NULL,
 info_json TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('active','trash','deleted')),
 placement_json TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL
);
CREATE TABLE document_records (
 document_id TEXT NOT NULL, record_key TEXT NOT NULL, value_json TEXT,
 object_key TEXT, deleted INTEGER NOT NULL DEFAULT 0, revision TEXT NOT NULL,
 PRIMARY KEY(document_id, record_key)
);
CREATE VIEW annotations AS
 SELECT document_id, substr(record_key,12) AS annotation_id, value_json, deleted, revision
 FROM document_records WHERE record_key LIKE 'annotation/%';
CREATE VIEW messages AS
 SELECT document_id, record_key, value_json, deleted, revision
 FROM document_records WHERE record_key LIKE 'message/%';
CREATE TABLE sync_operations (
 operation_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL, document_id TEXT NOT NULL,
 revision TEXT NOT NULL, result_json TEXT NOT NULL
);
CREATE TABLE change_log (
 sequence INTEGER PRIMARY KEY AUTOINCREMENT, document_id TEXT NOT NULL,
 revision TEXT NOT NULL, state TEXT NOT NULL, operation_id TEXT NOT NULL UNIQUE
);
CREATE INDEX sync_document_changes ON change_log(document_id,sequence);
