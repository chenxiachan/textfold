const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm');

function reader(initial = []) {
  const source = fs.readFileSync(path.join(__dirname, '../textfold/web/reader.js'), 'utf8');
  const listener = source.indexOf("  document.addEventListener('tf-storage-synced'");
  const declarations = source.indexOf('  let notesRefreshPending');
  const start = declarations >= 0 ? declarations : listener;
  const end = source.indexOf('  function saveNote(n)', start);
  assert.ok(start >= 0 && end > start);
  let event, nextTimer = 0;
  const timers = new Map(), delays = [], drawn = [], calls = [];
  const scope = { notes: initial, noteAliases: {}, notesOf: new Map(), acknowledged: new Map(),
    noteSaves: new Map(), failedNoteFields: new Map(), asking: new Map(), popNote: null,
    chat: { hidden: true }, busy: false, stage: null, tr: null, holding: false, drag: null, rafId: 0,
    D: { meta: { id: 'fixture' } }, document: { hidden: false, addEventListener(name, fn) { event = fn; } },
    setTimeout(fn, delay) { const id = ++nextTimer; timers.set(id, fn); delays.push(delay); return id; }, clearTimeout(id) { timers.delete(id); },
    indexNotes() { scope.notesOf.clear(); for (const n of scope.notes) {
      const key = scope.keyOf(n.anchor); const group = scope.notesOf.get(key) || [];
      group.push(n); scope.notesOf.set(key, group);
    } }, keyOf(a) { return a.on === 'paper' ? '¶' : a.unit || `§${a.section}`; },
    notesChanged(ids) { drawn.push([...ids]); },
    response: { notes: initial, aliases: {} },
    fetch: async () => { calls.push(1); return { ok: true, json: async () => scope.response }; }
  };
  scope.indexNotes();
  vm.runInNewContext(source.slice(start, end), scope);
  const deletion = source.indexOf('  function deleteNote(n)');
  vm.runInNewContext(source.slice(deletion, source.indexOf('  // ── a selection', deletion)), scope);
  return { scope, drawn, calls, timers, delays, event: () => event(), async flush() {
    const pending = [...timers.values()]; timers.clear();
    for (const fn of pending) await fn();
  } };
}
const note = (id, unit, text = 'A note') => ({ id, anchor: { unit }, text, _base: { text }, _revision: 'one' });

test('an unchanged sync retains warm zoom frames, including revision-only acknowledgements', async () => {
  for (const initial of [[], [note('a', 'p1')]]) {
    const h = reader(initial);
    h.scope.response = { notes: initial.map(n => ({ _revision: 'two', _base: { text: 'new base' }, text: n.text, anchor: n.anchor, id: n.id })), aliases: { old: 'a' } };
    await h.event(); await h.flush();
    assert.deepEqual(h.drawn, [], 'unchanged rendering must not call notesChanged and invalidate zoom frames');
    assert.equal(h.scope.noteAliases.old, 'a');
    if (initial.length) assert.equal(h.scope.acknowledged.get('a')._revision, 'two');
  }
});

test('changed, moved and deleted notes redraw only their affected passages', async () => {
  const h = reader([note('a','p1'), note('b','p2'), note('c','p3')]);
  h.scope.response = { notes: [note('a','p4','Changed'), note('b','p2')] };
  await h.event(); await h.flush();
  assert.deepEqual(h.drawn.map(ids => ids.sort()), [['p1','p3','p4']]);
});

test('a sync waits until a zoom gesture and local draft have finished', async () => {
  for (const guard of ['busy','stage','tr','holding','drag','rafId','popNote']) {
    const h = reader([note('a','p1')]); h.scope[guard] = true;
    h.scope.response = { notes: [note('a','p1','Remote')] };
    await h.event(); await h.flush();
    assert.equal(h.calls.length, 0, guard); assert.deepEqual(h.drawn, []);
    h.scope[guard] = false; await h.flush();
    assert.equal(h.calls.length, 1, guard); assert.equal(h.scope.notes[0].text, 'Remote');
  }
});

