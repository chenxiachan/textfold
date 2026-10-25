"""Offline persistence and record merge behavior, always in disposable libraries."""
import copy
import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

from textfold import sync_records as records
from textfold.cloud_storage import _pack, CloudStorageBackend, StorageError, UnavailableCloudStorage
from textfold.documents import ConflictError, Document, Source
from textfold.sqlite_storage import SQLiteBackend
from textfold.offline_storage import OfflineBackend
from test_storage import BackendContract


def values():
    return records.split(_pack(Document('md-test', meta={'id': 'md-test'}, source=Source(html='<p>Original</p>'),
                                       notes={'notes': [{'id': 'n-one', 'kind': 'note', 'anchor': {'unit': 'p1'}, 'text': 'Original', 'color': 'yellow'}]}))[0])


class RecordMerge(unittest.TestCase):
    def test_independent_annotations_and_fields_merge(self):
        base = values()
        local, remote = copy.deepcopy(base), copy.deepcopy(base)
        local['annotation/n-one']['text'] = 'Edited'
        remote['annotation/n-one']['color'] = 'blue'
        local['annotation/n-two'] = {'id': 'n-two', 'text': 'Another', 'anchor': {'unit': 'p2'}}
        merged, conflicts = records.reconcile(base, local, remote)
        self.assertEqual(conflicts, [])
        self.assertEqual(merged['annotation/n-one']['text'], 'Edited')
        self.assertEqual(merged['annotation/n-one']['color'], 'blue')
        self.assertIn('annotation/n-two', merged)

    def test_same_text_edit_keeps_both_revisions_and_delete_keeps_new_content(self):
        base = values()
        local, remote = copy.deepcopy(base), copy.deepcopy(base)
        local['annotation/n-one']['text'] = 'Phone'
        remote['annotation/n-one']['text'] = 'Laptop'
        merged, conflicts = records.reconcile(base, local, remote)
        self.assertEqual(len(conflicts), 1)
        conflict = merged['conflict/' + conflicts[0]]
        self.assertEqual({v['text'] for v in conflict['variants']}, {'Phone', 'Laptop'})
        del remote['annotation/n-one']
        merged, conflicts = records.reconcile(base, local, remote)
        self.assertNotIn('annotation/n-one', merged)
        self.assertTrue(merged['conflict/' + conflicts[0]]['deleted'])
        self.assertIn('Phone', records.canonical(merged))

    def test_equal_highlights_deduplicate_without_losing_link_aliases(self):
        local, remote = values(), values()
        for state, identity in ((local, 'n-a'), (remote, 'n-b')):
            state.pop('annotation/n-one')
            state['annotation/' + identity] = {'id': identity, 'kind': 'highlight', 'text': '', 'anchor': {'unit': 'p1', 'tokens': [1, 2]}}
        merged, _ = records.reconcile({}, local, remote)
        self.assertEqual(sum(k.startswith('annotation/') for k in merged), 1)
        self.assertEqual(merged['alias/n-b'], 'n-a')

    def test_message_forks_keep_separate_parent_contexts(self):
        note = {'id': 'n-chat', 'thread': [{'q': 'First', 'a': 'Answer'}]}
        nodes = records.messages(note)
        parent = nodes[-1]['id']
        nodes.extend([{'id': 'phone', 'parent': parent, 'role': 'user', 'text': 'Phone question'},
                      {'id': 'laptop', 'parent': parent, 'role': 'user', 'text': 'Laptop question'}])
        note['messages'] = nodes
        self.assertEqual(records.branches(note), ['laptop', 'phone'])
        self.assertEqual([t['q'] for t in records.conversation(note, 'phone')], ['First', 'Phone question'])
        self.assertEqual([t['q'] for t in records.conversation(note, 'laptop')], ['First', 'Laptop question'])

    def test_source_bytes_are_immutable_but_missing_original_can_be_added(self):
        before = values()
        changed = copy.deepcopy(before)
        changed['source/html'] = 'Replaced'
        self.assertFalse(records.original_unchanged(before, changed))
        changed = copy.deepcopy(before)
        changed['source/pdf'] = {'sha256': 'a' * 64, 'size': 10}
        self.assertTrue(records.original_unchanged(before, changed))

    def test_delete_conversation_vs_append_preserves_full_ancestry_for_restore(self):
        base = values()
        base['annotation/n-one']['kind'] = 'ask'
        nodes = records.messages({'id': 'n-one', 'thread': [{'q': 'First?', 'a': 'First answer'}]})
        base.update({'message/n-one/' + m['id']: m for m in nodes})
        deleted = {k: v for k, v in base.items() if not k.startswith(('annotation/', 'message/'))}
        changed = copy.deepcopy(base)
        changed['message/n-one/new'] = {'id': 'new', 'parent': nodes[-1]['id'], 'role': 'user', 'text': 'New question'}
        merged, conflicts = records.reconcile(base, deleted, changed)
        self.assertNotIn('annotation/n-one', merged)
        self.assertTrue(conflicts)
        restored = {'messages': [v for k, v in merged.items() if k.startswith('message/n-one/')]}
        self.assertEqual([t['q'] for t in records.conversation(restored, 'new')], ['First?', 'New question'])
        # Legacy directory snapshots transport whole notes, rather than hidden
        # SQL records. The deleted conversation must survive that round trip.
        round_trip = records.split(records.join('md-test', merged))
        conflict = round_trip['conflict/' + conflicts[0]]
        deleted = next(i for i, v in enumerate(conflict['variants']) if v is None)
        confirmed = records.resolve_conflict(round_trip, conflicts[0], deleted)
        self.assertFalse(any(k.startswith('conflict/') for k in confirmed), 'Serialization alone must not manufacture a later edit')
        selected = next(i for i, v in enumerate(conflict['variants']) if v is not None)
        restored = records.join('md-test', records.resolve_conflict(round_trip, conflicts[0], selected))['notes']['notes'][0]
        self.assertEqual([t['q'] for t in records.conversation(restored, 'new')], ['First?', 'New question'])

    def test_invalid_non_default_branch_is_rejected(self):
        with self.assertRaises(ValueError):
            records.branches({'messages': [
                {'id': 'a', 'parent': 'a', 'role': 'user', 'text': 'Cycle'},
                {'id': 'z', 'parent': None, 'role': 'user', 'text': 'Valid branch'}]})

    def test_hidden_new_branch_keeps_a_real_annotation_candidate_for_restore(self):
        base = values()
        base['annotation/n-one']['kind'] = 'ask'
        nodes = records.messages({'id': 'n-one', 'thread': [{'q': 'First?', 'a': 'Answer'}]})
        base.update({'message/n-one/' + n['id']: n for n in nodes})
        deleted = {k: v for k, v in base.items() if not k.startswith(('annotation/', 'message/'))}
        changed = copy.deepcopy(base)
        changed['message/n-one/q2'] = {'id': 'q2', 'parent': nodes[-1]['id'], 'role': 'user', 'text': 'Second?'}
        merged, ids = records.reconcile(base, deleted, changed)
        values_after_restart = records.split(records.join('md-test', merged))
        values_after_restart['message/n-one/q3'] = {'id': 'q3', 'parent': 'q2', 'role': 'user', 'text': 'New hidden branch'}
        conflict = values_after_restart['conflict/' + ids[0]]
        delete = next(i for i, v in enumerate(conflict['variants']) if v is None)
        resolved = records.resolve_conflict(values_after_restart, ids[0], delete)
        retained = next(v for k, v in resolved.items() if k.startswith('conflict/'))
        live = next(i for i, v in enumerate(retained['variants']) if v is not None)
        restored = records.join('md-test', records.resolve_conflict(resolved, retained['id'], live))['notes']['notes'][0]
        self.assertEqual([t['q'] for t in records.conversation(restored, 'q3')], ['First?', 'Second?', 'New hidden branch'])
        delete = next(i for i, v in enumerate(retained['variants']) if v is None)
        self.assertFalse(any(k.startswith('conflict/') for k in records.resolve_conflict(resolved, retained['id'], delete)))

    def test_restoring_an_archive_does_not_replace_a_completed_answer_with_its_pending_draft(self):
        value = values()
        note = value['annotation/n-one']
        note['kind'] = 'ask'
        draft = [{'id': 'q1', 'parent': None, 'role': 'user', 'text': 'Question'},
                 {'id': 'a1', 'parent': 'q1', 'role': 'assistant', 'text': '', 'pending': True}]
        value.update({'message/n-one/' + n['id']: copy.deepcopy(n) for n in draft})
        value['message/n-one/a1'].update(text='Completed answer', pending=False)
        value['conflict/c-old'] = {'id': 'c-old', 'key': 'annotation/n-one', 'variants': [None, copy.deepcopy(note)],
                                   'fields': ['messages'], 'deleted': True, 'messages': draft}
        result = records.join('md-test', records.resolve_conflict(value, 'c-old', 1))['notes']['notes'][0]
        self.assertEqual(records.conversation(result, 'a1')[-1]['a'], 'Completed answer')

    def test_library_round_trip_keeps_order_empty_maps_and_activity_is_not_a_content_conflict(self):
        from textfold.documents import LibraryState
        state = LibraryState({'folders': [{'id': 'f-z', 'name': 'First'}, {'id': 'f-a', 'name': 'Second'}],
                              'of': {}, 'custom': {}}, {})
        self.assertEqual(records.library_value(records.library_records(state)).catalog, state.catalog)
        merged, conflicts = records.reconcile({'catalog.opened/md-test': 1}, {'catalog.opened/md-test': 3}, {'catalog.opened/md-test': 2})
        self.assertEqual(merged['catalog.opened/md-test'], 3)
        self.assertEqual(conflicts, [])


