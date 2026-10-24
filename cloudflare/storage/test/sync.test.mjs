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
});
after(async () => { await mf?.dispose(); });
async function call(path, method = 'GET', body) {
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

test('legacy bootstrap retains unchanged records and fences old writers after v2 activation', async () => {
  const id = 'md-legacy', document = { format: 'textfold/document-v1', id, meta: { id, title: 'Legacy' },
    source: { html: 'Legacy source', markdown: '# Legacy', xml: null, wiki_html: null, pdf: null, reference: null, originals: {} },
    processing: { layout: null, structured: null, replies: {} }, layers: { en: { keep: true } }, images: {}, extensions: {}, legacy: {}, notes: null, previous: null, created: 0, updated: 0 };
  const old = await ok('v1/documents/' + id, 'PUT', { document, revision: null, commit: randomUUID() });
  const migrated = await ok('v2/documents/' + id, 'PUT', payload([change('meta', { ...document.meta, title: 'Renamed' })], old.revision));
  const stored = await ok('v2/documents/' + id);
  assert.equal(stored.records['source/markdown'], '# Legacy');
  assert.deepEqual(stored.records['layers/en'], { keep: true });
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
