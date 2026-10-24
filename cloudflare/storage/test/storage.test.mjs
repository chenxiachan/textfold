import { migrate } from './schema.mjs';
import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { createHash, randomUUID } from 'node:crypto';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';

const username = 'reader-读者', password = 'test-fixture-not-a-production-secret';
const authorization = 'Basic ' + Buffer.from(`${username}:${password}`).toString('base64');
let mf, db, bucket;
const digest = bytes => createHash('sha256').update(bytes).digest('hex');

before(async () => {
  mf = new Miniflare(convertV4MiniflareOptions({ modules: true, scriptPath: '.wrangler/build/index.js',
    compatibilityDate: '2026-10-23', compatibilityFlags: ['nodejs_compat'],
    d1Databases: ['DB'], r2Buckets: ['BLOBS'], bindings: { STORAGE_USERNAME: username, STORAGE_PASSWORD: password } }));
  db = await mf.getD1Database('DB');
  bucket = await mf.getR2Bucket('BLOBS');
  await migrate(db);
});
after(async () => { await mf?.dispose(); });

async function request(path, method = 'GET', body, headers = {}) {
  const init = { method, headers: { Authorization: authorization, ...headers } };
  if (body !== undefined) {
    if (body instanceof Uint8Array) { init.body = body; init.headers['Content-Length'] = String(body.byteLength); }
    else { init.body = JSON.stringify(body); init.headers['Content-Type'] = 'application/json'; }
  }
  return mf.dispatchFetch(`http://storage.test/v1/${path}`, init);
}
async function json(path, method = 'GET', body) {
  const result = await request(path, method, body);
  assert.equal(result.status, 200, await result.clone().text());
  return result.json();
}
function document(id = `md-${randomUUID()}`) {
  return { format: 'textfold/document-v1', id, meta: { id, title: '文献 📚' },
    source: { html: '<article>Research</article>', markdown: '# Research', xml: null, wiki_html: null,
      pdf: null, reference: null, originals: {} }, layers: {}, notes: { notes: [] }, images: {},
    processing: { layout: null, structured: null, replies: {} }, previous: null, extensions: {}, legacy: {}, created: 0, updated: 0 };
}
async function save(doc, revision = null, commit = randomUUID()) {
  return json(`documents/${doc.id}`, 'PUT', { document: doc, revision, commit });
}

test('missing/invalid authentication cannot read or mutate the library', async () => {
  assert.equal((await request('documents', 'GET', undefined, { Authorization: '' })).status, 401);
  assert.equal((await request('documents/nope', 'DELETE', undefined, { Authorization: 'Bearer wrong' })).status, 401);
});

test('whole-document save, lightweight index, immutable blob and stale CAS', async () => {
  const doc = document(), bytes = new TextEncoder().encode('%PDF-original-中文'), hash = digest(bytes);
  assert.equal((await request(`blobs/${hash}`, 'PUT', bytes)).status, 200);
  doc.source.pdf = { sha256: hash, size: bytes.length };
  doc.layers = { en: { version: 'one' }, 'tr-zh': { title: '中文题目', version: '二' } };
  doc.notes = { notes: [{ id: 'n1', text: 'Keep' }] };
  const first = await save(doc);
  const info = await json(`documents/${doc.id}/info`);
  assert.deepEqual(info.languages, ['en', 'zh']);
  assert.equal(info.note_count, 1);
  assert.equal(info.readable, true);
  assert.equal(info.titles.zh, '中文题目');
  assert.equal((await json(`documents/${doc.id}`)).document.meta.title, '文献 📚');
  assert.deepEqual(new Uint8Array(await (await request(`blobs/${hash}`)).arrayBuffer()), bytes);
  doc.meta.title = 'Edited';
  const updated = await save(doc, first.revision);
  assert.notEqual(updated.revision, first.revision);
  const rejected = await request(`documents/${doc.id}`, 'PUT', { document: doc, revision: first.revision, commit: randomUUID() });
  assert.equal(rejected.status, 409);
  assert.equal((await json(`documents/${doc.id}/info`)).revision, updated.revision);
});

test('missing blobs and failed publication never replace the old document', async () => {
  const doc = document(), first = await save(doc);
  doc.images.figure = { sha256: 'a'.repeat(64), size: 20 };
  const result = await request(`documents/${doc.id}`, 'PUT', { document: doc, revision: first.revision, commit: randomUUID() });
  assert.equal(result.status, 409);
  assert.equal((await json(`documents/${doc.id}/info`)).revision, first.revision);
});

