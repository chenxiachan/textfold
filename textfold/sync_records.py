"""Document records and lossless three-way reconciliation.

Private to storage: callers still exchange complete Documents. Clocks never
decide which user content survives. Missing keys represent explicit deletions.
"""
import copy
import hashlib
import json

from .documents import LibraryState


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _message_history(note):
    nodes = copy.deepcopy(note.get("messages", []))
    if "thread" not in note and not note.get("ask"):
        return nodes, None
    turns = note["thread"] if "thread" in note else [note["ask"]]
    parent = None
    for turn in turns:
        clean = {k: v for k, v in turn.items() if k not in ("message_id", "id", "parent", "role", "text")}
        overlay = note.get("legacy_language") or {}
        old = next((n for n in nodes if n["id"] == turn.get("message_id")), {})
        if (old.get("id") in overlay.get("messages", []) and old.get("lang") == "en"
                and clean.get("lang") == overlay.get("code")):
            clean["lang"] = "en"   # the displayed correction never rewrites a confirmed message
        questions = {n["id"] for n in nodes if n.get("role") == "user" and n.get("parent") == parent and n.get("text") == clean.get("q", "")}
        candidates = [n for n in nodes if n.get("role") == "assistant" and n.get("parent") in questions
                      and n.get("text") == clean.get("a", "") and all(n.get(k) == v for k, v in clean.items() if k not in ("q", "a"))]
        known = next((n for n in candidates if n["id"] == turn.get("message_id")), None) or next(iter(sorted(candidates, key=lambda n: n["id"])), None)
        if known:
            parent = known["id"]
            continue
        identity = "m-" + digest([note.get("id"), parent, clean])[:32]
        question = {"id": identity + "-q", "parent": parent, "role": "user", "text": clean.get("q", "")}
        answer = {**clean, "id": identity + "-a", "parent": question["id"], "role": "assistant", "text": clean.get("a", "")}
        for node in (question, answer):
            if not any(n["id"] == node["id"] for n in nodes): nodes.append(node)
        parent = answer["id"]
    return nodes, parent


def messages(note):
    """The documented thread is canonical; extra branch history stays beside it."""
    return _message_history(note)[0]


def branches(note):
    all_nodes = messages(note)
    nodes = {m["id"]: m for m in all_nodes}
    if len(nodes) != len(all_nodes):
        raise ValueError("Duplicate conversation message ID")
    for node in all_nodes:
        seen, current = set(), node
        while current is not None:
            identity = current["id"]
            if identity in seen or current.get("role") not in ("user", "assistant") or not isinstance(current.get("text"), str):
                raise ValueError("Invalid conversation ancestry")
            seen.add(identity)
            parent = current.get("parent")
            if parent is not None and parent not in nodes:
                raise ValueError("Missing parent message")
            current = nodes.get(parent)
    parents = {m.get("parent") for m in nodes.values()}
    return sorted(k for k in nodes if k not in parents)


def conversation(note, head=None):
    if head is None and "thread" in note:
        return _language_view(note, copy.deepcopy(note["thread"]))
    nodes = {m["id"]: m for m in messages(note)}
    heads = branches(note)
    if head is not None and head not in nodes:
        raise ValueError("Unknown conversation branch")
    identity = head or (heads[-1] if heads else None)
    path, seen = [], set()
    while identity:
        if identity in seen or identity not in nodes:
            raise ValueError("Invalid conversation ancestry")
        seen.add(identity)
        path.append(nodes[identity])
        identity = nodes[identity].get("parent")
    path.reverse()
    turns = []
    for node in path:
        if node["role"] == "user":
            turns.append({**{k: v for k, v in node.items() if k in ("pending", "error", "lang")}, "q": node["text"], "a": "", "message_id": node["id"]})
        elif turns:
            turns[-1].update({k: v for k, v in node.items() if k not in ("id", "parent", "role", "text")})
            turns[-1].update(a=node["text"], message_id=node["id"])
    return _language_view(note, turns)


def _language_view(note, turns):
    overlay = note.get("legacy_language") or {}
    for turn in turns:
        if turn.get("message_id") in overlay.get("messages", []) and turn.get("lang") == "en":
            turn["lang"] = overlay["code"]
    return turns


