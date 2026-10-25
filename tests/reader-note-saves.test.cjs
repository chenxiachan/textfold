const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function reader(post) {
  const original = { id: 'n-one', anchor: { unit: 'p1' }, text: 'Original', color: 'yellow', _base: { text: 'Original' } };
  const alerts = [], calls = [], boxes = [];
  const document = { querySelector: () => boxes[0] || null, createElement: () => ({ setAttribute() {}, set textContent(value) { alerts.push(value); }, hidden: false }), body: { append: box => boxes.push(box) } };
  const context = vm.createContext({
    document,
    notes: [original], acknowledged: new Map([[original.id, original]]), noteSaves: new Map(), failedNoteFields: new Map(),
    D: { app: true, meta: { id: 'md-review' } }, indexNotes() {}, notesChanged() {}, keyOf() { return 'p1'; },
    popNote: null, T: { en: { sync_save_failed: 'Save failed' } }, ui: 'en', alert: message => alerts.push(message),
    post: async (url, body) => { calls.push(JSON.parse(JSON.stringify(body.note || body))); return post(body.note, calls.length, body); },
  });
  const source = fs.readFileSync(path.join(__dirname, '../textfold/web/reader.js'), 'utf8');
  // Exercise the actual reader queue, with just its DOM and HTTP boundaries replaced.
  const start = source.indexOf('  function saveNote(n)'), deletion = source.indexOf('  function deleteNote(n)', start);
  const end = source.indexOf('\n  }', deletion) + 5;
  assert.ok(start >= 0 && end > start);
  const errorStart = source.indexOf('  function saveError(error)'), errorEnd = source.indexOf('  const noteSaves', errorStart);
  vm.runInContext(source.slice(errorStart, errorEnd) + source.slice(start, end), context);
  return { context, calls, alerts, original };
}

test('same-text retry after a failed save persists the visible draft', async () => {
  const h = reader(async (note, call) => {
    if (call === 1) throw new Error('Temporary local HTTP failure');
    return { notes: [note] };
  });
  await assert.rejects(h.context.saveNote({ ...h.original, text: 'New research note' }));
  assert.equal(h.context.notes[0].text, 'New research note');
  await h.context.saveNote({ ...h.context.notes[0] });
  assert.equal(h.calls[1].text, 'New research note');
  assert.equal(h.context.notes[0].text, 'New research note');
  assert.equal(h.alerts.length, 1);
});

test('queued independent edit retries failed text and then releases its intent after acknowledgement', async () => {
  const h = reader(async (note, call) => {
    if (call === 1) throw new Error('Temporary local HTTP failure');
    return { notes: [{ ...note, text: 'Server merged text' }] };
  });
  const draft = { ...h.original, text: 'New research note' };
  const first = h.context.saveNote(draft);
  const second = h.context.saveNote({ ...draft, color: 'blue' });
  await assert.rejects(first);
  await second;
  assert.equal(h.calls[1].text, 'New research note');
  assert.equal(h.calls[1].color, 'blue');
  await h.context.saveNote({ ...h.context.notes[0], color: 'green' });
  assert.equal(h.calls[2].text, 'Server merged text');
  assert.equal(h.context.failedNoteFields.size, 0);
});

test('successful queued edits keep the server acknowledgement for independent fields', async () => {
  const h = reader(async note => ({ notes: [{ ...note, text: 'Server merged text' }] }));
  const draft = { ...h.original, text: 'New research note' };
  const first = h.context.saveNote(draft);
  const second = h.context.saveNote({ ...draft, color: 'blue' });
  await first; await second;
  assert.equal(h.calls[1].text, 'Server merged text');
  assert.equal(h.calls[1].color, 'blue');
});

test('background synchronization leaves a failed visible draft available for retry', async () => {
  const h = reader(async () => { throw new Error('Temporary local HTTP failure'); });
  await assert.rejects(h.context.saveNote({ ...h.original, text: 'Unsent draft' }));
  const source = fs.readFileSync(path.join(__dirname, '../textfold/web/reader.js'), 'utf8');
  let refresh, scheduled;
  Object.assign(h.context, {
    document: { addEventListener(name, callback) { refresh = callback; } },
    asking: new Map(), chat: { hidden: true }, busy: false, stage: null, tr: null, holding: false, drag: null, rafId: 0,
    setTimeout(callback) { scheduled = callback; return 1; },
    clearTimeout() {},
    fetch() { assert.fail('Background refresh must not overwrite the unsaved draft'); },
  });
  vm.runInContext(source.slice(source.indexOf('  let notesRefreshPending'), source.indexOf('  function saveNote(n)')), h.context);
  await refresh();
  await scheduled();
  assert.equal(h.context.notes[0].text, 'Unsent draft');
});

test('explicit deletion clears failed draft tracking only after its acknowledgement', async () => {
  const h = reader(async (note, call) => {
    if (call === 1) throw new Error('Temporary local HTTP failure');
    return { notes: [] };
  });
  await assert.rejects(h.context.saveNote({ ...h.original, text: 'Unsent draft' }));
  assert.equal(h.context.failedNoteFields.size, 1);
  await h.context.deleteNote(h.context.notes[0]);
  assert.equal(h.context.failedNoteFields.size, 0);
  assert.equal(h.context.acknowledged.size, 0);
});