test('a zoom or local save starting during fetch cannot be overwritten by its response', async () => {
  for (const guard of ['busy','draft','save']) {
    const h = reader([note('a','p1')]); let release;
    h.scope.fetch = () => { h.calls.push(1); return new Promise(resolve => { release = resolve; }); };
    const event = h.event(), flushing = h.flush(); await Promise.resolve();
    if (guard === 'save') h.scope.noteSaves.set('a', Promise.resolve());
    else if (guard === 'draft') h.scope.popNote = { id: 'a' };
    else h.scope.busy = true;
    release({ok:true,json:async()=>({notes:[note('a','p1','Remote')]})});
    await event; await flushing;
    assert.equal(h.scope.notes[0].text, 'A note'); assert.deepEqual(h.drawn, []);
    h.scope.noteSaves.clear(); h.scope.popNote = null; h.scope.busy = false;
    h.scope.response = { notes: [note('a','p1','Latest')] };
    h.scope.fetch = async () => ({ok:true,json:async()=>h.scope.response});
    await h.flush(); assert.equal(h.scope.notes[0].text, 'Latest');
  }
});

test('burst notifications coalesce and a newer notification discards an in-flight response', async () => {
  const h = reader([note('a','p1')]); let release;
  h.scope.fetch = () => { h.calls.push(1); return new Promise(resolve => { release = resolve; }); };
  for (let i=0; i<10; i++) h.event();
  const flushing = h.flush(); await Promise.resolve();
  assert.equal(h.calls.length,1);
  h.event(); release({ok:true,json:async()=>({notes:[note('a','p1','Stale')]})});
  await flushing; assert.equal(h.scope.notes[0].text,'A note');
  h.scope.fetch = async () => ({ok:true,json:async()=>({notes:[note('a','p1','Latest')]})});
  await h.flush(); assert.equal(h.scope.notes[0].text,'Latest');
});

test('a local edit completed during HTTP still rejects the older reader snapshot', async () => {
  const h = reader([note('a','p1')]); let release;
  h.scope.fetch = () => new Promise(resolve => { release = resolve; });
  h.event(); const flushing = h.flush(); await Promise.resolve();
  h.scope.notes = [note('a','p1','Saved locally while waiting')];
  h.scope.indexNotes();
  release({ok:true,json:async()=>({notes:[note('a','p1','Older snapshot')]})});
  await flushing;
  assert.equal(h.scope.notes[0].text,'Saved locally while waiting');
  assert.deepEqual(h.drawn,[]);
});

test('conversation branch changes and note ordering remain visible', async () => {
  const h = reader([note('a','p1'),note('b','p1')]);
  h.scope.response = { notes:[note('b','p1'),{...note('a','p1'),messages:[{id:'branch',text:'A reply'}]}] };
  await h.event(); await h.flush();
  assert.deepEqual(h.drawn,[['p1']]);
});

test('a transient failed HTTP, fetch or JSON response retains the only refresh hint', async () => {
  for (const failure of [async()=>({ok:false}), async()=>{throw Error('offline');}, async()=>({ok:true,json:async()=>{throw Error('invalid JSON');}})]) {
    const h=reader([note('a','p1')]); h.scope.fetch=failure;
    h.event(); await h.flush();
    assert.equal(h.timers.size,1,'keep the last hint after a transient read failure');
    assert.ok(h.delays.at(-1)>=1000,'failure retries must back off');
    h.scope.fetch=async()=>({ok:true,json:async()=>({notes:[note('a','p1','Recovered')]})});
    await h.flush(); assert.equal(h.scope.notes[0].text,'Recovered');
    assert.equal(h.timers.size,0);
  }
});

test('persistent failures and long-lived editors do not retry at animation cadence', async () => {
  const h=reader(); h.scope.fetch=async()=>{throw Error('offline');};
  h.event(); for(let i=0;i<10;i++) await h.flush();
  assert.ok(h.delays.at(-1)>=30000); assert.ok(h.delays.at(-1)<=30000);
  h.scope.popNote={id:'draft'}; await h.flush();
  assert.ok(h.delays.at(-1)>=1000);
});

test('an optimistic deletion blocks refresh until the delete POST is acknowledged', async () => {
  const h=reader([note('a','p1')]); let release;
  h.scope.D.app=true; h.scope.post=()=>new Promise(resolve=>{release=resolve;});
  const deletion=h.scope.deleteNote(h.scope.notes[0]); await Promise.resolve();
  h.drawn.length=0; h.event(); await h.flush();
  assert.equal(h.calls.length,0,'an old server snapshot must not resurrect the deleting note');
  assert.deepEqual(h.scope.notes,[]);
  release({notes:[]}); await deletion;
  h.scope.response={notes:[]}; await h.flush();
  assert.deepEqual(h.scope.notes,[]); assert.deepEqual(h.drawn,[]);
});