def settle_language(note, base):
    """Label old native-language turns without changing immutable message bytes."""
    before = copy.deepcopy(note)
    if note.get("anchor", {}).get("lang") == "en":
        note["anchor"]["lang"] = base
    if not note.get("legacy_language"):
        nodes, head = _message_history(note)
        old = [n["id"] for n in nodes if n.get("role") == "assistant" and n.get("lang") == "en"]
        if old:
            note["legacy_language"] = {"code": base, "messages": sorted(old)}
            note["messages"] = nodes
            if head:
                note["thread"] = conversation(note, head)
    return note != before


def last_answer(note):
    """The canonical thread's latest completed answer, including legacy turns."""
    nodes, identity = _message_history(note)
    by = {n["id"]: n for n in nodes}
    if "thread" not in note and not note.get("ask"):
        heads = branches(note)
        identity = heads[-1] if heads else None
    seen = set()
    while identity:
        if identity in seen or identity not in by:
            raise ValueError("Invalid conversation ancestry")
        seen.add(identity)
        node = by[identity]
        if node.get("role") == "assistant" and node.get("text") and not node.get("pending"):
            return identity
        identity = node.get("parent")
    return None


def split(manifest):
    """A record's identifier is local to a document, never a physical filename."""
    records = {"meta": copy.deepcopy(manifest["meta"])}
    for group in ("source", "processing"):
        for key, value in manifest[group].items():
            if key in ("originals", "replies"):
                for identity, item in value.items():
                    records[f"{group}.{key}/{identity}"] = copy.deepcopy(item)
            else:
                records[f"{group}/{key}"] = copy.deepcopy(value)
    for group in ("layers", "images", "extensions", "legacy"):
        for key, value in manifest[group].items():
            records[f"{group}/{key}"] = copy.deepcopy(value)
    records["previous"] = copy.deepcopy(manifest["previous"])
    if manifest["notes"] is not None:
        notes = manifest["notes"]
        records["notes"] = {k: copy.deepcopy(v) for k, v in notes.items() if k not in ("notes", "conflicts", "aliases")}
        for index, note in enumerate(notes.get("notes", [])):
            identity = note.get("id") or "legacy-" + digest([index, note])[:32]
            records["annotation/" + identity] = {k: copy.deepcopy(v) for k, v in note.items() if k not in ("thread", "ask", "messages", "branches")}
            nodes, head = _message_history({**note, "id": identity})
            if "thread" in note or note.get("ask"):
                records["annotation/" + identity]["thread_head"] = head
            for node in nodes:
                records[f"message/{identity}/{node['id']}"] = node
        for conflict in notes.get("conflicts", []):
            records["conflict/" + conflict["id"]] = copy.deepcopy(conflict)
        for alias, identity in notes.get("aliases", {}).items():
            records["alias/" + alias] = identity
    return records


def join(identity, records, created=0, updated=0):
    manifest = {"format": "textfold/document-v1", "id": identity, "meta": copy.deepcopy(records.get("meta")),
                "source": {"html": None, "markdown": None, "xml": None, "wiki_html": None, "pdf": None, "reference": None, "originals": {}},
                "processing": {"layout": None, "structured": None, "replies": {}},
                "layers": {}, "images": {}, "extensions": {}, "legacy": {}, "previous": copy.deepcopy(records.get("previous")),
                "notes": None, "created": created, "updated": updated}
    for key, value in records.items():
        if "/" not in key:
            continue
        group, name = key.split("/", 1)
        if group in ("source", "processing", "layers", "images", "extensions", "legacy"):
            manifest[group][name] = copy.deepcopy(value)
        elif group in ("source.originals", "processing.replies"):
            a, b = group.split(".")
            manifest[a][b][name] = copy.deepcopy(value)
    if "notes" in records or any(k.startswith(("annotation/", "conflict/")) for k in records):
        notes = {**copy.deepcopy(records.get("notes", {})), "notes": [], "conflicts": [], "aliases": {}}
        for key in sorted(records):
            if key.startswith("annotation/"):
                note = copy.deepcopy(records[key])
                identity = key.split("/", 1)[1]
                nodes = [copy.deepcopy(v) for k, v in sorted(records.items()) if k.startswith("message/" + identity + "/")]
                if nodes:
                    note["messages"] = nodes
                    note["branches"] = branches(note)
                    if "thread_head" in note:
                        note["thread"] = conversation(note, note["thread_head"]) if note["thread_head"] else []
                    else:
                        note["thread"] = conversation(note)
                notes["notes"].append(note)
            elif key.startswith("conflict/"):
                notes["conflicts"].append(copy.deepcopy(records[key]))
            elif key.startswith("alias/"):
                notes["aliases"][key.split("/", 1)[1]] = records[key]
        manifest["notes"] = notes
    return manifest


