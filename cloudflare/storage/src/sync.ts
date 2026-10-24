import { Failure, object, identity, revision, reply, jsonBody, snapshot, loadSnapshot, validateDocument, documentInfo } from "./index";
import type { StorageEnv, Json, ObjectValue } from "./index";
import { NOTIFICATION_PROTOCOL, notificationsEnabled, publishChanges } from "./notifications";

type Head = { id: string; revision: string; manifest_key: string; info_json: string; state: string; placement_json: string; created: number; updated: number };
type Stored = { record_key: string; value_json: string | null; object_key: string | null; deleted: number };
const LIBRARY = "@library";
const own = (value: ObjectValue, key: string): boolean => Object.hasOwn(value, key);
const put = (value: ObjectValue, key: string, item: Json): void => {
  Object.defineProperty(value, key, { value: item, enumerable: true, configurable: true, writable: true });
};

function canonical(value: Json): string {
  if (Array.isArray(value)) return "[" + value.map(canonical).join(",") + "]";
  if (value !== null && typeof value === "object") return "{" + Object.keys(value).sort().map(k => JSON.stringify(k) + ":" + canonical(value[k])).join(",") + "}";
  return JSON.stringify(value);
}

async function hash(value: Json): Promise<string> {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(canonical(value)));
  return Array.from(new Uint8Array(bytes), b => b.toString(16).padStart(2, "0")).join("");
}

async function legacyMessages(note: ObjectValue): Promise<ObjectValue[]> {
  if (Array.isArray(note.messages)) return note.messages.map(object);
  const turns = Array.isArray(note.thread) ? note.thread : note.ask ? [note.ask] : [];
  const nodes: ObjectValue[] = [];
  let parent: string | null = null;
  for (const raw of turns) {
    const turn = object(raw);
    const id: string = "m-" + (await hash([note.id ?? null, parent, turn])).slice(0, 32);
    nodes.push({ id: id + "-q", parent, role: "user", text: turn.q ?? "" });
    nodes.push({ ...turn, id: id + "-a", parent: id + "-q", role: "assistant", text: turn.a ?? "" });
    parent = id + "-a";
  }
  return nodes;
}

export async function split(doc: ObjectValue): Promise<ObjectValue> {
  const result: ObjectValue = { meta: doc.meta, previous: doc.previous };
  for (const group of ["source", "processing"]) {
    for (const [key, value] of Object.entries(object(doc[group]))) {
      if (key === "originals" || key === "replies") {
        for (const [id, item] of Object.entries(object(value))) result[`${group}.${key}/${id}`] = item;
      } else result[`${group}/${key}`] = value;
    }
  }
  for (const group of ["layers", "images", "extensions", "legacy"]) {
    for (const [id, value] of Object.entries(object(doc[group]))) result[`${group}/${id}`] = value;
  }
  if (doc.notes !== null) {
    const notes = object(doc.notes);
    result.notes = Object.fromEntries(Object.entries(notes).filter(([key]) => !["notes", "conflicts", "aliases"].includes(key)));
    const all = Array.isArray(notes.notes) ? notes.notes : [];
    for (let i = 0; i < all.length; i++) {
      const note = object(all[i]), id = typeof note.id === "string" ? note.id : "legacy-" + (await hash([i, note])).slice(0, 32);
      result[`annotation/${id}`] = Object.fromEntries(Object.entries(note).filter(([key]) => !["thread", "ask", "messages", "branches"].includes(key)));
      for (const node of await legacyMessages({ ...note, id })) result[`message/${id}/${node.id}`] = node;
    }
    if (Array.isArray(notes.conflicts)) for (const raw of notes.conflicts) { const item = object(raw); result[`conflict/${item.id}`] = item; }
    if (notes.aliases) for (const [id, value] of Object.entries(object(notes.aliases))) result[`alias/${id}`] = value;
  }
  return result;
}

