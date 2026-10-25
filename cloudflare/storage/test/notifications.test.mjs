import { before, after, test } from 'node:test';
import assert from 'node:assert/strict';
import { randomUUID } from 'node:crypto';
import { readFile } from 'node:fs/promises';
import { Miniflare, convertV4MiniflareOptions } from 'miniflare';
import { migrate } from './schema.mjs';

let mf, library;
const auth = 'Basic ' + Buffer.from('reader:fixture-only').toString('base64');
const options = { name: 'storage-notifications', modules: true, scriptPath: '.wrangler/build/index.js',
  compatibilityDate: '2026-10-23', compatibilityFlags: ['nodejs_compat'], d1Databases: ['DB'], r2Buckets: ['BLOBS'],
  durableObjects: { NOTIFICATIONS: { className: 'LibraryNotifications', useSQLite: true } },
  bindings: { STORAGE_USERNAME: 'reader', STORAGE_PASSWORD: 'fixture-only' } };
before(async () => {
  mf = new Miniflare(convertV4MiniflareOptions(options));
  await migrate(await mf.getD1Database('DB'));
  library = (await (await call('v2/health')).json()).library_id;
});
after(async () => { await mf?.dispose(); });
function call(path, method = 'GET', body, headers = {}) {
  return mf.dispatchFetch('http://storage.test/' + path, { method,
    headers: { Authorization: auth, 'Content-Type': 'application/json', ...headers },
    ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
}
function nextMessage(socket, timeout = 3000) {
  return new Promise((resolve, reject) => {
    const onMessage = event => { cleanup(); resolve(JSON.parse(event.data)); };
    const timer = setTimeout(() => { cleanup(); reject(new Error('Notification timeout')); }, timeout);
    const cleanup = () => { clearTimeout(timer); socket.removeEventListener('message', onMessage); };
    socket.addEventListener('message', onMessage);
  });
}
async function connect(headers = {}) {
  const response = await call('v2/events', 'GET', undefined, { Upgrade: 'websocket', ...headers });
  assert.equal(response.status, 101);
  const socket = response.webSocket, ready = nextMessage(socket);
  socket.accept();
  assert.deepEqual(await ready, { type: 'ready', protocol: 'textfold/changes-ws-v1', library_id: library });
  return socket;
}
function payload(changes, revision = null, state = 'active') {
  return { operation_id: randomUUID(), revision, state, placement: {}, changes };
}
const change = (key, value) => ({ key, value, deleted: false });

test('authenticated capability registers before ready, and failed authentication cannot subscribe', async () => {
  const health = await (await call('v2/health')).json();
  assert.deepEqual(health.notifications, { protocol: 'textfold/changes-ws-v1', path: '/v2/events' });
  assert.equal((await call('v2/events', 'GET', undefined, { Upgrade: 'websocket', Authorization: 'Basic wrong' })).status, 401);
  assert.equal((await call('v2/events')).status, 426);
  const socket = await connect({ 'X-TextFold-Library': 'untrusted-client-library' });
  socket.close();
});

test('document, deletion and library commits broadcast committed cursors after hibernation', async () => {
  const a = await connect(), b = await connect();
  try {
    await mf.unsafeEvictDurableObject('storage-notifications', 'LibraryNotifications', {
      name: 'library-notifications:' + library, webSockets: 'hibernate',
    });
    const id = 'md-' + randomUUID();
    let nextA = nextMessage(a), nextB = nextMessage(b);
    const created = await call('v2/documents/' + id, 'PUT', payload([change('meta', { id, title: 'Private title' }), change('source/html', 'Private source')]));
    assert.equal(created.status, 200);
    const ack = await created.json();
    const first = await nextA;
    assert.deepEqual(await nextB, first);
    assert.deepEqual(Object.keys(first).sort(), ['cursor', 'library_id', 'type']);
    assert.equal(first.library_id, library);
    assert.equal(first.type, 'changed');
    nextB = nextMessage(b);
    assert.equal((await call('v2/documents/' + id, 'PUT', payload([], ack.revision, 'deleted'))).status, 200);
    assert.ok((await nextB).cursor > first.cursor);
    nextB = nextMessage(b);
    assert.equal((await call('v2/library', 'PUT', payload([change('catalog/folders', [])]))).status, 200);
    const final = await nextB;
    const health = await (await call('v2/health')).json();
    assert.equal(final.cursor, health.cursor);
    assert.equal((await call('v2/documents/' + id, 'PUT', payload([], ack.revision))).status, 409);
  } finally { a.close(); b.close(); }
});

test('permanent deletion notifies only after the terminal snapshot is committed', async () => {
  const id = 'md-purge-notification';
  const created = await (await call('v2/documents/' + id, 'PUT', payload([change('meta', { id, title: 'Trash' }), change('source/html', 'Original')]))).json();
  const trashed = await (await call('v2/documents/' + id, 'PUT', payload([], created.revision, 'trash'))).json();
  const socket = await connect();
  try {
    const changed = nextMessage(socket);
    const result = await call('v2/documents/' + id + '/purge', 'POST', { ...payload([], trashed.revision, 'deleted'), purge: true });
    assert.equal(result.status, 200);
    const hint = await changed;
    const state = await (await call('v2/documents/' + id)).json();
    assert.equal(state.purged, true); assert.deepEqual(state.records, {});
    assert.equal(hint.cursor, (await (await call('v2/health')).json()).cursor);
  } finally { socket.close(); }
});

test('clients cannot broadcast application messages or subscribe to another library', async () => {
  const socket = await connect();
  try {
    const closed = new Promise(resolve => socket.addEventListener('close', resolve, { once: true }));
    socket.send(JSON.stringify({ type: 'subscribe', library_id: 'different-library' }));
    assert.equal((await closed).code, 1008);
  } finally { socket.close(); }
});

test('library instance names isolate notifications between libraries', async () => {
  const namespace = await mf.getDurableObjectNamespace('NOTIFICATIONS');
  const otherLibrary = randomUUID();
  const response = await namespace.get(namespace.idFromName('library-notifications:' + otherLibrary)).fetch('https://notifications.internal/', {
    headers: { Upgrade: 'websocket', 'X-TextFold-Library': otherLibrary },
  });
  const other = response.webSocket, ready = nextMessage(other);
  other.accept();
  assert.equal((await ready).library_id, otherLibrary);
  const unexpected = [];
  other.addEventListener('message', event => unexpected.push(event.data));
  const local = await connect();
  try {
    const next = nextMessage(local);
    assert.equal((await call('v2/documents/md-' + randomUUID(), 'PUT', payload([]))).status, 200);
    assert.equal((await next).library_id, library);
    assert.deepEqual(unexpected, []);
  } finally { local.close(); other.close(); }
});

test('disabling notifications leaves authenticated HTTP synchronization available', async () => {
  const disabled = new Miniflare(convertV4MiniflareOptions({ ...options, name: 'disabled-notifications',
    bindings: { ...options.bindings, NOTIFICATIONS_DISABLED: 'true' } }));
  try {
    await migrate(await disabled.getD1Database('DB'));
    const headers = { Authorization: auth };
    const health = await (await disabled.dispatchFetch('http://storage.test/v2/health', { headers })).json();
    assert.equal(health.notifications, undefined);
    assert.equal((await disabled.dispatchFetch('http://storage.test/v2/events', { headers: { ...headers, Upgrade: 'websocket' } })).status, 404);
    assert.equal((await disabled.dispatchFetch('http://storage.test/v2/changes?cursor=0', { headers })).status, 200);
  } finally { await disabled.dispose(); }
});

test('broadcast RPC failure leaves the committed HTTP result and durable change feed intact', async () => {
  // A fixture-only proxy throws after D1 commits; no production test hooks or
  // credential strings are added to the service implementation.
  const wrapper = `import worker from './index.mjs';
    export default { fetch(request, env, ctx) {
      const failed = new Proxy(env, { get(target, key) {
        return key === 'NOTIFICATIONS' ? { getByName() { return { changed() { throw Error('fixture'); } }; } } : Reflect.get(target, key);
      }}); return worker.fetch(request, failed, ctx);
    }};`;
  const failed = new Miniflare(convertV4MiniflareOptions({ ...options, name: 'failed-notifications',
    scriptPath: undefined, durableObjects: {}, modules: [
      { type: 'ESModule', path: 'failure.mjs', contents: wrapper },
      { type: 'ESModule', path: 'index.mjs', contents: await readFile('.wrangler/build/index.js', 'utf8') },
    ] }));
  try {
    await migrate(await failed.getD1Database('DB'));
    const id = 'md-' + randomUUID();
    const result = await failed.dispatchFetch('http://storage.test/v2/documents/' + id, { method: 'PUT',
      headers: { Authorization: auth, 'Content-Type': 'application/json' },
      body: JSON.stringify(payload([change('meta', { id, title: 'Saved' }), change('source/html', 'Original')])) });
    assert.equal(result.status, 200);
    assert.equal((await (await failed.dispatchFetch('http://storage.test/v2/changes?cursor=0', { headers: { Authorization: auth } })).json()).changes.length, 1);
  } finally { await failed.dispose(); }
});