def original_unchanged(before, after):
    """Published source bytes can be filled in once, then never replaced."""
    if before.get("meta") is None:
        return True
    for key, value in before.items():
        if not (key.startswith("source.originals/") or key in ("source/pdf", "source/markdown", "source/xml", "source/wiki_html")):
            continue
        if value is not None and after.get(key) != value:
            return False
    # HTML is a representation of PDF/office originals, but the original for a
    # web-only import. Re-converting the same PDF may improve its representation.
    binary = before.get("source/pdf") is not None or any(k.startswith("source.originals/") for k in before)
    if not binary and before.get("source/html") is not None and after.get("source/html") != before["source/html"]:
        return False
    return True


_ABSENT = object()


def _conversation_head(key, left, right, versions):
    prefix = "message/" + key.split("/", 1)[1] + "/"
    nodes = {v["id"]: v for values in versions for k, v in values.items() if k.startswith(prefix)}
    def follows(child, ancestor):
        seen = set()
        while child and child not in seen:
            if child == ancestor: return True
            seen.add(child)
            child = nodes.get(child, {}).get("parent")
        return False
    if isinstance(left, str) and isinstance(right, str):
        if follows(left, right): return left
        if follows(right, left): return right
    return max((v for v in (left, right) if isinstance(v, str)), default=None)


def reconcile(base, local, remote):
    """Merge independent records/fields; retain ambiguous edits as revisions."""
    result = copy.deepcopy(remote)
    conflicts = []
    for key in sorted(base.keys() | local.keys() | remote.keys()):
        b, l, r = (mapping.get(key, _ABSENT) for mapping in (base, local, remote))
        if l == b or l == r:
            continue
        if r == b:
            if l is _ABSENT:
                result.pop(key, None)
            else:
                result[key] = copy.deepcopy(l)
            continue
        # Anchor selectors, binary references and generated layers are atomic.
        # Metadata/annotation fields can merge independently, timestamps cannot
        # manufacture a conflict. Same-field ambiguity retains both revisions.
        merged, ambiguous = copy.deepcopy(r) if r is not _ABSENT else None, []
        if key in ("catalog/folder_order", "zotero-order") and isinstance(l, list) and isinstance(r, list):
            common = b if isinstance(b, list) else []
            all_ids = set(l) | set(r)
            result[key] = [identity for identity in common if identity in all_ids] + sorted(all_ids - set(common))
            continue
        if key.startswith(("catalog.opened/", "catalog.added/")) and all(type(v) in (int, float) for v in (l, r)):
            result[key] = (max if key.startswith("catalog.opened/") else min)(l, r)
            continue  # display-only activity timestamps are not content revisions
        if key.startswith(("annotation/", "catalog.folders/")) and all(isinstance(v, dict) for v in (b, l, r)):
            merged = copy.deepcopy(r)
            for field in sorted(b.keys() | l.keys() | r.keys()):
                bv, lv, rv = (v.get(field, _ABSENT) for v in (b, l, r))
                if lv == bv or lv == rv:
                    continue
                if rv == bv or field in ("updated", "created"):
                    if lv is _ABSENT:
                        merged.pop(field, None)
                    else:
                        merged[field] = copy.deepcopy(lv)
                elif field == "thread_head":
                    merged[field] = _conversation_head(key, lv, rv, (base, local, remote))
                else:
                    ambiguous.append(field)
            result[key] = merged
            if not ambiguous:
                continue
        elif l is _ABSENT or r is _ABSENT:
            result.pop(key, None)  # deletion wins visibility, never erases edits
        elif key.startswith("message/"):
            # Message IDs are immutable except a pending generation completing.
            if r.get("pending") and not l.get("pending"):
                result[key] = copy.deepcopy(l)
                continue
            if l.get("pending") and not r.get("pending"):
                continue
        variants = [None if v is _ABSENT else copy.deepcopy(v) for v in (l, r)]
        variants.sort(key=canonical)
        conflict = {"key": key, "base": None if b is _ABSENT else copy.deepcopy(b), "variants": variants,
                    "deleted": l is _ABSENT or r is _ABSENT, "fields": ambiguous}
        conflict["id"] = "c-" + digest(conflict)[:32]
        result["conflict/" + conflict["id"]] = conflict
        conflicts.append(conflict["id"])
    # Deleting a conversation while another device appends a branch keeps the
    # deletion visible and retains its entire ancestry for an explicit restore.
    for key in sorted(k for k in base if k.startswith("annotation/")):
        prefix = "message/" + key.split("/", 1)[1] + "/"
        if key in local and key in remote:
            continue
        other = remote if key not in local else local
        if key not in other or not any(k.startswith(prefix) and v != base.get(k, _ABSENT) for k, v in other.items()):
            continue
        for mapping in (base, local, remote):
            for k, v in mapping.items():
                if k.startswith(prefix) and (k not in result or result[k].get("pending")):
                    result[k] = copy.deepcopy(v)
        variants = [None, copy.deepcopy(other[key])]
        conflict = {"key": key, "base": copy.deepcopy(base[key]), "variants": variants, "deleted": True, "fields": ["messages"]}
        conflict["id"] = "c-" + digest(conflict)[:32]
        result["conflict/" + conflict["id"]] = conflict
        conflicts.append(conflict["id"])
    # Exact duplicate highlights share a target, not the identity of user notes.
    seen = {}
    for key in sorted(list(result)):
        if not key.startswith("annotation/"):
            continue
        value = result[key]
        if value.get("text") or value.get("kind") == "ask" or not value.get("anchor"):
            continue
        if any(k.startswith("message/" + key.split("/", 1)[1] + "/") for k in result):
            continue
        signature = digest({k: v for k, v in value.items() if k not in ("id", "created", "updated")})
        if signature in seen:
            alias, kept = key.split("/", 1)[1], seen[signature].split("/", 1)[1]
            result["alias/" + alias] = kept
            del result[key]
        else:
            seen[signature] = key
    # A document snapshot must carry hidden conversation ancestry too. The
    # fingerprint lets a later delete decision notice newly appended branches.
    retained = []
    for identity in conflicts:
        conflict = result.pop("conflict/" + identity)
        if conflict["key"].startswith("annotation/"):
            nodes = message_nodes(result, conflict["key"])
            conflict["message_revision"] = digest(nodes)
            if conflict["deleted"]:
                conflict["messages"] = nodes
            conflict.pop("id")
            conflict["id"] = "c-" + digest(conflict)[:32]
        result["conflict/" + conflict["id"]] = conflict
        retained.append(conflict["id"])
    return result, retained