function turns(nodes: ObjectValue[]): { heads: string[]; thread: Json[] } {
  const by = new Map(nodes.map(node => [String(node.id), node]));
  const parents = new Set(nodes.map(node => node.parent));
  const heads = [...by.keys()].filter(key => !parents.has(key)).sort();
  const path: ObjectValue[] = [], seen = new Set<string>();
  let id: string | null = heads.at(-1) ?? null;
  while (id) {
    if (seen.has(id) || !by.has(id)) throw new Failure(400, "Invalid conversation ancestry");
    seen.add(id);
    const node = by.get(id)!;
    path.push(node);
    id = typeof node.parent === "string" ? node.parent : null;
  }
  // Validate every branch, including branches other than the default view.
  for (const node of nodes) {
    const ancestors = new Set<string>();
    let current: ObjectValue | undefined = node;
    while (current) {
      const key = String(current.id);
      if (ancestors.has(key)) throw new Failure(400, "Conversation cycle");
      ancestors.add(key);
      if (current.parent !== null && !by.has(String(current.parent))) throw new Failure(400, "Missing parent message");
      current = current.parent === null ? undefined : by.get(String(current.parent));
    }
  }
  const thread: ObjectValue[] = [];
  for (const node of path.reverse()) {
    if (node.role === "user") thread.push({ q: node.text, a: "", message_id: node.id });
    else if (thread.length) Object.assign(thread[thread.length - 1], Object.fromEntries(Object.entries(node).filter(([k]) => !["id", "parent", "role", "text"].includes(k))), { a: node.text, message_id: node.id });
  }
  return { heads, thread };
}

export function join(id: string, values: ObjectValue, created: number, updated: number): ObjectValue {
  const doc: ObjectValue = { format: "textfold/document-v1", id, meta: values.meta ?? null, previous: values.previous ?? null,
    source: { html: null, markdown: null, xml: null, wiki_html: null, pdf: null, reference: null, originals: {} },
    processing: { layout: null, structured: null, replies: {} }, layers: {}, images: {}, extensions: {}, legacy: {}, notes: null, created, updated };
  for (const [key, value] of Object.entries(values)) {
    const at = key.indexOf("/");
    if (at < 0) continue;
    const group = key.slice(0, at), name = key.slice(at + 1);
    if (["source", "processing", "layers", "images", "extensions", "legacy"].includes(group)) put(object(doc[group]), name, value);
    else if (["source.originals", "processing.replies"].includes(group)) { const [a, b] = group.split("."); put(object(object(doc[a])[b]), name, value); }
  }
  if (own(values, "notes") || Object.keys(values).some(key => key.startsWith("annotation/") || key.startsWith("conflict/"))) {
    const notes: ObjectValue = { ...object(values.notes ?? {}), notes: [], conflicts: [], aliases: {} };
    for (const key of Object.keys(values).sort()) {
      if (key.startsWith("annotation/")) {
        const note = { ...object(values[key]) }, prefix = "message/" + key.slice(11) + "/";
        const nodes = Object.keys(values).sort().filter(k => k.startsWith(prefix)).map(k => object(values[k]));
        if (nodes.length) { const tree = turns(nodes); Object.assign(note, { messages: nodes, branches: tree.heads, thread: tree.thread }); }
        (notes.notes as Json[]).push(note);
      } else if (key.startsWith("conflict/")) (notes.conflicts as Json[]).push(values[key]);
      else if (key.startsWith("alias/")) put(object(notes.aliases), key.slice(6), values[key]);
    }
    doc.notes = notes;
  }
  return doc;
}

function librarySplit(state: ObjectValue): ObjectValue {
  const values: ObjectValue = {};
  for (const [key, value] of Object.entries(object(state.catalog))) {
    if (key === "sync_conflicts") { if (Array.isArray(value)) for (const raw of value) { const conflict = object(raw); values[`conflict/${conflict.id}`] = conflict; } continue; }
    if (key === "folders" && Array.isArray(value)) { values["catalog/folder_order"] = value.map(raw => object(raw).id); if (!value.length) values["catalog/folders"] = []; for (const raw of value) { const folder = object(raw); values[`catalog.folders/${folder.id}`] = folder; } }
    else if (value !== null && typeof value === "object" && !Array.isArray(value)) { if (!Object.keys(value).length) values[`catalog/${key}`] = {}; for (const [id, item] of Object.entries(value)) values[`catalog.${key}/${id}`] = item; }
    else values[`catalog/${key}`] = value;
  }
  for (const [id, value] of Object.entries(object(state.zotero))) values[`zotero/${id}`] = value;
  if (Object.keys(object(state.zotero)).length) values["zotero-order"] = Object.keys(object(state.zotero));
  return values;
}