class SQLiteDocuments(BackendContract, unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.backend = SQLiteBackend(self.root)

    def test_explicit_generate_after_permanent_delete_refetches_same_arxiv_id(self):
        from textfold import pipeline, store
        identity = '2602.01219'
        self.backend.save(Document(identity, meta={'id': identity, 'title': 'Old'},
                                   source=Source(html='Old source'), notes={'notes': [{'text': 'Old note'}]}))
        self.backend.trash(identity); self.backend.delete_trashed(identity)
        def fetch_again(pid, root):
            with store.edit(pid, create=True) as doc:
                doc.meta = {'id': pid, 'title': 'Fetched again'}
                doc.source.html = 'Freshly fetched original'
        with store.use_backend(self.backend), mock.patch.object(pipeline, 'fetch', side_effect=fetch_again), \
                mock.patch.object(store, 'parsed', side_effect=RuntimeError('Reached parsing')):
            with self.assertRaisesRegex(RuntimeError, 'Reached parsing'):
                pipeline.generate('https://arxiv.org/abs/' + identity, 'en', None)
        fresh = self.backend.get(identity)
        self.assertEqual(fresh.source.html, 'Freshly fetched original')
        self.assertIsNone(fresh.notes)

    def test_replace_preserves_notes_and_previous_original(self):
        doc = Document('md-test', meta={'id': 'md-test'}, source=Source(markdown='Original', html='Original'))
        self.backend.save(doc)
        doc.source.markdown = 'Replacement'
        with self.assertRaisesRegex(ValueError, 'immutable'):
            self.backend.save(doc)
        self.assertEqual(self.backend.get(doc.id).source.markdown, 'Original')

    def test_parser_rejects_overlapping_concurrent_edits(self):
        # Raw source replacement itself is now rejected before publication.
        self.test_replace_preserves_notes_and_previous_original()

    def test_offline_save_and_outbox_survive_restart(self):
        backend = SQLiteBackend(self.root, tracking=True)
        doc = Document('md-test', meta={'id': 'md-test'}, source=Source(pdf=b'Original'))
        backend.save(doc)
        with backend.transaction() as db:
            row = db.execute('SELECT * FROM pending_changes').fetchone()
            self.assertEqual(row['generation'], doc.revision)
            self.assertGreater(row['modified_at'], 0)
        reopened = SQLiteBackend(self.root, tracking=True)
        self.assertEqual(reopened.get(doc.id).source.pdf, b'Original')
        self.assertEqual(reopened.status()['pending'], 1)

    def test_failed_local_transaction_never_reports_new_content_or_clears_the_outbox(self):
        import sqlite3
        backend = SQLiteBackend(self.root, tracking=True)
        doc = Document('md-test', meta={'id': 'md-test'}, source=Source(html='Original'), notes={'notes': []})
        backend.save(doc)
        before = backend.get(doc.id)
        with backend.transaction() as db:
            db.execute("CREATE TRIGGER fail_outbox BEFORE INSERT ON pending_changes BEGIN SELECT RAISE(ABORT,'disk full fixture'); END")
        doc.notes['notes'].append({'id': 'n-new', 'anchor': {}, 'text': 'Uncommitted'})
        with self.assertRaises(sqlite3.DatabaseError):
            backend.save(doc)
        self.assertEqual(backend.get(doc.id).notes, before.notes)
        self.assertEqual(backend.get(doc.id).revision, before.revision)
        self.assertEqual(backend.status()['pending'], 1)

    def test_trash_schema_upgrade_retains_original_bytes_and_outbox(self):
        backend = SQLiteBackend(self.root, tracking=True)
        doc = Document('md-upgrade', meta={'title': 'Old workspace'}, source=Source(pdf=b'Original bytes'))
        backend.save(doc); backend.trash(doc.id)
        with backend.transaction() as db:
            db.execute('ALTER TABLE documents DROP COLUMN purged')
            db.execute('ALTER TABLE documents DROP COLUMN purge_base')
        backend = SQLiteBackend(self.root, tracking=True)
        self.assertEqual(backend.list_trash()[0]['id'], doc.id)
        with self.assertRaises(ConflictError): backend.delete_trashed(doc.id)
        self.assertEqual(backend.purge(float('inf')), [])
        self.assertEqual(backend.restore(doc.id), {})
        self.assertEqual(backend.get(doc.id).source.pdf, b'Original bytes')
        with backend.transaction() as db:
            self.assertIsNotNone(db.execute('SELECT generation FROM pending_changes WHERE document_id=?', (doc.id,)).fetchone())

    def test_sqlite_permanent_delete_preserves_active_and_other_documents(self):
        first = Document('md-remove', meta={'title': 'Remove'}, source=Source(html='Remove'))
        other = Document('md-keep', meta={'title': 'Keep'}, source=Source(html='Keep'))
        self.backend.save(first); self.backend.save(other)
        self.assertFalse(self.backend.delete_trashed(other.id))
        self.backend.trash(first.id)
        with self.assertRaises(ConflictError): self.backend.delete_trashed(first.id, 'stale')
        entry = self.backend.list_trash()[0]
        self.assertTrue(self.backend.delete_trashed(first.id, entry['revision']))
        self.assertEqual(self.backend.list_trash(), [])
        self.assertEqual(self.backend.get(other.id).source.html, 'Keep')
        with self.assertRaises(ConflictError): self.backend.save(Document(first.id))

    def test_uncertain_only_recovery_export_keeps_local_records_and_attachments(self):
        import io
        import zipfile
        remote = UnavailableCloudStorage({}, 'Offline fixture')
        backend = OfflineBackend(self.root, remote)
        doc = Document('md-uncertain', meta={'title': 'Recovery'}, source=Source(pdf=b'Protected original'),
                       notes={'notes': [{'id': 'n-one', 'text': 'Protected edit', 'anchor': {}}]})
        backend.save(doc)
        with backend.cache.transaction() as db:
            values = backend.cache.values(db, doc.id)
            generation = db.execute('SELECT generation FROM pending_changes WHERE document_id=?', (doc.id,)).fetchone()[0]
            db.execute('INSERT INTO pending_requests VALUES (?,?,?,?,?,?,?)',
                       (doc.id, 'uncertain-operation', '{}', records.canonical(values), generation, '{}', 'active'))
            # Exercise a recovered older outbox with only its uncertain request.
            db.execute('DELETE FROM pending_changes WHERE document_id=?', (doc.id,))
        backend._accept_purge(doc.id, {'revision': str(uuid.uuid4()), 'purged': True})
        backend.clear_cache()
        with zipfile.ZipFile(io.BytesIO(backend.export_pending())) as archive:
            self.assertIn('Protected edit', archive.read('pending.json').decode())
            self.assertIn(b'Protected original', [archive.read(name) for name in archive.namelist() if name.startswith('blobs/')])
        self.assertEqual(backend.status()['pending'], 1)


class ConflictResolution(unittest.TestCase):
    def test_old_delete_conflict_retains_later_text_and_message_branches(self):
        from textfold import notes, store
        from textfold.storage import LocalDirectoryBackend
        from textfold.cloud_storage import _unpack
        for mode in ('local', 'offline'):
            for edit in ('text', 'message'):
                with self.subTest(mode=mode, edit=edit), tempfile.TemporaryDirectory() as root:
                    base = values()
                    base['annotation/n-one']['kind'] = 'ask'
                    nodes = records.messages({'id': 'n-one', 'thread': [{'q': 'First?', 'a': 'First answer'}]})
                    base.update({'message/n-one/' + n['id']: n for n in nodes})
                    deleted = {k: v for k, v in base.items() if not k.startswith(('annotation/', 'message/'))}
                    changed = copy.deepcopy(base)
                    changed['annotation/n-one']['text'] = 'Remote edit'
                    changed['message/n-one/q2'] = {'id': 'q2', 'parent': nodes[-1]['id'], 'role': 'user', 'text': 'Second question'}
                    merged, ids = records.reconcile(base, deleted, changed)
                    ordinary = next(c for c in (merged['conflict/' + i] for i in ids) if not c['fields'])
                    message = next(c for c in (merged['conflict/' + i] for i in ids) if c['fields'] == ['messages'])
                    backend = (LocalDirectoryBackend(root) if mode == 'local' else
                               OfflineBackend(root, UnavailableCloudStorage({'url': 'https://fixture.invalid'}, 'offline')))
                    # Old local conflict snapshots have the ancestry archive but
                    # no fingerprint; they must get the same protection.
                    if mode == 'offline':
                        with backend.cache.transaction() as db:
                            backend.cache.write(db, 'md-test', merged)
                    else:
                        for c in (ordinary, message):
                            c.pop('message_revision', None)
                        backend.save(_unpack(records.join('md-test', merged), lambda *_: b''))
                    with store.use_backend(backend):
                        resolve = notes.resolve if mode == 'local' else backend.resolve
                        live = next(i for i, v in enumerate(ordinary['variants']) if v is not None)
                        resolve('md-test', ordinary['id'], live)
                        doc = backend.get('md-test')
                        if edit == 'text':
                            doc.notes['notes'][0]['text'] = 'Later saved important research'
                        else:
                            doc.notes['notes'][0]['messages'].append({'id': 'q3', 'parent': 'q2', 'role': 'user', 'text': 'Later saved branch'})
                        backend.save(doc)
                        delete = next(i for i, v in enumerate(message['variants']) if v is None)
                        resolve('md-test', message['id'], delete)
                        result = backend.get('md-test').notes
                        self.assertEqual(result['notes'], [])
                        self.assertEqual(len(result['conflicts']), 1)
                        retained = result['conflicts'][0]
                        live = next(i for i, v in enumerate(retained['variants']) if v is not None)
                        resolve('md-test', retained['id'], live)
                        recovered = backend.get('md-test').notes['notes'][0]
                        if edit == 'text':
                            self.assertEqual(recovered['text'], 'Later saved important research')
                        else:
                            self.assertEqual(next(n['text'] for n in recovered['messages'] if n['id'] == 'q3'), 'Later saved branch')
                        self.assertEqual(records.conversation(recovered, 'q2')[-1]['q'], 'Second question')

    def test_resolution_retains_edit_saved_after_conflict_in_both_backends(self):
        from textfold import notes, store
        from textfold.storage import LocalDirectoryBackend
        from textfold.cloud_storage import _unpack
        base = values()
        phone, laptop = copy.deepcopy(base), copy.deepcopy(base)
        phone['annotation/n-one']['text'] = 'Phone'
        laptop['annotation/n-one']['text'] = 'Laptop'
        merged, ids = records.reconcile(base, phone, laptop)
        for mode in ('local', 'offline'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                backend = (LocalDirectoryBackend(root) if mode == 'local' else
                           OfflineBackend(root, UnavailableCloudStorage({'url': 'https://fixture.invalid'}, 'offline')))
                doc = _unpack(records.join('md-test', merged), lambda *_: self.fail('No binary fixture'))
                doc.source.pdf = b'Original PDF'
                doc.extensions['plugin/private.bin'] = b'Unknown extension data'
                backend.save(doc)
                doc = backend.get(doc.id)
                doc.notes['notes'][0].update(text='Later saved edit', color='blue')
                backend.save(doc)
                with store.use_backend(backend):
                    resolve = notes.resolve if mode == 'local' else backend.resolve
                    resolve(doc.id, ids[0], 0)
                    result = backend.get(doc.id)
                    self.assertEqual(result.notes['notes'][0]['color'], 'blue')
                    conflicts = result.notes['conflicts']
                    self.assertEqual(len(conflicts), 1)
                    self.assertEqual({v['text'] for v in conflicts[0]['variants']}, {'Laptop', 'Later saved edit'})
                    reopened = (LocalDirectoryBackend(root) if mode == 'local' else
                                OfflineBackend(root, UnavailableCloudStorage({'url': 'https://fixture.invalid'}, 'offline')))
                    self.assertEqual(reopened.get(doc.id).notes['conflicts'], conflicts)
                    self.assertEqual(reopened.get(doc.id).source.pdf, b'Original PDF')
                    self.assertEqual(reopened.get(doc.id).extensions['plugin/private.bin'], b'Unknown extension data')
                    later = next(i for i, v in enumerate(conflicts[0]['variants']) if v['text'] == 'Later saved edit')
                    resolve(doc.id, conflicts[0]['id'], later)
                    self.assertEqual(backend.get(doc.id).notes['notes'][0]['text'], 'Later saved edit')
                    self.assertEqual(backend.get(doc.id).notes.get('conflicts'), [])


@unittest.skipUnless(os.environ.get('TEXTFOLD_TEST_STORAGE_URL'), 'run cloudflare/storage npm test')
class OfflineDevices(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.identity = 'md-' + uuid.uuid4().hex
        self.a, self.b = self.device('a'), self.device('b')
        self.doc = Document(self.identity, meta={'id': self.identity, 'title': 'Offline'},
                            source=Source(html='<p>Immutable original</p>', pdf=b'%PDF-original'),
                            notes={'notes': [{'id': 'n-one', 'kind': 'note', 'text': 'Original', 'anchor': {'unit': 'p1'}}]})

    def device(self, name):
        remote = CloudStorageBackend(os.environ['TEXTFOLD_TEST_STORAGE_URL'], os.environ['TEXTFOLD_TEST_STORAGE_USER'], os.environ['TEXTFOLD_TEST_STORAGE_PASSWORD'])
        return OfflineBackend(self.root / name, remote)

    def sync(self, device):
        status = device.sync()
        self.assertFalse(status['error'], status)
        self.assertEqual(status['pending'], 0, status)

    def seed(self):
        self.a.save(self.doc)
        self.sync(self.a)
        self.sync(self.b)
        self.b.get(self.identity)

    def trash_entry(self, device):
        return next(row for row in device.list_trash() if row['id'] == self.identity)

    def permanently_delete(self):
        self.a.trash(self.identity); self.sync(self.a)
        self.a.delete_trashed(self.identity, self.trash_entry(self.a)['revision']); self.sync(self.a)

    def import_again(self, device, source='Fetched again'):
        device.prepare_import(self.identity)
        doc = device.get(self.identity)
        self.assertIsNone(doc.meta)
        doc.meta = {'id': self.identity, 'title': 'Reimported'}
        doc.source = Source(html=source)
        doc.notes = {'notes': [{'id': 'n-new', 'text': 'New note', 'anchor': {}}]}
        device.save(doc)
        return doc

    def test_explicit_reimport_same_id_is_clean_and_reaches_other_devices(self):
        self.seed(); self.permanently_delete(); self.sync(self.b)
        self.import_again(self.a); self.sync(self.a); self.sync(self.b)
        for device in (self.a, self.b):
            doc = device.get(self.identity)
            self.assertEqual(doc.id, self.identity)
            self.assertEqual(doc.source.html, 'Fetched again')
            self.assertEqual([n['id'] for n in doc.notes['notes']], ['n-new'])
        self.assertTrue(any(d['id'] == self.identity for d in self.a.remote.sync_index()))

    def test_offline_notes_before_delete_never_merge_into_reimport(self):
        import io, zipfile
        self.seed()
        old = self.b.get(self.identity); old.notes['notes'][0]['text'] = 'Protected old offline note'
        self.b.save(old)
        self.permanently_delete(); self.import_again(self.a); self.sync(self.a)
        status = self.b.sync()
        self.assertGreater(status['pending'], 0)
        self.assertTrue(self.trash_entry(self.b)['recovery'])
        with self.assertRaises(ConflictError): self.b.prepare_import(self.identity)
        self.b.clear_cache()
        with zipfile.ZipFile(io.BytesIO(self.b.export_pending())) as archive:
            self.assertIn('Protected old offline note', archive.read('pending.json').decode())
        self.assertEqual([n['id'] for n in self.a.get(self.identity).notes['notes']], ['n-new'])
        self.assertNotIn('Protected old offline note', records.canonical(self.a.remote.sync_snapshot(self.identity)))

    def test_lost_reimport_ack_and_restart_keep_edits_without_old_notes(self):
        self.seed(); self.permanently_delete(); self.import_again(self.a)
        commit = self.a.remote.sync_commit
        def lose(identity, payload):
            commit(identity, payload)
            raise StorageError('Lost reimport acknowledgement')
        with mock.patch.object(self.a.remote, 'sync_commit', side_effect=lose):
            self.assertIn('Lost reimport', self.a.sync()['error'])
        doc = self.a.get(self.identity); doc.notes['notes'][0]['text'] = 'Edit after lost response'
        self.a.save(doc)
        self.a = self.device('a')
        self.assertFalse(self.a.sync()['error'])  # acknowledge the first request before publishing later edits
        self.sync(self.a); self.sync(self.b)
        self.assertEqual(self.b.get(self.identity).notes['notes'][0]['text'], 'Edit after lost response')
        self.assertEqual(len(self.b.get(self.identity).notes['notes']), 1)

    def test_uncached_deleted_document_can_be_explicitly_imported_again(self):
        self.seed(); self.permanently_delete()
        fresh = self.device('new-device')
        self.import_again(fresh); self.sync(fresh); self.sync(self.a)
        self.assertEqual(self.a.get(self.identity).source.html, 'Fetched again')

    def test_legacy_missing_index_marker_is_refreshed_before_explicit_reimport(self):
        self.seed(); self.permanently_delete()
        with self.a.cache.transaction() as db:
            db.execute('UPDATE documents SET purged=0 WHERE id=?', (self.identity,))
        self.import_again(self.a); self.sync(self.a)
        self.assertEqual(self.a.get(self.identity).source.html, 'Fetched again')

    def test_cloud_generate_after_purge_uses_same_arxiv_id_without_old_content(self):
        from textfold import pipeline, store
        self.identity = self.doc.id = '2602.01219'
        self.doc.meta['id'] = self.identity
        self.seed(); self.permanently_delete()
        def fetch_again(pid, root):
            with store.edit(pid, create=True) as doc:
                doc.meta = {'id': pid, 'title': 'Fetched again'}
                doc.source.html = 'Fresh original from arXiv'
        with store.use_backend(self.a), mock.patch.object(pipeline, 'fetch', side_effect=fetch_again), \
                mock.patch.object(store, 'parsed', side_effect=RuntimeError('Reached parsing')):
            with self.assertRaisesRegex(RuntimeError, 'Reached parsing'):
                pipeline.generate('https://arxiv.org/abs/' + self.identity, 'en', None)
        self.sync(self.a)
        self.assertIsNone(self.a.get(self.identity).notes)
        self.assertEqual(self.a.remote.sync_snapshot(self.identity)['records']['source/html'], 'Fresh original from arXiv')

    def test_competing_reimport_keeps_losing_local_source_for_recovery(self):
        import io, zipfile
        self.seed(); self.permanently_delete(); self.sync(self.b)
        self.import_again(self.a, 'Source from A'); self.import_again(self.b, 'Source from B')
        self.sync(self.a)
        self.assertTrue(self.b.sync()['error'])
        with zipfile.ZipFile(io.BytesIO(self.b.export_pending())) as archive:
            self.assertIn('Source from B', archive.read('pending.json').decode())
        self.assertEqual(self.a.get(self.identity).source.html, 'Source from A')

    def test_permanent_deletion_requires_clean_trash_and_is_terminal_across_restart(self):
        self.seed()
        self.assertFalse(self.a.delete_trashed(self.identity))
        self.a.trash(self.identity, {'of': 'folder'})
        with self.assertRaises(ConflictError):
            self.a.delete_trashed(self.identity)
        self.sync(self.a); self.sync(self.b)
        self.assertEqual(self.trash_entry(self.b)['title'], 'Offline')
        self.assertTrue(self.a.delete_trashed(self.identity, self.trash_entry(self.a)['revision']))
        self.assertFalse(self.trash_entry(self.a)['restorable'])
        self.a = self.device('a')
        self.sync(self.a); self.sync(self.b)
        self.assertFalse(any(row['id'] == self.identity for row in self.a.list_trash()))
        state = self.a.remote.sync_snapshot(self.identity)
        self.assertTrue(state['purged']); self.assertEqual(state['records'], {})
        self.assertIsNone(self.b.restore(self.identity))
        with self.assertRaises(ConflictError):
            self.b.remote.sync_commit(self.identity, {'operation_id': str(uuid.uuid4()), 'revision': state['revision'],
                                                      'changes': [], 'state': 'active', 'placement': {}})
        self.a.clear_cache(); self.b.clear_cache()
        with self.assertRaises(FileNotFoundError): self.b.get(self.identity)

    def test_remote_purge_keeps_offline_notes_uncertain_requests_and_export(self):
        import io
        import zipfile
        self.seed()
        doc = self.b.get(self.identity)
        doc.notes['notes'][0]['text'] = 'Keep my offline work'
        self.b.save(doc)
        with self.b.cache.transaction() as db:
            pending = db.execute('SELECT generation FROM pending_changes WHERE document_id=?', (self.identity,)).fetchone()[0]
            snapshot = self.b.cache.values(db, self.identity)
            operation = str(uuid.uuid4())
            payload = {'operation_id': operation, 'revision': 'old', 'changes': [], 'state': 'active', 'placement': {}}
            db.execute('INSERT INTO pending_requests VALUES (?,?,?,?,?,?,?)', (self.identity, operation, records.canonical(payload), records.canonical(snapshot), pending, records.canonical(snapshot), 'active'))
        self.a.trash(self.identity); self.sync(self.a)
        self.a.delete_trashed(self.identity, self.trash_entry(self.a)['revision']); self.sync(self.a)
        self.b = self.device('b')
        with mock.patch.object(self.b.remote, 'sync_commit', wraps=self.b.remote.sync_commit) as commit:
            status = self.b.sync()
        commit.assert_not_called()
        self.assertGreater(status['pending'], 0)
        self.assertTrue(self.trash_entry(self.b)['recovery'])
        self.assertFalse(self.trash_entry(self.b)['can_delete'])
        self.b.clear_cache()
        self.assertEqual(self.b.purge(float('inf')), [])
        with zipfile.ZipFile(io.BytesIO(self.b.export_pending())) as archive:
            data = archive.read('pending.json').decode()
            self.assertIn('Keep my offline work', data)
            self.assertIn(operation, data)
            self.assertEqual(archive.read('blobs/' + next(iter(blob for blob in archive.namelist() if blob.startswith('blobs/'))).split('/')[-1]), self.doc.source.pdf)
        with self.b.cache.transaction() as db:
            self.assertEqual(db.execute('SELECT operation_id FROM pending_requests WHERE document_id=?', (self.identity,)).fetchone()[0], operation)
        self.assertTrue(self.b.remote.sync_snapshot(self.identity)['purged'])

    def test_lost_purge_ack_replays_after_restart_without_recovery_or_resurrection(self):
        self.seed(); self.a.trash(self.identity); self.sync(self.a)
        self.a.delete_trashed(self.identity, self.trash_entry(self.a)['revision'])
        commit = self.a.remote.sync_commit
        def lose_ack(identity, payload):
            commit(identity, payload)
            raise StorageError('Lost purge acknowledgement')
        with mock.patch.object(self.a.remote, 'sync_commit', side_effect=lose_ack):
            self.assertIn('Lost purge', self.a.sync()['error'])
        self.a = self.device('a')
        self.sync(self.a)
        self.assertFalse(any(row['id'] == self.identity for row in self.a.list_trash()))
        with self.a.cache.transaction() as db:
            self.assertFalse(db.execute('SELECT 1 FROM records WHERE document_id=?', (self.identity,)).fetchone())

    def test_concurrent_restore_cancels_queued_permanent_delete(self):
        self.seed(); self.a.trash(self.identity); self.sync(self.a); self.sync(self.b)
        self.a.delete_trashed(self.identity, self.trash_entry(self.a)['revision'])
        self.assertIsNotNone(self.b.restore(self.identity)); self.sync(self.b)
        status = self.a.sync()
        self.assertIn('cancelled', status['error'])
        self.sync(self.a)
        self.assertEqual(self.a.get(self.identity).source.pdf, self.doc.source.pdf)
        self.assertFalse(self.a.remote.sync_snapshot(self.identity)['purged'])

    def test_permanent_delete_checks_service_capability_and_stale_local_revision(self):
        self.seed(); self.a.trash(self.identity); self.sync(self.a)
        entry = self.trash_entry(self.a)
        with mock.patch.object(self.a.remote, 'sync_health', return_value={'format': 'textfold/storage-v2', 'library_id': self.a.remote.sync_health()['library_id'], 'cursor': self.a.remote.sync_health()['cursor']}):
            with self.assertRaises(StorageError): self.a.delete_trashed(self.identity)
        with self.assertRaises(ConflictError): self.a.delete_trashed(self.identity, 'stale')
        with mock.patch.object(self.a.remote, 'sync_health', side_effect=StorageError('offline')):
            with self.assertRaises(StorageError): self.a.delete_trashed(self.identity, entry['revision'])
        self.assertEqual(self.trash_entry(self.a)['revision'], entry['revision'])

    def test_purge_during_generation_keeps_checkpoints_and_paid_replies_for_export(self):
        import io
        import zipfile
        self.seed()
        lease = self.b.defer_sync(self.identity)
        lease.__enter__()
        try:
            doc = self.b.get(self.identity)
            self.a.trash(self.identity); self.sync(self.a)
            self.a.delete_trashed(self.identity, self.trash_entry(self.a)['revision']); self.sync(self.a)
            self.b.sync()  # observes terminal identity while the lease is held
            doc.processing.replies['paid-answer'] = b'Paid model output'
            self.b.save(doc)
        finally:
            lease.__exit__(None, None, None)
        self.b = self.device('b')
        self.assertGreater(self.b.sync()['pending'], 0)
        self.b.clear_cache()
        with zipfile.ZipFile(io.BytesIO(self.b.export_pending())) as archive:
            self.assertIn('paid-answer', archive.read('pending.json').decode())
            self.assertIn(b'Paid model output', [archive.read(name) for name in archive.namelist() if name.startswith('blobs/')])
        self.assertTrue(self.b.remote.sync_snapshot(self.identity)['purged'])

    def test_cloud_transport_trash_interface_and_metadata_listing(self):
        self.seed(); self.a.trash(self.identity); self.sync(self.a)
        remote = self.a.remote
        with mock.patch.object(remote, '_download', side_effect=AssertionError('Listing loaded a blob')):
            entry = next(row for row in remote.list_trash() if row['id'] == self.identity)
        self.assertEqual(remote.restore(self.identity, entry['revision']), {})
        self.sync(self.a)
        self.a.trash(self.identity); self.sync(self.a)
        entry = next(row for row in remote.list_trash() if row['id'] == self.identity)
        self.assertTrue(remote.delete_trashed(self.identity, entry['revision']))
        self.sync(self.a); self.sync(self.b)
        self.assertTrue(remote.sync_snapshot(self.identity)['purged'])

    def test_uncached_trash_purge_is_durable_without_downloading_original(self):
        self.seed(); self.a.trash(self.identity); self.sync(self.a); self.sync(self.b)
        self.b.clear_cache(); self.b = self.device('b')
        entry = self.trash_entry(self.b)
        with mock.patch.object(self.b.remote, '_download', side_effect=AssertionError('Purge downloaded a blob')):
            self.assertTrue(self.b.delete_trashed(self.identity, entry['revision']))
            self.assertEqual(self.trash_entry(self.b)['title'], 'Offline')
            self.b = self.device('b')
            self.sync(self.b)
        self.assertTrue(self.b.remote.sync_snapshot(self.identity)['purged'])

    def test_offline_edit_restart_and_two_device_independent_merge(self):
        self.seed()
        for device, identity in ((self.a, 'n-phone'), (self.b, 'n-laptop')):
            doc = device.get(self.identity)
            doc.notes['notes'].append({'id': identity, 'kind': 'note', 'text': identity, 'anchor': {'unit': 'p2'}})
            with mock.patch.object(device.remote, 'sync_health', side_effect=StorageError('offline')):
                device.save(doc)
                self.assertEqual(device.sync()['pending'], 1)
        self.a = self.device('a')
        self.sync(self.a)
        self.sync(self.b)
        self.sync(self.a)
        self.assertEqual({n['id'] for n in self.a.get(self.identity).notes['notes']}, {'n-one', 'n-phone', 'n-laptop'})
        self.assertEqual(self.a.get(self.identity).notes, self.b.get(self.identity).notes)

    def test_native_language_overlay_roundtrips_without_rewriting_paid_messages(self):
        from textfold import store, notes, sync_records
        self.doc.layers = {'en':{'lang':'zh'}}
        self.doc.notes = {'notes':[{'id':'n-old','kind':'ask','anchor':{'on':'paper','lang':'en'},
            'thread':[{'q':'Why?', 'a':'Paid reply', 'lang':'en'}]}]}
        self.seed()
        before = copy.deepcopy(self.a.get(self.identity).notes['notes'][0]['messages'])
        with store.use_backend(self.a):
            notes.settle_base(self.identity, 'zh')
        self.sync(self.a); self.sync(self.b)
        seen = self.b.get(self.identity).notes['notes'][0]
        self.assertEqual(seen['messages'], before)
        self.assertEqual(seen['thread'][0]['lang'], 'zh')
        self.assertEqual(len(sync_records.branches(seen)), 1)
        with store.use_backend(self.b):
            seen['thread'].append({'q':'In English?', 'a':'English reply', 'lang':'en'})
            notes.put(self.identity, seen)
        self.sync(self.b); self.sync(self.a)
        self.a = self.device('a')
        seen = self.a.get(self.identity).notes['notes'][0]
        self.assertEqual([t['lang'] for t in seen['thread']], ['zh','en'])
        self.assertEqual([m for m in seen['messages'] if m['id'] in {old['id'] for old in before}], before)
        self.assertEqual(len(seen['messages']), 4)
        self.assertEqual(len(sync_records.branches(seen)), 1)

    def test_new_import_is_durable_while_offline(self):
        from textfold import store, local
        with mock.patch.object(self.a.remote, 'sync_health', side_effect=StorageError('offline')), store.use_backend(self.a):
            identity = local.import_text('# New offline import\n\nContent saved without network.', 'offline.md')
            self.assertIn('saved without network', store.document(identity).source.markdown)
        self.assertGreater(self.a.status()['pending'], 0)
        self.sync(self.a); self.sync(self.b)
        self.assertIn('saved without network', self.b.get(identity).source.markdown)

    def test_generation_batches_remote_publication_but_keeps_local_checkpoints(self):
        self.seed()
        with self.a.defer_sync(self.identity):
            partial = self.a.get(self.identity)
            partial.layers['en'] = {'version':'completed-native'}
            self.a.save(partial)
            result = self.a.sync()
            self.assertFalse(result['error'], result)
            self.assertGreater(result['pending'], 0)
            self.sync(self.b)
            self.assertNotIn('en', self.b.get(self.identity).layers)
            other = Document('md-independent-'+uuid.uuid4().hex, meta={'title':'Other'}, source=Source(html='Other'))
            self.a.save(other)
            self.assertFalse(self.a.sync()['error'])
            self.sync(self.b)
            self.assertEqual(self.b.get(other.id).source.html, 'Other')
            complete = self.a.get(self.identity)
            complete.layers['tr-zh'] = {'version':'completed-translation'}
            self.a.save(complete)
        self.sync(self.a); self.sync(self.b)
        self.assertEqual(self.b.get(self.identity).layers, complete.layers)
        self.assertEqual(self.b.get(self.identity).source.pdf, self.doc.source.pdf)

    def test_generation_start_during_snapshot_fetch_prevents_partial_upload(self):
        self.seed()
        changed = self.a.get(self.identity)
        changed.meta['title'] = 'Local change'
        self.a.save(changed)
        actual = self.a._snapshot
        lease = self.a.defer_sync(self.identity)
        started = False
        def racing(identity):
            nonlocal started
            result = actual(identity)
            if identity == self.identity and not started:
                lease.__enter__()
                started = True
            return result
        try:
            with mock.patch.object(self.a, '_snapshot', racing), mock.patch.object(self.a.remote, 'sync_commit', wraps=self.a.remote.sync_commit) as commit:
                self.a._push(self.identity)
                self.assertTrue(started)
                self.assertEqual(commit.call_count, 0)
        finally:
            if started: lease.__exit__(None,None,None)
        self.sync(self.a); self.sync(self.b)
        self.assertEqual(self.b.get(self.identity).meta['title'], 'Local change')

    def test_metadata_only_trash_can_be_restored_after_cache_cleanup_and_restart(self):
        self.seed()
        self.a.trash(self.identity, {'of': 'f-kept'}); self.sync(self.a); self.sync(self.b)
        self.b.clear_cache()
        self.b = self.device('b')
        self.assertEqual(self.b.restore(self.identity), {'of': 'f-kept'})
        self.sync(self.b); self.sync(self.a)
        self.assertEqual(self.a.get(self.identity).source.pdf, self.doc.source.pdf)

    def test_concurrent_text_keeps_revisions_and_can_resolve(self):
        self.seed()
        for device, text in ((self.a, 'Phone'), (self.b, 'Laptop')):
            doc = device.get(self.identity)
            doc.notes['notes'][0]['text'] = text
            device.save(doc)
        self.sync(self.a)
        self.sync(self.b)
        self.sync(self.a)
        conflicts = [c for c in self.b.conflicts() if c['document'] == self.identity]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual({v['text'] for v in conflicts[0]['variants']}, {'Phone', 'Laptop'})
        self.b.resolve(self.identity, conflicts[0]['id'], 0, 'Combined note')
        self.sync(self.b)
        self.sync(self.a)
        self.assertEqual(self.a.get(self.identity).notes['notes'][0]['text'], 'Combined note')
        self.assertEqual([c for c in self.a.conflicts() if c['document'] == self.identity], [])

    def test_lost_ack_retries_same_operation_without_losing_new_edit(self):
        self.a.save(self.doc)
        actual = self.a.remote.sync_commit
        def lost(identity, payload):
            actual(identity, payload)
            raise StorageError('acknowledgement lost')
        with mock.patch.object(self.a.remote, 'sync_commit', side_effect=lost):
            self.assertEqual(self.a.sync()['pending'], 1)
        doc = self.a.get(self.identity)
        doc.notes['notes'].append({'id': 'n-newer', 'text': 'Made during uncertainty', 'anchor': {'unit': 'p2'}})
        self.a.save(doc)
        self.a = self.device('a')
        self.a.sync()
        self.sync(self.a)
        self.sync(self.b)
        result = self.b.get(self.identity)
        self.assertEqual({n['id'] for n in result.notes['notes']}, {'n-one', 'n-newer'})

    def test_delete_versus_edit_keeps_deleted_state_and_recoverable_revision(self):
        self.seed()
        self.a.trash(self.identity)
        changed = self.b.get(self.identity)
        changed.notes['notes'][0]['text'] = 'Edited offline'
        self.b.save(changed)
        self.sync(self.a)
        self.sync(self.b)
        with self.assertRaises(FileNotFoundError):
            self.b.get(self.identity)
        conflicts = [c for c in self.b.conflicts() if c['document'] == self.identity]
        self.assertTrue(any(c['key'] == 'document-state' for c in conflicts))
        conflict = next(c for c in conflicts if c['key'] == 'document-state')
        self.b.resolve(self.identity, conflict['id'], 'restore')
        self.sync(self.b)
        self.assertEqual(self.b.get(self.identity).notes['notes'][0]['text'], 'Edited offline')

    def check_delete_edit_orders(self, action):
        for edit_first in (True, False):
            with self.subTest(action=action, edit_first=edit_first):
                self.identity = 'md-' + uuid.uuid4().hex
                self.doc.id = self.identity
                self.doc.meta['id'] = self.identity
                self.doc.revision = None
                self.seed()
                getattr(self.a, action)(self.identity)
                changed = self.b.get(self.identity)
                changed.notes['notes'][0]['text'] = 'New research from second device'
                self.b.save(changed)
                first, second = (self.b, self.a) if edit_first else (self.a, self.b)
                self.sync(first); self.sync(second); self.sync(first)
                for device in (self.a, self.b):
                    with self.assertRaises(FileNotFoundError):
                        device.get(self.identity)
                    conflicts = [c for c in device.conflicts() if c['document'] == self.identity and c['key'] == 'document-state']
                    self.assertEqual(len(conflicts), 1)
                self.b.resolve(self.identity, conflicts[0]['id'], 'restore')
                self.sync(self.b); self.sync(self.a)
                for device in (self.a, self.b):
                    restored = device.get(self.identity)
                    self.assertEqual(restored.notes['notes'][0]['text'], 'New research from second device')
                    self.assertEqual(restored.source.pdf, b'%PDF-original')

    def test_trash_and_edit_are_recoverable_in_both_sync_orders(self):
        self.check_delete_edit_orders('trash')

    def test_delete_and_edit_are_recoverable_in_both_sync_orders(self):
        self.check_delete_edit_orders('delete')

    def test_cache_clear_protects_unsent_data_and_separates_library_identity(self):
        self.seed()
        doc = self.a.get(self.identity)
        doc.images['pending.bin'] = b'not uploaded yet'
        self.a.save(doc)
        self.a.clear_cache()
        self.assertEqual(self.a.get(self.identity).images['pending.bin'], b'not uploaded yet')
        with mock.patch.object(self.a.remote, 'sync_health', return_value={'library_id': 'another-library'}):
            self.assertIn('identity changed', self.a.sync()['error'])
        self.assertEqual(self.a.status()['pending'], 1)
        self.sync(self.a)
        self.a.clear_cache()
        with mock.patch.object(self.a.remote, 'sync_health', side_effect=StorageError('offline')):
            with self.assertRaises(StorageError):
                self.a.get(self.identity)

    def test_id_histories_from_two_offline_devices_keep_both_sides(self):
        self.doc.extensions['ids.json'] = json.dumps({'format':'textfold/ids','version':1,'units':{'old':{'section':'s1'}},'sections':{}}).encode()
        self.seed()
        for device, identity in ((self.a,'phone'), (self.b,'laptop')):
            doc = device.get(self.identity)
            history = json.loads(doc.extensions['ids.json']); history['units'][identity] = {'section':identity}
            doc.extensions['ids.json'] = json.dumps(history).encode(); device.save(doc)
        self.sync(self.a); self.sync(self.b); self.sync(self.a)
        for device in (self.a,self.b):
            self.assertEqual(set(json.loads(device.get(self.identity).extensions['ids.json'])['units']), {'old','phone','laptop'})
            self.assertEqual(device.status()['conflicts'], 0)


    def test_first_id_histories_from_two_devices_merge_without_opaque_conflict(self):
        self.seed()
        for device, identity in ((self.a,'phone'), (self.b,'laptop')):
            doc = device.get(self.identity)
            history = json.loads(doc.extensions.get('ids.json', b'{"format":"textfold/ids","version":1,"units":{},"sections":{}}')); history['units'][identity] = {'section':identity}
            doc.extensions['ids.json'] = json.dumps(history).encode(); device.save(doc)
        self.sync(self.a); self.sync(self.b); self.sync(self.a)
        for device in (self.a,self.b):
            self.assertEqual(set(json.loads(device.get(self.identity).extensions['ids.json'])['units']), {'phone','laptop'})
            self.assertEqual(device.status()['conflicts'], 0)
    def test_conversation_forks_survive_two_device_merge(self):
        self.doc.notes['notes'][0].update(kind='ask', thread=[{'q': 'First?', 'a': 'Answer'}])
        self.seed()
        for device, identity in ((self.a, 'phone-message'), (self.b, 'laptop-message')):
            doc = device.get(self.identity)
            note = doc.notes['notes'][0]
            parent = records.branches(note)[0]
            note['messages'].append({'id': identity, 'parent': parent, 'role': 'user', 'text': identity})
            device.save(doc)
        self.sync(self.a)
        self.sync(self.b)
        self.sync(self.a)
        note = self.a.get(self.identity).notes['notes'][0]
        self.assertEqual(set(records.branches(note)), {'phone-message', 'laptop-message'})

    def test_later_conversation_branch_survives_old_delete_conflict_and_cloud_restart(self):
        self.doc.notes['notes'][0].update(kind='ask', thread=[{'q': 'First?', 'a': 'First answer'}])
        self.seed()
        deleted = self.a.get(self.identity)
        deleted.notes['notes'] = []
        self.a.save(deleted)
        changed = self.b.get(self.identity)
        note = changed.notes['notes'][0]
        note['text'] = 'Remote edit'
        note['messages'].append({'id': 'q2', 'parent': records.branches(note)[0], 'role': 'user', 'text': 'Second?'})
        self.b.save(changed)
        self.sync(self.a); self.sync(self.b); self.sync(self.a)
        conflicts = [c for c in self.b.conflicts() if c['document'] == self.identity]
        ordinary = next(c for c in conflicts if not c['fields'])
        message = next(c for c in conflicts if c['fields'] == ['messages'])
        self.b.resolve(self.identity, ordinary['id'], next(i for i, v in enumerate(ordinary['variants']) if v is not None))
        changed = self.b.get(self.identity)
        changed.notes['notes'][0]['text'] = 'Later saved research'
        changed.notes['notes'][0]['messages'].append({'id': 'q3', 'parent': 'q2', 'role': 'user', 'text': 'Later branch'})
        self.b.save(changed)
        self.b.resolve(self.identity, message['id'], next(i for i, v in enumerate(message['variants']) if v is None))
        self.sync(self.b); self.sync(self.a)
        self.a = self.device('a')
        retained = next(c for c in self.a.conflicts() if c['document'] == self.identity)
        self.a.resolve(self.identity, retained['id'], next(i for i, v in enumerate(retained['variants']) if v is not None))
        self.sync(self.a); self.sync(self.b)
        for device in (self.a, self.b):
            restored = device.get(self.identity)
            note = restored.notes['notes'][0]
            self.assertEqual(note['text'], 'Later saved research')
            self.assertEqual([t['q'] for t in records.conversation(note, 'q3')], ['First?', 'Second?', 'Later branch'])
            self.assertEqual(restored.source.pdf, b'%PDF-original')

    def test_cursor_advances_only_after_applied_page_and_export_contains_no_credentials(self):
        self.seed()
        with self.b.cache.transaction() as db:
            before = db.execute("SELECT value FROM sync_state WHERE key='cursor'").fetchone()[0]
        changed = self.a.get(self.identity)
        changed.notes['notes'][0]['text'] = 'Change to pull'
        self.a.save(changed); self.sync(self.a)
        with mock.patch.object(self.b, '_snapshot', side_effect=StorageError('interrupted page')):
            self.assertIn('interrupted', self.b.sync()['error'])
        with self.b.cache.transaction() as db:
            self.assertEqual(db.execute("SELECT value FROM sync_state WHERE key='cursor'").fetchone()[0], before)
        self.b = self.device('b'); self.sync(self.b)
        changed = self.b.get(self.identity)
        self.assertEqual(changed.notes['notes'][0]['text'], 'Change to pull')
        changed.notes['notes'][0]['text'] = 'Unsent edit'; self.b.save(changed)
        import io, json, zipfile
        with zipfile.ZipFile(io.BytesIO(self.b.export_pending())) as archive:
            exported = archive.read('pending.json').decode()
            self.assertIn('Unsent edit', exported)
            self.assertNotIn(os.environ['TEXTFOLD_TEST_STORAGE_PASSWORD'], exported)
            self.assertNotIn('Authorization', exported)

    def test_repeated_local_import_preserves_source_and_new_cloud_notes(self):
        from textfold.storage import LocalDirectoryBackend
        source = LocalDirectoryBackend(self.root / 'legacy')
        source.save(copy.deepcopy(self.doc))
        before = {p.relative_to(source.root): p.read_bytes() for p in source.root.rglob('*') if p.is_file()}
        self.assertEqual(self.a.cache.import_library(source)['imported'], 1)
        self.sync(self.a); self.sync(self.b)
        changed = self.b.get(self.identity)
        changed.notes['notes'].append({'id': 'n-cloud', 'text': 'From second device', 'anchor': {'unit': 'p1'}})
        self.b.save(changed); self.sync(self.b); self.sync(self.a)
        self.a.cache.import_library(source, force=True); self.sync(self.a)
        self.assertIn('n-cloud', {n['id'] for n in self.a.get(self.identity).notes['notes']})
        self.assertEqual(before, {p.relative_to(source.root): p.read_bytes() for p in source.root.rglob('*') if p.is_file()})


if __name__ == '__main__':
    unittest.main()