def changes(before, after):
    return [{"key": key, "deleted": key not in after, "value": after.get(key)}
            for key in sorted(before.keys() | after.keys()) if before.get(key, _ABSENT) != after.get(key, _ABSENT)]


def message_nodes(values, key):
    prefix = "message/" + key.split("/", 1)[1] + "/"
    return [copy.deepcopy(v) for k, v in sorted(values.items()) if k.startswith(prefix)]


def merge_message_nodes(older, newer):
    nodes = {n["id"]: copy.deepcopy(n) for n in older}
    for node in newer:
        previous = nodes.get(node["id"])
        if previous is None or previous.get("pending") or not node.get("pending"):
            nodes[node["id"]] = copy.deepcopy(node)
    return [nodes[k] for k in sorted(nodes)]


def resolved_value(current, conflict, choice, text=None):
    """Choose only ambiguous fields, retaining already-merged independent edits."""
    if type(choice) is not int or choice not in (0, 1):
        raise ValueError("Choose one of the retained revisions")
    value = copy.deepcopy(conflict["variants"][choice])
    if conflict["key"].startswith("annotation/") and isinstance(current, dict) and isinstance(value, dict) and conflict.get("fields"):
        selected, value = value, copy.deepcopy(current)
        for field in conflict["fields"]:
            if field in selected:
                value[field] = selected[field]
            else:
                value.pop(field, None)
    if text is not None:
        if not isinstance(text, str) or not isinstance(value, dict) or not conflict["key"].startswith("annotation/"):
            raise ValueError("Manual text requires an annotation revision")
        value["text"] = text
    return value