function libraryJoin(values: ObjectValue): ObjectValue {
  const catalog: ObjectValue = {}, zotero: ObjectValue = {};
  for (const key of Object.keys(values).sort((a, b) => Number(!a.startsWith("catalog/")) - Number(!b.startsWith("catalog/")) || a.localeCompare(b))) {
    if (key.startsWith("catalog.folders/")) { catalog.folders ??= []; (catalog.folders as Json[]).push(values[key]); }
    else if (key.startsWith("catalog.")) { const at = key.indexOf("/"), group = key.slice(8, at); if (!own(catalog, group)) put(catalog, group, {}); put(object(catalog[group]), key.slice(at + 1), values[key]); }
    else if (key.startsWith("catalog/") && key !== "catalog/folder_order") put(catalog, key.slice(8), values[key]);
    else if (key.startsWith("zotero/")) put(zotero, key.slice(7), values[key]);
  }
  if (Array.isArray(catalog.folders)) {
    const order = Array.isArray(values["catalog/folder_order"]) ? values["catalog/folder_order"] : [];
    const positions = new Map(order.map((id, i) => [id, i]));
    catalog.folders.sort((a, b) => (positions.get(object(a).id) ?? order.length) - (positions.get(object(b).id) ?? order.length) || String(object(a).id).localeCompare(String(object(b).id)));
  }
  const order = Array.isArray(values["zotero-order"]) ? values["zotero-order"] : [];
  const positions = new Map(order.map((id, i) => [id, i]));
  const sorted = Object.fromEntries(Object.entries(zotero).sort(([a], [b]) => (positions.get(a) ?? order.length) - (positions.get(b) ?? order.length) || a.localeCompare(b)));
  return { catalog, zotero: sorted };
}

function immutable(before: ObjectValue, after: ObjectValue): boolean {
  if (before.meta === null || before.meta === undefined) return true;
  for (const [key, value] of Object.entries(before)) {
    if ((key.startsWith("source.originals/") || ["source/pdf", "source/markdown", "source/xml", "source/wiki_html"].includes(key)) && value !== null && canonical(value) !== canonical(after[key] ?? null)) return false;
  }
  const binary = before["source/pdf"] != null || Object.keys(before).some(k => k.startsWith("source.originals/"));
  return binary || before["source/html"] == null || before["source/html"] === after["source/html"];
}

async function current(env: StorageEnv, db: D1DatabaseSession, id: string): Promise<{ head: Head | null; values: ObjectValue }> {
  const [heads, rows] = await db.batch([
    db.prepare("SELECT * FROM sync_documents WHERE id=?1").bind(id),
    db.prepare("SELECT record_key,value_json,object_key,deleted FROM document_records WHERE document_id=?1").bind(id),
  ]);
  const head = (heads.results[0] as Head | undefined) ?? null;
  if (head) {
    const values: ObjectValue = {};
    for (const row of rows.results as Stored[]) {
      if (row.deleted) continue;
      values[row.record_key] = row.object_key ? (await loadSnapshot(env, row.object_key)).value : JSON.parse(row.value_json!);
    }
    return { head, values };
  }
  if (id === LIBRARY) {
    const legacy = await db.prepare("SELECT revision,manifest_key FROM library_state WHERE singleton=1").first<{ revision: string; manifest_key: string }>();
    return legacy ? { head: { id, revision: legacy.revision, manifest_key: legacy.manifest_key, info_json: "{}", state: "active", placement_json: "{}", created: 0, updated: 0 }, values: librarySplit(await loadSnapshot(env, legacy.manifest_key)) } : { head: null, values: {} };
  }
  const legacy = await db.prepare("SELECT * FROM documents WHERE id=?1").bind(id).first<Omit<Head, "state" | "placement_json">>();
  return legacy ? { head: { ...legacy, state: "active", placement_json: "{}" }, values: await split(await loadSnapshot(env, legacy.manifest_key)) } : { head: null, values: {} };
}

