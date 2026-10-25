-- Permanent deletion is terminal; keep the identity after removing its records.
ALTER TABLE sync_documents ADD COLUMN purged INTEGER NOT NULL DEFAULT 0;

-- A v2 transaction may consume legacy trash after recording the matching
-- operation. Old Worker requests remain fenced after activation.
DROP TRIGGER legacy_trash_delete_fence;
CREATE TRIGGER legacy_trash_delete_fence BEFORE DELETE ON trash
WHEN (SELECT sync_enabled FROM storage_identity WHERE singleton=1)=1
AND NOT EXISTS (
 SELECT 1 FROM sync_documents d JOIN sync_operations o ON o.document_id=d.id AND o.revision=d.revision
 WHERE d.id=OLD.id AND (json_extract(o.result_json,'$.consumed_trash')=OLD.token OR json_extract(o.result_json,'$.purged')=1)
)
BEGIN SELECT RAISE(ABORT,'textfold_v2_write_required'); END;