def resolve_conflict(values, identity, choice, text=None):
    """Resolve a reviewed pair without discarding a subsequently saved revision."""
    result = copy.deepcopy(values)
    conflict = result["conflict/" + identity]
    key = conflict["key"]
    current = result.get(key)
    value = resolved_value(current, conflict, choice, text)
    fields = conflict.get("fields", [])

    def same(left, right):
        if value is not None and fields and isinstance(left, dict) and isinstance(right, dict):
            return all(left.get(f, _ABSENT) == right.get(f, _ABSENT) for f in fields)
        return left == right

    # Independent fields have already merged; only a newer ambiguous value
    # requires another decision. Retain it even when a manual text was supplied.
    newer = not any(same(current, v) for v in conflict["variants"]) and not same(current, value)
    prefix = "message/" + key.split("/", 1)[1] + "/" if key.startswith("annotation/") else None
    # Local snapshots keep hidden nodes in the archive; SQLite also has live
    # orphan records. Both represent the same tree, rather than a new edit.
    messages = merge_message_nodes(conflict.get("messages", []), message_nodes(result, key)) if prefix else []
    if prefix and value is None:
        reviewed = conflict.get("message_revision")
        if reviewed is None and "messages" in conflict:
            reviewed = digest(sorted(conflict["messages"], key=lambda n: n["id"]))
        newer = newer or (bool(messages) if reviewed is None else digest(messages) != reviewed)
    if value is None:
        result.pop(key, None)
        if prefix:
            result = {k: v for k, v in result.items() if not k.startswith(prefix)}
    else:
        result[key] = value
        if prefix:
            for node in messages:
                result[prefix + node["id"]] = node
    result.pop("conflict/" + identity)
    if newer:
        # New hidden nodes can arrive after the annotation was deleted. Keep a
        # real annotation candidate so their archived ancestry can be restored.
        candidate = current
        if prefix and current is None and value is None:
            candidate = next((v for v in conflict["variants"] if v is not None), None)
        variants = sorted([copy.deepcopy(candidate), copy.deepcopy(value)], key=canonical)
        retained = {"key": key, "base": conflict.get("base"), "variants": variants,
                    "deleted": current is None or value is None, "fields": fields}
        if prefix and retained["deleted"]:
            retained["messages"] = messages
        if prefix:
            retained["message_revision"] = digest(merge_message_nodes(retained.get("messages", []), message_nodes(result, key)))
        retained["id"] = "c-" + digest(retained)[:32]
        result["conflict/" + retained["id"]] = retained
    return result


def library_records(state):
    result = {}
    for key, value in state.catalog.items():
        if key == "sync_conflicts":
            for conflict in value:
                result["conflict/" + conflict["id"]] = copy.deepcopy(conflict)
            continue
        if key == "folders" and isinstance(value, list):
            result["catalog/folder_order"] = [folder["id"] for folder in value]
            if not value:
                result["catalog/folders"] = []
            for folder in value:
                result["catalog.folders/" + folder["id"]] = copy.deepcopy(folder)
        elif isinstance(value, dict):
            if not value:
                result["catalog/" + key] = {}
            for identity, item in value.items():
                result[f"catalog.{key}/{identity}"] = copy.deepcopy(item)
        else:
            result["catalog/" + key] = copy.deepcopy(value)
    result.update({"zotero/" + key: copy.deepcopy(value) for key, value in state.zotero.items()})
    if state.zotero:
        result["zotero-order"] = list(state.zotero)
    return result


def library_value(values, revision=None):
    catalog, zotero = {}, {}
    order = values.get("catalog/folder_order", [])
    for key, value in sorted(values.items(), key=lambda item: (not item[0].startswith("catalog/"), item[0])):
        if key.startswith("catalog.folders/"):
            catalog.setdefault("folders", []).append(copy.deepcopy(value))
        elif key.startswith("catalog."):
            group, identity = key[8:].split("/", 1)
            catalog.setdefault(group, {})[identity] = copy.deepcopy(value)
        elif key.startswith("catalog/"):
            if key != "catalog/folder_order":
                catalog[key[8:]] = copy.deepcopy(value)
        elif key.startswith("zotero/"):
            zotero[key[7:]] = copy.deepcopy(value)
        elif key.startswith("conflict/"):
            catalog.setdefault("sync_conflicts", []).append(copy.deepcopy(value))
    if "folders" in catalog:
        positions = {identity: i for i, identity in enumerate(order)}
        catalog["folders"].sort(key=lambda folder: (positions.get(folder["id"], len(order)), folder["id"]))
    ordering = values.get("zotero-order", [])
    positions = {identity: i for i, identity in enumerate(ordering)}
    zotero = dict(sorted(zotero.items(), key=lambda item: (positions.get(item[0], len(ordering)), item[0])))
    return LibraryState(catalog, zotero, revision)


def note_records(identity, notes):
    """Split notes without depending on a transport or its blob codec."""
    return split({"meta": None, "source": {}, "processing": {}, "layers": {}, "images": {},
                  "extensions": {}, "legacy": {}, "previous": None, "notes": notes})