export async function handleSync(request: Request, env: StorageEnv, ctx: ExecutionContext): Promise<Response> {
  const url = new URL(request.url), path = url.pathname, db = env.DB.withSession("first-primary");
  if (path === "/v2/health" && request.method === "GET") {
    const library = await db.prepare("SELECT library_id,sync_enabled FROM storage_identity WHERE singleton=1").first<{ library_id: string; sync_enabled: number }>();
    const cursor = await db.prepare("SELECT COALESCE(MAX(sequence),0) AS cursor FROM change_log").first<{ cursor: number }>();
    return reply({ format: "textfold/storage-v2", library_id: library!.library_id, cursor: cursor!.cursor, sync_enabled: Boolean(library!.sync_enabled),
      ...(notificationsEnabled(env) ? { notifications: { protocol: NOTIFICATION_PROTOCOL, path: "/v2/events" } } : {}) });
  }
  if (path === "/v2/events" && request.method === "GET") {
    if (!notificationsEnabled(env)) throw new Failure(404, "Notifications unavailable");
    if (request.headers.get("Upgrade")?.toLowerCase() !== "websocket") throw new Failure(426, "WebSocket upgrade required");
    const library = await db.prepare("SELECT library_id FROM storage_identity WHERE singleton=1").first<{ library_id: string }>();
    return env.NOTIFICATIONS.getByName("library-notifications:" + library!.library_id).fetch(new Request("https://notifications.internal/", {
      headers: { Upgrade: "websocket", "X-TextFold-Library": library!.library_id },
    }));
  }
  if (path === "/v2/index" && request.method === "GET") {
    const rows = await db.prepare("SELECT id,revision,info_json,state FROM sync_documents WHERE id!=?1 AND id>?2 UNION ALL SELECT id,revision,info_json,'active' AS state FROM documents WHERE id>?2 AND id NOT IN (SELECT id FROM sync_documents) ORDER BY id LIMIT 100").bind(LIBRARY, url.searchParams.get("after") || "").all<{ id: string; revision: string; info_json: string; state: string }>();
    return reply({ documents: rows.results.map(row => ({ id: row.id, revision: row.revision, info: JSON.parse(row.info_json), state: row.state })), next: rows.results.length === 100 ? rows.results[99].id : null });
  }
  if (path === "/v2/changes" && request.method === "GET") {
    const cursor = Number(url.searchParams.get("cursor") || 0);
    if (!Number.isSafeInteger(cursor) || cursor < 0) throw new Failure(400, "Invalid sync cursor");
    const rows = await db.prepare("SELECT sequence,document_id,revision,state FROM change_log WHERE sequence>?1 ORDER BY sequence LIMIT 100").bind(cursor).all<{ sequence: number; document_id: string; revision: string; state: string }>();
    return reply({ changes: rows.results, cursor: rows.results.at(-1)?.sequence ?? cursor, more: rows.results.length === 100 });
  }
  const match = path.match(/^\/v2\/(?:documents\/([^/]+)|(library))$/);
  if (!match) throw new Failure(404, "Not found");
  const id = match[2] ? LIBRARY : identity(decodeURIComponent(match[1]));
  if (!match[2] && id === LIBRARY) throw new Failure(400, "Reserved document ID");
  const existing = await current(env, db, id), head = existing.head;
  if (request.method === "GET") {
    if (!head && id !== LIBRARY) throw new Failure(404, "Document not found");
    return reply({ records: existing.values, revision: head?.revision ?? null, state: head?.state ?? "active", placement: JSON.parse(head?.placement_json ?? "{}"), info: JSON.parse(head?.info_json ?? "{}") });
  }
  if (request.method !== "PUT") throw new Failure(405, "Method not allowed");
  const body = await jsonBody(request), operation = revision(body.operation_id), expected = revision(body.revision);
  if (!operation) throw new Failure(400, "Missing operation ID");
  const requestHash = await hash({ document_id: id, body });
  const replay = await db.prepare("SELECT request_hash,result_json FROM sync_operations WHERE operation_id=?1").bind(operation).first<{ request_hash: string; result_json: string }>();
  if (replay) {
    if (replay.request_hash !== requestHash) throw new Failure(409, "Operation ID already used with different content");
    return reply(JSON.parse(replay.result_json));
  }
  if (expected !== (head?.revision ?? null)) throw new Failure(409, "Cloud document changed; reconcile and retry");
  if (!["active", "trash", "deleted"].includes(String(body.state)) || !Array.isArray(body.changes) || body.changes.length > 20000) throw new Failure(400, "Invalid record changes");
  const values: ObjectValue = { ...existing.values };
  const changed = new Set<string>();
  for (const raw of body.changes) {
    const change = object(raw), key = change.key;
    if (typeof key !== "string" || !key || key.length > 1000 || /[\x00-\x1f]/.test(key) || changed.has(key) || typeof change.deleted !== "boolean") throw new Failure(400, "Invalid record key");
    if (key.split(/[/.]/).some(part => ["__proto__", "prototype", "constructor"].includes(part)) ||
      !(id === LIBRARY ? key === "zotero-order" || /^(catalog(?:\.[^/]+)?|zotero|conflict)\//.test(key) : ["meta", "previous", "notes"].includes(key) || /^(source(?:\.originals)?|processing(?:\.replies)?|layers|images|extensions|legacy|annotation|message|conflict|alias)\//.test(key))) throw new Failure(400, "Unknown record key");
    changed.add(key);
    if (change.deleted) delete values[key]; else { if (!own(change, "value")) throw new Failure(400, "Missing record value"); values[key] = change.value; }
  }
  if (id !== LIBRARY && !immutable(existing.values, values)) throw new Failure(409, "Original source is immutable");
  if (id !== LIBRARY) for (const [key, raw] of Object.entries(existing.values)) {
    if (!key.startsWith("message/")) continue;
    const before = object(raw), annotation = "annotation/" + key.split("/")[1];
    if (!own(values, annotation)) continue; // explicit deletion of the conversation
    if (!own(values, key)) {
      if (before.pending !== true) throw new Failure(409, "Confirmed conversation messages are immutable");
    } else if (canonical(raw) !== canonical(values[key])) {
      const after = object(values[key]);
      if (before.pending !== true || before.id !== after.id || before.parent !== after.parent || before.role !== after.role || (before.role === "user" && before.text !== after.text)) throw new Failure(409, "Confirmed conversation messages are immutable");
    }
  }
  const created = head?.created || (typeof body.created === "number" && body.created > 0 ? body.created : Date.now() / 1000), updated = Date.now() / 1000;
  const document = id === LIBRARY ? libraryJoin(values) : join(id, values, created, updated);
  if (id !== LIBRARY) {
    const refs = new Map<string, { sha256: string; size: number }>();
    validateDocument(document, id, refs);
    const registered = await db.prepare("SELECT COUNT(*) AS total FROM json_each(?1) AS ref JOIN blobs ON blobs.hash=json_extract(ref.value,'$.sha256') AND blobs.size=json_extract(ref.value,'$.size')").bind(JSON.stringify([...refs.values()])).first<{ total: number }>();
    if (registered?.total !== refs.size) throw new Failure(409, "Document references missing blobs");
  }
  const commit = crypto.randomUUID(), key = `sync-manifests/${commit}.json`, placement = object(body.placement ?? {});
  const info = id === LIBRARY ? {} : documentInfo(document, commit, created, updated);
  await snapshot(env, key, document);
  // The manifest is a recovery/export snapshot. Individual records live in D1;
  // large record bodies use immutable R2 objects to stay below D1's row limit.
  const stored: { key: string; value: string | null; object: string | null; deleted: number }[] = [];
  for (const recordKey of new Set([...Object.keys(existing.values), ...Object.keys(values)])) {
    if (head && own(existing.values, recordKey) && own(values, recordKey) && canonical(existing.values[recordKey]) === canonical(values[recordKey])) continue;
    const value = own(values, recordKey) ? values[recordKey] : existing.values[recordKey], text = JSON.stringify(value);
    let objectKey: string | null = null;
    if (new TextEncoder().encode(text).byteLength > 128 * 1024) { objectKey = `record-values/${await hash(value)}.json`; await snapshot(env, objectKey, { value }); }
    stored.push({ key: recordKey, value: objectKey ? null : text, object: objectKey, deleted: own(values, recordKey) ? 0 : 1 });
  }
  const initialized = await db.prepare("SELECT revision FROM sync_documents WHERE id=?1").bind(id).first<{ revision: string }>();
  if (!initialized) {
    // A first v2 write imports unchanged legacy records too.
    const keys = new Set(stored.map(row => row.key));
    for (const [recordKey, value] of Object.entries(values)) if (!keys.has(recordKey)) {
      const text = JSON.stringify(value);
      let objectKey: string | null = null;
      if (new TextEncoder().encode(text).byteLength > 128 * 1024) { objectKey = `record-values/${await hash(value)}.json`; await snapshot(env, objectKey, { value }); }
      stored.push({ key: recordKey, value: objectKey ? null : text, object: objectKey, deleted: 0 });
    }
  }
  const result = { revision: commit, info, state: body.state };
  const guard = "EXISTS (SELECT 1 FROM sync_documents WHERE id=?1 AND revision=?2)";
  const statements: D1PreparedStatement[] = [initialized
    ? db.prepare("UPDATE sync_documents SET revision=?1,manifest_key=?2,info_json=?3,state=?4,placement_json=?5,updated=?6 WHERE id=?7 AND revision=?8").bind(commit, key, JSON.stringify(info), body.state, JSON.stringify(placement), updated, id, expected)
    : id === LIBRARY
      ? db.prepare("INSERT INTO sync_documents SELECT ?1,?2,?3,?4,?5,?6,?7,?8 WHERE (?9 IS NULL AND NOT EXISTS(SELECT 1 FROM library_state WHERE singleton=1)) OR EXISTS(SELECT 1 FROM library_state WHERE singleton=1 AND revision=?9) ON CONFLICT(id) DO NOTHING").bind(id, commit, key, JSON.stringify(info), body.state, JSON.stringify(placement), created, updated, expected)
      : db.prepare("INSERT INTO sync_documents SELECT ?1,?2,?3,?4,?5,?6,?7,?8 WHERE (?9 IS NULL AND NOT EXISTS(SELECT 1 FROM documents WHERE id=?1)) OR EXISTS(SELECT 1 FROM documents WHERE id=?1 AND revision=?9) ON CONFLICT(id) DO NOTHING").bind(id, commit, key, JSON.stringify(info), body.state, JSON.stringify(placement), created, updated, expected)];
  // Group JSON bindings so each statement remains small, even on large libraries.
  let chunk: typeof stored = [], size = 0;
  const flush = () => {
    if (!chunk.length) return;
    statements.push(db.prepare(`INSERT INTO document_records(document_id,record_key,value_json,object_key,deleted,revision) SELECT ?1,json_extract(value,'$.key'),json_extract(value,'$.value'),json_extract(value,'$.object'),json_extract(value,'$.deleted'),?2 FROM json_each(?3) WHERE ${guard} ON CONFLICT(document_id,record_key) DO UPDATE SET value_json=excluded.value_json,object_key=excluded.object_key,deleted=excluded.deleted,revision=excluded.revision`).bind(id, commit, JSON.stringify(chunk)));
    chunk = []; size = 0;
  };
  for (const row of stored) { const length = JSON.stringify(row).length; if (size + length > 400000) flush(); chunk.push(row); size += length; }
  flush();
  if (id === LIBRARY) statements.push(db.prepare(`INSERT INTO library_state SELECT 1,?2,?3 WHERE ${guard} ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,manifest_key=excluded.manifest_key`).bind(id, commit, key));
  else if (body.state === "active") statements.push(db.prepare(`INSERT INTO documents SELECT ?1,?2,?3,?4,?5,?6 WHERE ${guard} ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,manifest_key=excluded.manifest_key,info_json=excluded.info_json,updated=excluded.updated`).bind(id, commit, key, JSON.stringify(info), created, updated));
  else statements.push(db.prepare(`DELETE FROM documents WHERE id=?1 AND ${guard}`).bind(id, commit));
  statements.push(db.prepare(`INSERT INTO sync_operations SELECT ?3,?4,?1,?2,?5 WHERE ${guard}`).bind(id, commit, operation, requestHash, JSON.stringify(result)));
  statements.push(db.prepare(`INSERT INTO change_log(document_id,revision,state,operation_id) SELECT ?1,?2,?3,?4 WHERE ${guard}`).bind(id, commit, body.state, operation));
  statements.push(db.prepare(`UPDATE storage_identity SET sync_enabled=1 WHERE singleton=1 AND ${guard}`).bind(id, commit));
  const committed = await db.batch(statements);
  if (committed[0].meta.changes !== 1) throw new Failure(409, "Cloud document changed; reconcile and retry");
  if (notificationsEnabled(env)) ctx.waitUntil(publishChanges(env).catch(() => {
    console.error(JSON.stringify({ event: "storage_notification_delivery_failed" }));
  }));
  return reply(result);
}
