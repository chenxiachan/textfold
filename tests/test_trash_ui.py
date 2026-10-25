"""Trash browsing and permanent deletion use disposable libraries only."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from textfold.storage import LocalDirectoryBackend
from textfold.documents import ConflictError, Document, Source


class TrashStorage(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.backend = LocalDirectoryBackend(self.root/'library')
        self.doc = Document('md-trash-fixture', meta={'title':'Keep <notes>'},
            source=Source(html='Original', pdf=b'%PDF-original'), layers={'en':{'version':'one'}},
            notes={'notes':[{'id':'n-one','text':'Private note'}]})
        self.backend.save(self.doc)

    def test_list_reads_metadata_only_and_restore_preserves_everything(self):
        self.backend.trash(self.doc.id, {'of':'f-original'})
        read = Path.read_bytes
        def safe(path):
            if path.name == 'source.pdf': self.fail('trash listing loaded a PDF')
            return read(path)
        with mock.patch.object(Path,'read_bytes',safe):
            rows = self.backend.list_trash()
        self.assertEqual(len(rows),1)
        self.assertEqual((rows[0]['id'],rows[0]['title'],rows[0]['restorable']), (self.doc.id,'Keep <notes>',True))
        self.assertEqual(self.backend.restore(self.doc.id), {'of':'f-original'})
        restored = self.backend.get(self.doc.id)
        self.assertEqual(restored.source,self.doc.source)
        self.assertEqual(restored.layers,self.doc.layers)
        self.assertEqual(restored.notes,self.doc.notes)
        self.assertEqual(self.backend.list_trash(),[])

    def test_permanent_delete_erases_only_all_retained_copies_of_that_document(self):
        first = Path(self.backend.trash(self.doc.id))
        first.rename(first.with_name(self.doc.id+'-20261001-010101'))
        self.doc.revision = None
        self.doc._legacy = {}
        self.backend.save(self.doc)
        self.backend.trash(self.doc.id)
        other = Document('md-other',meta={'title':'Other'},source=Source(html='Other original'))
        self.backend.save(other)
        other_trash = Path(self.backend.trash(other.id))
        self.doc.revision = None
        self.doc._legacy = {}
        self.backend.save(self.doc)
        row = next(r for r in self.backend.list_trash() if r['id']==self.doc.id)
        self.assertFalse(row['restorable'])
        self.assertTrue(self.backend.delete_trashed(self.doc.id))
        self.assertFalse(self.backend.delete_trashed(self.doc.id))
        self.assertEqual(self.backend.get(self.doc.id).source,self.doc.source)
        self.assertTrue(other_trash.exists())
        self.assertFalse(any((self.backend.root/'papers/.trash').glob(self.doc.id+'-*')))

    def test_invalid_identifiers_cannot_delete_active_or_unrelated_files(self):
        outside = self.root/'outside'; outside.write_text('Keep',encoding='utf-8')
        for identity in ('../outside','',self.doc.id+'/../outside','@library'):
            with self.subTest(identity=identity),self.assertRaises(ValueError): self.backend.delete_trashed(identity)
        self.assertFalse(self.backend.delete_trashed(self.doc.id))
        self.assertEqual(outside.read_text(encoding='utf-8'),'Keep')
        self.assertEqual(self.backend.get(self.doc.id).source,self.doc.source)
        malformed = self.backend.root/'papers/.trash'/f'{self.doc.id}-20269999-999999'
        malformed.mkdir(parents=True)
        self.assertFalse(self.backend.delete_trashed(self.doc.id))
        self.assertTrue(malformed.exists())

    def test_stale_confirmation_preserves_changed_trash(self):
        folder = Path(self.backend.trash(self.doc.id))
        token = self.backend.list_trash()[0]['revision']
        (folder/'notes.json').write_text('{"notes":[{"text":"New work"}]}', encoding='utf-8')
        for action in (self.backend.restore, self.backend.delete_trashed):
            with self.assertRaises(ConflictError):
                action(self.doc.id, token)
        self.assertTrue(folder.exists())
        current = self.backend.list_trash()[0]['revision']
        self.assertEqual(self.backend.restore(self.doc.id, current), {})
        self.assertEqual(self.backend.get(self.doc.id).notes['notes'][0]['text'], 'New work')

    def test_deleting_a_linked_entry_keeps_external_original(self):
        external = self.root/'external'; external.mkdir()
        (external/'meta.json').write_text(json.dumps({'title':'External'}),encoding='utf-8')
        (external/'source.html').write_text('Keep external',encoding='utf-8')
        trash = self.backend.root/'papers/.trash'; trash.mkdir()
        link = trash/'md-external-20261001-010101'
        try: link.symlink_to(external,target_is_directory=True)
        except OSError as error:
            if os.name != 'nt': self.skipTest(str(error))
            made = subprocess.run(['cmd','/c','mklink','/J',str(link),str(external)],capture_output=True)
            if made.returncode: self.skipTest('Directory links unavailable')
        self.assertEqual(self.backend.list_trash()[0]['title'],'External')
        self.assertTrue(self.backend.delete_trashed('md-external'))
        self.assertFalse(link.is_symlink())
        self.assertEqual((external/'source.html').read_text(encoding='utf-8'),'Keep external')

    def test_redirected_trash_root_never_deletes_an_external_directory(self):
        external = self.root/'external-trash'; external.mkdir()
        entry = external/f'{self.doc.id}-20261001-010101'; entry.mkdir()
        (entry/'meta.json').write_text('{"title":"Keep"}', encoding='utf-8')
        link = self.backend.root/'papers/.trash'
        try: link.symlink_to(external, target_is_directory=True)
        except OSError as error:
            if os.name != 'nt': self.skipTest(str(error))
            made = subprocess.run(['cmd','/c','mklink','/J',str(link),str(external)],capture_output=True)
            if made.returncode: self.skipTest('Directory links unavailable')
        with self.assertRaises(ValueError): self.backend.delete_trashed(self.doc.id)
        self.assertEqual(self.backend.purge(float('inf')), [])
        self.assertTrue((entry/'meta.json').exists())


class TrashRoutes(unittest.TestCase):
    from test_storage_routes import StorageRoutes as _routes
    setUp, start, stop, call = _routes.setUp, _routes.start, _routes.stop, _routes.call

    def test_browse_restore_and_permanent_delete_through_http(self):
        _, imported = self.call('/api/import',{'name':'trash.md','text':'# Retained title\n\nOriginal source.'})
        pid = imported['paper']
        source = self.data/'papers'/pid/'source.md'
        original = source.read_bytes()
        self.assertEqual(self.call('/api/trash')[1],[])
        self.assertEqual(self.call('/api/papers/delete',{'papers':[pid]})[1]['deleted'],[pid])
        status, rows = self.call('/api/trash')
        self.assertEqual(status,200)
        self.assertEqual(rows[0]['id'],pid)
        self.assertEqual(self.call('/api/trash/delete',{'paper':pid,'revision':'stale'})[0],409)
        self.assertEqual(self.call('/api/papers/restore',{'papers':[pid],'revisions':{pid:'stale'}})[0],409)
        self.assertEqual(self.call('/api/trash')[1][0]['id'],pid)
        self.assertEqual(self.call('/api/papers/restore',{'papers':[pid]})[1]['restored'],[pid])
        self.assertEqual(source.read_bytes(),original)
        self.call('/api/papers/delete',{'papers':[pid]})
        self.assertEqual(self.call('/api/trash/delete',{'paper':pid}), (200,{'deleted':pid}))
        self.assertEqual(self.call('/api/trash')[1],[])
        self.assertEqual(self.call('/api/papers/restore',{'papers':[pid]})[1]['restored'],[])
        self.assertEqual(self.call('/api/trash/delete',{'paper':'../outside'})[0],400)
        self.assertEqual(self.call('/api/trash/delete',{'paper':pid})[0],404)
