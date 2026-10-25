import { migrate } from './schema.mjs';
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';

test('0005 moves only purged index rows and preserves active/trash data and operation receipts', async () => {
  const mf = new Miniflare(convertV4MiniflareOptions({ modules: true, scriptPath: '.wrangler/build/index.js',
    compatibilityDate: '2026-10-23', compatibilityFlags: ['nodejs_compat'], d1Databases: ['DB'], r2Buckets: ['BLOBS'],
    bindings: { STORAGE_USERNAME: 'fixture', STORAGE_PASSWORD: 'fixture-only' } }));
  try {
    const db = await mf.getD1Database('DB');
    await migrate(db, { through: 4 });
    for (const state of ['active', 'trash', 'deleted']) {
      const id = 'md-old-' + state, revision = randomUUID(), operation = randomUUID();
      await db.prepare('INSERT INTO sync_documents VALUES (?,?,?,?,?,?,?,?,?)').bind(id, revision, 'retained-' + id, '{"meta":{"title":"Old"}}', state, '{"of":"keep"}', 1, 2, state === 'deleted' ? 1 : 0).run();
      await db.prepare('INSERT INTO sync_operations VALUES (?,?,?,?,?)').bind(operation, 'old-hash', id, revision, JSON.stringify({ revision, state, purged: state === 'deleted' })).run();
      await db.prepare('INSERT INTO change_log(document_id,revision,state,operation_id) VALUES (?,?,?,?)').bind(id, revision, state, operation).run();
      if (state !== 'deleted') await db.prepare('INSERT INTO document_records VALUES (?,?,?,?,?,?)').bind(id, 'source/html', '"Original source"', null, 0, revision).run();
    }
    const before = await db.prepare("SELECT * FROM sync_documents WHERE state!='deleted' ORDER BY id").all();
    const records = await db.prepare('SELECT * FROM document_records ORDER BY document_id').all();
    const receipts = await db.prepare('SELECT * FROM sync_operations ORDER BY document_id').all();
    const bucket = await mf.getR2Bucket('BLOBS');
    await bucket.put('retained-fixture', 'Original bytes');
    await migrate(db, { after: 4 });
    assert.deepEqual((await db.prepare('SELECT * FROM sync_documents ORDER BY id').all()).results, before.results);
    assert.deepEqual((await db.prepare('SELECT * FROM document_records ORDER BY document_id').all()).results, records.results);
    assert.deepEqual((await db.prepare('SELECT * FROM sync_operations ORDER BY document_id').all()).results, receipts.results);
    assert.equal((await db.prepare('SELECT id FROM document_deletions').first()).id, 'md-old-deleted');
    assert.equal(await (await bucket.get('retained-fixture')).text(), 'Original bytes');
  } finally { await mf.dispose(); }
});