test('concurrent saves have one winner and no mixed document state', async () => {
  const doc = document(), first = await save(doc);
  const a = structuredClone(doc), b = structuredClone(doc);
  a.meta.title = 'A'; a.notes = { notes: [{ text: 'A' }] };
  b.meta.title = 'B'; b.notes = { notes: [{ text: 'B' }] };
  const results = await Promise.all([a, b].map(document => request(`documents/${doc.id}`, 'PUT', {
    document, revision: first.revision, commit: randomUUID(),
  })));
  assert.deepEqual(results.map(r => r.status).sort(), [200, 409]);
  const saved = (await json(`documents/${doc.id}`)).document;
  assert.equal(saved.meta.title, saved.notes.notes[0].text);
});

test('trash/restore retain a complete snapshot and refresh its revision', async () => {
  const doc = document(), first = await save(doc), commit = randomUUID();
  const gone = await json(`documents/${doc.id}/trash`, 'POST', { commit, placement: { names: 'Mine' } });
  assert.equal(gone.token, commit);
  assert.equal((await request(`documents/${doc.id}`)).status, 404);
  assert.deepEqual(await json(`documents/${doc.id}/restore`, 'POST', {}), { placement: { names: 'Mine' } });
  const restored = await json(`documents/${doc.id}/info`);
  assert.notEqual(restored.revision, first.revision);
  assert.deepEqual(await json(`documents/${doc.id}/restore`, 'POST', {}), { placement: null });
  await json(`documents/${doc.id}/trash`, 'POST', { commit: randomUUID(), placement: {} });
  const purged = await json('trash/purge', 'POST', { before: Date.now() / 1000 + 1 });
  assert.equal(purged.removed.length > 0, true);
  assert.deepEqual(await json(`documents/${doc.id}/restore`, 'POST', {}), { placement: null });
});

test('library state uses CAS independently of documents', async () => {
  assert.equal((await json('library')).revision, null);
  const state = { catalog: { folders: [{ id: 'f1', name: 'Reading' }] }, zotero: {}, revision: null, commit: randomUUID() };
  const first = await json('library', 'PUT', state);
  assert.equal((await json('library')).catalog.folders[0].name, 'Reading');
  assert.equal((await request('library', 'PUT', { ...state, commit: randomUUID() })).status, 409);
  assert.equal((await json('library')).revision, first.revision);
});

test('pending references can be read but do not appear in the index', async () => {
  const doc = document(); doc.meta = null; doc.source.html = null; doc.source.reference = 'https://example.com/paper';
  await save(doc);
  assert.equal((await json(`documents/${doc.id}`)).document.source.reference, doc.source.reference);
  assert.equal((await request(`documents/${doc.id}/info`)).status, 404);
  assert.equal((await json('documents')).documents.some(row => row.id === doc.id), false);
});

test('binary checksum failure cannot register a dangling blob reference', async () => {
  const result = await request(`blobs/${'b'.repeat(64)}`, 'PUT', new TextEncoder().encode('wrong checksum'));
  assert.ok(result.status >= 400);
  assert.equal(await db.prepare('SELECT hash FROM blobs WHERE hash = ?1').bind('b'.repeat(64)).first(), null);
});

test('empty blobs and interrupted registry writes can be repaired', async () => {
  const bytes = new Uint8Array(0), hash = digest(bytes);
  await bucket.put(`blobs/${hash}`, bytes, { sha256: hash });
  assert.equal((await request(`blobs/${hash}`, 'HEAD')).status, 404);
  assert.equal((await request(`blobs/${hash}`, 'PUT', bytes)).status, 200);
  assert.equal((await request(`blobs/${hash}`, 'HEAD')).status, 200);
});

test('replaying a trash token cannot delete a newly imported document with the same ID', async () => {
  const doc = document(), commit = randomUUID();
  await save(doc);
  await json(`documents/${doc.id}/trash`, 'POST', { commit, placement: {} });
  doc.meta.title = 'New import';
  await save(doc);
  await json(`documents/${doc.id}/trash`, 'POST', { commit, placement: {} });
  assert.equal((await json(`documents/${doc.id}/info`)).meta.title, 'New import');
});

test('index pagination is exclusive, complete and does not require R2 snapshots', async () => {
  const prefix = 'zz-page-';
  await db.batch(Array.from({ length: 105 }, (_, index) => {
    const id = prefix + String(index).padStart(3, '0');
    const info = { id, meta: { id }, titles: {}, languages: [], note_count: 0, created: 1, updated: 1, revision: randomUUID(), readable: false };
    return db.prepare('INSERT INTO documents VALUES (?1, ?2, ?3, ?4, 1, 1)').bind(id, info.revision, 'no-snapshot-needed', JSON.stringify(info));
  }));
  const first = await json('documents?after=' + prefix);
  assert.equal(first.documents.length, 100);
  const second = await json('documents?after=' + first.next);
  assert.equal(second.documents.length, 5);
  assert.equal(second.next, null);
  assert.equal(new Set([...first.documents, ...second.documents].map(row => row.id)).size, 105);
});
