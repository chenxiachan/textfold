import { migrate } from './schema.mjs';
import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';

let mf, db;
const authorization = 'Basic ' + Buffer.from('reader:fixture-only').toString('base64');
before(async () => {
  mf = new Miniflare(convertV4MiniflareOptions({ modules: true, scriptPath: '.wrangler/build/index.js',
    compatibilityDate: '2026-10-23', compatibilityFlags: ['nodejs_compat'], d1Databases: ['DB'], r2Buckets: ['BLOBS'],
    bindings: { STORAGE_USERNAME: 'reader', STORAGE_PASSWORD: 'fixture-only' } }));
  db = await mf.getD1Database('DB');
  await migrate(db);
  // Seed legacy trash before the first v2 write activates the legacy fence.
  for (const id of ['md-old-trash-restore', 'md-old-trash-purge', 'md-old-trash-active']) {
    await ok('v1/documents/' + id, 'PUT', { document: legacyDocument(id), revision: null, commit: randomUUID() });
    await ok('v1/documents/' + id + '/trash', 'POST', { commit: randomUUID(), placement: { of: 'legacy-folder' } });
    if (id.endsWith('active')) await ok('v1/documents/' + id, 'PUT', { document: legacyDocument(id), revision: null, commit: randomUUID() });
  }
});
after(async () => { await mf?.dispose(); });
async function call(path, method = 'GET', body) {
  if (body?.purge === true && method === 'PUT') { path += '/purge'; method = 'POST'; }
  return mf.dispatchFetch('http://storage.test/' + path, { method,
    headers: { Authorization: authorization, 'Content-Type': 'application/json' },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
}
async function ok(path, method, body) {
  const response = await call(path, method, body);
  assert.equal(response.status, 200, await response.clone().text());
  return response.json();
}
const change = (key, value) => ({ key, value, deleted: false });
const payload = (changes, revision = null) => ({ operation_id: randomUUID(), revision, changes, state: 'active', placement: {} });
const initial = id => [change('meta', { id, title: 'Immutable original' }), change('source/html', '<p>Source</p>')];
const legacyDocument = id => ({ format: 'textfold/document-v1', id, meta: { id, title: 'Retained legacy document' },
  source: { html: 'Original', markdown: '# Original', xml: null, wiki_html: null, pdf: null, reference: null, originals: {} },
  processing: { layout: null, structured: null, replies: {} }, layers: { en: { keep: 'paid result' } },
  images: {}, extensions: {}, legacy: {}, notes: { notes: [{ id: 'n-old', text: 'Old note', anchor: {} }] }, previous: null, created: 1, updated: 1 });

test('legacy bootstrap retains unchanged records and fences old writers after v2 activation', async () => {
  const id = 'md-legacy', document = { format: 'textfold/document-v1', id, meta: { id, title: 'Legacy' },
    source: { html: 'Legacy source', markdown: '# Legacy', xml: null, wiki_html: null, pdf: null, reference: null, originals: {} },
    processing: { layout: null, structured: null, replies: {} }, layers: { en: { keep: true } }, images: {}, extensions: {}, legacy: {}, notes: null, previous: null, created: 0, updated: 0 };
  document.notes = { notes: [{ id: 'n-legacy', anchor: {}, thread: [{ q: 'First', a: 'Old reply' }, { q: 'Added by old pip', a: 'Paid reply' }],
    messages: [{ id: 'legacy-q', parent: null, role: 'user', text: 'First' }, { id: 'legacy-a', parent: 'legacy-q', role: 'assistant', text: 'Old reply' }] }] };
  const old = await ok('v1/documents/' + id, 'PUT', { document, revision: null, commit: randomUUID() });
  const migrated = await ok('v2/documents/' + id, 'PUT', payload([change('meta', { ...document.meta, title: 'Renamed' })], old.revision));
  const stored = await ok('v2/documents/' + id);
  assert.equal(stored.records['source/markdown'], '# Legacy');
  assert.deepEqual(stored.records['layers/en'], { keep: true });
  assert.equal(Object.keys(stored.records).filter(k => k.startsWith('message/n-legacy/')).length, 4);
  assert.ok(Object.values(stored.records).some(v => v && typeof v === 'object' && v.text === 'Paid reply'));
  assert.equal((await call('v1/documents/' + id, 'PUT', { document, revision: migrated.revision, commit: randomUUID() })).status, 409);
  // Simulate an old Worker request that passed its HTTP check before activation.
  // The database itself must fence a late legacy publication on another ID.
  await assert.rejects(db.prepare("INSERT INTO documents VALUES (?,?,?,?,?,?)").bind('md-late-legacy', randomUUID(), 'orphan-manifest', '{}', 1, 1).run(), /textfold_v2_write_required/);
  assert.equal(await db.prepare("SELECT id FROM documents WHERE id='md-late-legacy'").first(), null);
});

test('operation replay is idempotent and scoped to a document', async () => {
  const body = payload(initial('md-replay'));
  const first = await ok('v2/documents/md-replay', 'PUT', body);
  assert.deepEqual(await ok('v2/documents/md-replay', 'PUT', body), first);
  assert.equal((await call('v2/documents/md-other', 'PUT', body)).status, 409);
  assert.equal((await db.prepare('SELECT COUNT(*) AS n FROM change_log WHERE document_id=?').bind('md-replay').first()).n, 1);
});

test('concurrent sparse commits have a single winner and failed CAS publishes no partial records', async () => {
  const id = 'md-cas', first = await ok('v2/documents/' + id, 'PUT', payload(initial(id)));
  const responses = await Promise.all(['a', 'b'].map(key => call('v2/documents/' + id, 'PUT', payload([change('layers/' + key, { marker: key })], first.revision))));
  assert.deepEqual(responses.map(r => r.status).sort(), [200, 409]);
  const state = await ok('v2/documents/' + id);
  assert.equal(Object.keys(state.records).filter(k => k.startsWith('layers/')).length, 1);
});

test('original bytes, completed history and every branch ancestry are protected', async () => {
  const id = 'md-tree', annotation = { id: 'n-chat', kind: 'ask', anchor: { on: 'paper' }, text: '' };
  const q = { id: 'q', parent: null, role: 'user', text: 'Question' };
  const a = { id: 'a', parent: 'q', role: 'assistant', text: 'Answer' };
  const first = await ok('v2/documents/' + id, 'PUT', payload([...initial(id), change('annotation/n-chat', annotation), change('message/n-chat/q', q), change('message/n-chat/a', a)]));
  for (const [changes, status] of [
    [[change('source/html', 'Replacement')], 409],
    [[change('message/n-chat/a', { ...a, text: 'Overwritten' })], 409],
    [[change('message/n-chat/z', { id: 'z', parent: 'missing', role: 'user', text: 'Orphan' })], 400],
    [[change('extensions/__proto__', { poisoned: true })], 400]
  ]) assert.equal((await call('v2/documents/' + id, 'PUT', payload(changes, first.revision))).status, status);
  assert.equal((await ok('v2/documents/' + id)).revision, first.revision);
});

test('tombstones and monotonic changes survive deletion and stale device retries', async () => {
  const id = 'md-deleted', first = await ok('v2/documents/' + id, 'PUT', payload(initial(id)));
  await ok('v2/documents/' + id, 'PUT', { ...payload([], first.revision), state: 'deleted' });
  assert.equal((await ok('v2/documents/' + id)).state, 'deleted');
  assert.equal((await call('v2/documents/' + id, 'PUT', payload(initial(id)))).status, 409);
  const feed = await ok('v2/changes?cursor=0');
  assert.ok(feed.changes.some(c => c.document_id === id && c.state === 'deleted'));
  assert.ok(feed.changes.every((c, i, a) => !i || c.sequence > a[i - 1].sequence));
});

test('permanent deletion is idempotent, terminal and cannot erase an active or restored revision', async () => {
  const id = 'md-permanent', active = await ok('v2/documents/' + id, 'PUT', payload(initial(id)));
  const destructive = revision => ({ ...payload([], revision), state: 'deleted', purge: true });
  assert.equal((await call('v2/documents/' + id, 'PUT', destructive(active.revision))).status, 409);
  const trash = await ok('v2/documents/' + id, 'PUT', { ...payload([], active.revision), state: 'trash' });
  const restore = await ok('v2/documents/' + id, 'PUT', payload([], trash.revision));
  assert.equal((await call('v2/documents/' + id, 'PUT', destructive(trash.revision))).status, 409);
  assert.equal((await ok('v2/documents/' + id)).state, 'active');
  const again = await ok('v2/documents/' + id, 'PUT', { ...payload([], restore.revision), state: 'trash' });
  const purge = destructive(again.revision), result = await ok('v2/documents/' + id, 'PUT', purge);
  assert.equal(result.purged, true);
  assert.deepEqual(await ok('v2/documents/' + id, 'PUT', purge), result);
  const state = await ok('v2/documents/' + id);
  assert.deepEqual(state.records, {}); assert.equal(state.purged, true);
  assert.equal(state.operation_id, purge.operation_id);
  assert.equal((await db.prepare('SELECT COUNT(*) AS n FROM document_records WHERE document_id=?').bind(id).first()).n, 0);
  // Even an old client that knows the newest revision cannot resurrect it.
  assert.equal((await call('v2/documents/' + id, 'PUT', payload(initial(id), result.revision))).status, 409);
  assert.equal((await call('v2/documents/' + id, 'PUT', payload(initial(id)))).status, 409);
  assert.equal((await ok('v2/index')).documents.some(d => d.id === id), false);
});

test('legacy trash is indexed, restored losslessly and purged after the v2 fence', async () => {
  const id = 'md-old-trash-restore', original = await ok('v2/documents/' + id);
  assert.equal(original.state, 'trash');
  assert.deepEqual(original.placement, { of: 'legacy-folder' });
  assert.equal((await ok('v2/index')).documents.filter(d => d.id === id).length, 1);
  await ok('v2/documents/' + id, 'PUT', { ...payload([], original.revision), placement: original.placement });
  const restored = await ok('v2/documents/' + id);
  assert.deepEqual(restored.records, original.records);
  assert.equal(restored.records['source/markdown'], '# Original');
  assert.equal(restored.records['layers/en'].keep, 'paid result');
  assert.equal(await db.prepare('SELECT token FROM trash WHERE id=?').bind(id).first(), null);
  const purgeId = 'md-old-trash-purge', trash = await ok('v2/documents/' + purgeId);
  await ok('v2/documents/' + purgeId, 'PUT', { ...payload([], trash.revision), state: 'deleted', purge: true });
  assert.equal((await ok('v2/documents/' + purgeId)).purged, true);
  assert.equal(await db.prepare('SELECT token FROM trash WHERE id=?').bind(purgeId).first(), null);
  const activeId = 'md-old-trash-active', active = await ok('v2/documents/' + activeId);
  assert.equal(active.state, 'active');
  assert.equal((await call('v2/documents/' + activeId, 'PUT', { ...payload([], active.revision), state: 'deleted', purge: true })).status, 409);
  assert.deepEqual((await ok('v2/documents/' + activeId)).records, active.records);
});

test('a failed purge transaction retains records and emits no deletion event', async () => {
  const id = 'md-purge-rollback', active = await ok('v2/documents/' + id, 'PUT', payload(initial(id)));
  const trash = await ok('v2/documents/' + id, 'PUT', { ...payload([], active.revision), state: 'trash' });
  await db.prepare("CREATE TRIGGER fail_purge_fixture BEFORE DELETE ON document_records WHEN OLD.document_id='md-purge-rollback' BEGIN SELECT RAISE(ABORT,'fixture failure'); END").run();
  try {
    const result = await call('v2/documents/' + id, 'PUT', { ...payload([], trash.revision), state: 'deleted', purge: true });
    assert.equal(result.status, 503);
    const snapshot = await ok('v2/documents/' + id);
    assert.equal(snapshot.revision, trash.revision); assert.equal(snapshot.purged, false);
    assert.equal(snapshot.records['source/html'], '<p>Source</p>');
    assert.equal((await db.prepare('SELECT COUNT(*) AS n FROM change_log WHERE document_id=?').bind(id).first()).n, 2);
  } finally { await db.prepare('DROP TRIGGER fail_purge_fixture').run(); }
});

test('restore racing permanent deletion has one winner with no partial publication', async () => {
  const id = 'md-purge-race', active = await ok('v2/documents/' + id, 'PUT', payload(initial(id)));
  const trash = await ok('v2/documents/' + id, 'PUT', { ...payload([], active.revision), state: 'trash' });
  const results = await Promise.all([
    call('v2/documents/' + id, 'PUT', payload([], trash.revision)),
    call('v2/documents/' + id, 'PUT', { ...payload([], trash.revision), state: 'deleted', purge: true })
  ]);
  assert.deepEqual(results.map(r => r.status).sort(), [200, 409]);
  const state = await ok('v2/documents/' + id);
  if (state.purged) { assert.equal(state.state, 'deleted'); assert.deepEqual(state.records, {}); }
  else { assert.equal(state.state, 'active'); assert.equal(state.records['source/html'], '<p>Source</p>'); }
  assert.equal((await db.prepare('SELECT COUNT(*) AS n FROM change_log WHERE document_id=?').bind(id).first()).n, 3);
});

test('concurrent retries of the same purge return the same acknowledgement', async () => {
  const id = 'md-purge-replayed-concurrently', active = await ok('v2/documents/' + id, 'PUT', payload(initial(id)));
  const trash = await ok('v2/documents/' + id, 'PUT', { ...payload([], active.revision), state: 'trash' });
  const body = { ...payload([], trash.revision), state: 'deleted', purge: true };
  const results = await Promise.all([ok('v2/documents/' + id, 'PUT', body), ok('v2/documents/' + id, 'PUT', body)]);
  assert.deepEqual(results[0], results[1]);
  assert.equal(results[0].purged, true);
  assert.equal((await db.prepare('SELECT COUNT(*) AS n FROM change_log WHERE document_id=?').bind(id).first()).n, 3);
});

test('purging one document keeps content-addressed attachments used by another', async () => {
  const bytes = new TextEncoder().encode('%PDF-shared-fixture');
  const digest = Buffer.from(await crypto.subtle.digest('SHA-256', bytes)).toString('hex');
  const uploaded = await mf.dispatchFetch('http://storage.test/v1/blobs/' + digest, { method: 'PUT', headers: { Authorization: authorization }, body: bytes });
  assert.equal(uploaded.status, 200);
  const ref = { sha256: digest, size: bytes.length };
  const a = await ok('v2/documents/md-purge-shared', 'PUT', payload([...initial('md-purge-shared'), change('source/pdf', ref)]));
  await ok('v2/documents/md-keep-shared', 'PUT', payload([...initial('md-keep-shared'), change('source/pdf', ref)]));
  const trash = await ok('v2/documents/md-purge-shared', 'PUT', { ...payload([], a.revision), state: 'trash' });
  await ok('v2/documents/md-purge-shared', 'PUT', { ...payload([], trash.revision), state: 'deleted', purge: true });
  assert.deepEqual((await ok('v2/documents/md-keep-shared')).records['source/pdf'], ref);
  const blob = await call('v1/blobs/' + digest);
  assert.equal(blob.status, 200); assert.deepEqual(new Uint8Array(await blob.arrayBuffer()), bytes);
});

test('explicit reimport reuses the ID and fences edits based on deleted revisions', async () => {
  const id = 'md-reimport';
  const active = await ok('v2/documents/' + id, 'PUT', payload(initial(id)));
  const trash = await ok('v2/documents/' + id, 'PUT', { ...payload([], active.revision), state: 'trash' });
  const purged = await ok('v2/documents/' + id, 'PUT', { ...payload([], trash.revision), state: 'deleted', purge: true });
  assert.equal((await ok('v2/index')).documents.some(d => d.id === id), false);
  const request = { ...payload(initial(id), purged.revision), reimport_from: purged.revision, base_revision: null };
  const fresh = await ok('v2/documents/' + id, 'PUT', request);
  assert.equal(fresh.state, 'active'); assert.equal(fresh.purged, false);
  assert.deepEqual(await ok('v2/documents/' + id, 'PUT', request), fresh);
  // A stale client may fetch the newest revision and try rebasing old notes.
  assert.equal((await call('v2/documents/' + id, 'PUT', payload(initial(id), fresh.revision))).status, 409);
  assert.equal((await call('v2/documents/' + id, 'PUT', { ...payload([], fresh.revision), base_revision: active.revision })).status, 409);
  const saved = await ok('v2/documents/' + id, 'PUT', { ...payload([], fresh.revision), base_revision: fresh.revision });
  assert.equal((await ok('v2/documents/' + id)).revision, saved.revision);
  assert.equal((await ok('v2/documents/' + id + '?base_revision=' + active.revision)).base_deleted, true);
});
