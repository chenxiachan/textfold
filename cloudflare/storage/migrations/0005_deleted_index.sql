-- Keep only synchronization receipts after permanent deletion, outside the
-- document index. Old revisions can never become a merge base for reimports.
CREATE TABLE document_deletions (
 id TEXT PRIMARY KEY, revision TEXT NOT NULL, sequence INTEGER NOT NULL,
 deleted_at REAL NOT NULL
);
INSERT INTO document_deletions
 SELECT d.id,d.revision,c.sequence,d.updated FROM sync_documents d
 JOIN change_log c ON c.document_id=d.id AND c.revision=d.revision
 WHERE d.purged=1;
DELETE FROM sync_documents WHERE purged=1
 AND EXISTS (SELECT 1 FROM document_deletions d WHERE d.id=sync_documents.id AND d.revision=sync_documents.revision);
