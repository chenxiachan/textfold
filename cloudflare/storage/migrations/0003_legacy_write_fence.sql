CREATE TRIGGER legacy_document_insert_fence BEFORE INSERT ON documents
WHEN (SELECT sync_enabled FROM storage_identity WHERE singleton=1)=1
AND NOT EXISTS (SELECT 1 FROM sync_documents WHERE id=NEW.id AND revision=NEW.revision)
BEGIN SELECT RAISE(ABORT,'textfold_v2_write_required'); END;

CREATE TRIGGER legacy_document_update_fence BEFORE UPDATE ON documents
WHEN (SELECT sync_enabled FROM storage_identity WHERE singleton=1)=1
AND NOT EXISTS (SELECT 1 FROM sync_documents WHERE id=NEW.id AND revision=NEW.revision)
BEGIN SELECT RAISE(ABORT,'textfold_v2_write_required'); END;

CREATE TRIGGER legacy_document_delete_fence BEFORE DELETE ON documents
WHEN (SELECT sync_enabled FROM storage_identity WHERE singleton=1)=1
AND NOT EXISTS (SELECT 1 FROM sync_documents WHERE id=OLD.id AND state IN ('trash','deleted'))
BEGIN SELECT RAISE(ABORT,'textfold_v2_write_required'); END;

CREATE TRIGGER legacy_library_insert_fence BEFORE INSERT ON library_state
WHEN (SELECT sync_enabled FROM storage_identity WHERE singleton=1)=1
AND NOT EXISTS (SELECT 1 FROM sync_documents WHERE id='@library' AND revision=NEW.revision)
BEGIN SELECT RAISE(ABORT,'textfold_v2_write_required'); END;

CREATE TRIGGER legacy_library_update_fence BEFORE UPDATE ON library_state
WHEN (SELECT sync_enabled FROM storage_identity WHERE singleton=1)=1
AND NOT EXISTS (SELECT 1 FROM sync_documents WHERE id='@library' AND revision=NEW.revision)
BEGIN SELECT RAISE(ABORT,'textfold_v2_write_required'); END;

CREATE TRIGGER legacy_trash_insert_fence BEFORE INSERT ON trash
WHEN (SELECT sync_enabled FROM storage_identity WHERE singleton=1)=1
BEGIN SELECT RAISE(ABORT,'textfold_v2_write_required'); END;

CREATE TRIGGER legacy_trash_delete_fence BEFORE DELETE ON trash
WHEN (SELECT sync_enabled FROM storage_identity WHERE singleton=1)=1
BEGIN SELECT RAISE(ABORT,'textfold_v2_write_required'); END;
