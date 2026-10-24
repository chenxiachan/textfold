"""The reader's own order in the library, as the sidebar shows it: folders, the names they gave documents, and when
each document came in and when it was last opened. Kept in library.json in the data folder, beside papers/, so that the
app and `textfold serve` show the same and a copy of the library carries it.

Folders: inside one another as deep as the reader makes them (a folder's parent; none: at the top), a document in one
at most (dragged into another, it moves). A document in no folder stays under its kind, or under when it came in or was
opened. Deleting a folder moves what is in it, documents and folders, up into its parent.

A name: what the reader calls a document in the library (a PDF whose title came out wrong, a file named by its
date), shown instead of its title; the title stays the document's own, and the name goes when it is cleared.

When a document came in is noted the first time the list shows it, from the oldest file in its folder (so one read
before this was kept is dated as well); when it was opened, each time the reader shows it.

    {"folders": [{"id": "f-3a9c01d2", "name": "Thesis"}, {"id": "f-77b0e415", "name": "Chapter 2", "parent": "f-3a9c01d2"}],
     "of": {"2512.24601": "f-77b0e415"}, "names": {"pdf-0123456789abcdef": "Smith 2024 review"},
     "added": {"2512.24601": 1791000000.0}, "opened": {"2512.24601": 1792000000.0}}
"""
import json
import copy
import re
import secrets
import threading
import time

from . import store
from .documents import ConflictError, LibraryState
from . import sync_records

_lock = threading.Lock()


def path():
    return store.DATA / "library.json"


def _times(d, key):
    v = d.get(key) if isinstance(d.get(key), dict) else {}
    return {p: float(t) for p, t in v.items() if store.valid_id(p) and isinstance(t, (int, float))}


class _Catalog(dict):
    pass  # merge base lives on the object, never in exported JSON


def load():
    try:
        state = store.repository().library()
        d = state.catalog
    except (OSError, ValueError):
        state, d = LibraryState(), {}
    folders = [{"id": f["id"], "name": f["name"], **({"parent": f["parent"]} if isinstance(f.get("parent"), str) else {})}
               for f in d.get("folders") or []
               if isinstance(f, dict) and isinstance(f.get("id"), str) and isinstance(f.get("name"), str)]
    ids = {f["id"] for f in folders}
    by = {f["id"]: f for f in folders}
    for f in folders:   # a parent that is gone, or a folder under itself (a loop, edited by hand): at the top
        seen, p = {f["id"]}, f.get("parent")
        while p in by and p not in seen:
            seen.add(p)
            p = by[p].get("parent")
        if f.get("parent") not in ids or p in seen:
            f.pop("parent", None)
    of = d.get("of") if isinstance(d.get("of"), dict) else {}
    names = d.get("names") if isinstance(d.get("names"), dict) else {}
    result = _Catalog({"folders": folders, "of": {p: f for p, f in of.items() if f in ids and store.valid_id(p)},
            "names": {p: n for p, n in names.items() if store.valid_id(p) and isinstance(n, str) and n.strip()},
            "added": _times(d, "added"), "opened": _times(d, "opened")})
    result.state = copy.deepcopy(state)
    return result


def _save(d):
    base = getattr(d, "state", None) or store.repository().library()
    local = copy.deepcopy(base)
    local.catalog.update(d)
    commit(base, local)


def commit(base, local):
    """Keep a UI operation's actual base across concurrent sync/organization edits."""
    for attempt in range(4):
        current = store.repository().library()
        merged, _ = sync_records.reconcile(sync_records.library_records(base), sync_records.library_records(local), sync_records.library_records(current))
        state = sync_records.library_value(merged, current.revision)
        try:
            store.repository().save_library(state)
            return state
        except ConflictError:
            if attempt == 3:
                raise


def folders():
    """The folders, in the order they were made, and which folder each document is in (none: not listed)."""
    d = load()
    return {"folders": d["folders"], "of": d["of"]}


def _name(s):
    s = re.sub(r"\s+", " ", str(s or "")).strip()[:80]
    if not s:
        raise ValueError("a folder needs a name")
    return s


def _under(d, fid):
    """The folders inside folder fid, however deep, fid among them."""
    out, more = {fid}, True
    while more:
        more = False
        for f in d["folders"]:
            if f.get("parent") in out and f["id"] not in out:
                out.add(f["id"])
                more = True
    return out


def change(body):
    """One change to the folders, the folders after it: {op: "new", name, parent?: id (inside it), papers?: [ids] moved
    into it} · {op: "rename", id, name} · {op: "delete", id} (what is in it moves up into its parent) · {op: "nest", id,
    parent: id or null (to the top)} · {op: "move", papers: [ids], folder: id or null (out of any)}.
    ValueError for one that cannot be made."""
    op = body.get("op")
    with _lock:
        d = load()
        ids = {f["id"] for f in d["folders"]}
        if op == "new":
            parent = body.get("parent") or None
            if parent is not None and parent not in ids:
                raise ValueError("no such folder")
            f = {"id": f"f-{secrets.token_hex(4)}", "name": _name(body.get("name")), **({"parent": parent} if parent else {})}
            pids = _papers(body.get("papers", []))
            d["folders"].append(f)
            d["of"].update(dict.fromkeys(pids, f["id"]))
        elif op in ("rename", "delete", "nest"):
            f = next((f for f in d["folders"] if f["id"] == body.get("id")), None)
            if f is None:
                raise ValueError("no such folder")
            if op == "rename":
                f["name"] = _name(body.get("name"))
            elif op == "nest":
                to = body.get("parent") or None
                if to is not None and to not in ids:
                    raise ValueError("no such folder")
                if to in _under(d, f["id"]):
                    raise ValueError("a folder cannot go inside itself")
                if to:
                    f["parent"] = to
                else:
                    f.pop("parent", None)
            else:
                up = f.get("parent")
                d["folders"].remove(f)
                for g in d["folders"]:
                    if g.get("parent") == f["id"]:
                        if up:
                            g["parent"] = up
                        else:
                            g.pop("parent", None)
                d["of"] = {p: (up if x == f["id"] else x) for p, x in d["of"].items() if x != f["id"] or up}
        elif op == "move":
            to = body.get("folder") or None
            if to is not None and to not in ids:
                raise ValueError("no such folder")
            for p in _papers(body.get("papers")):
                if to:
                    d["of"][p] = to
                else:
                    d["of"].pop(p, None)
        else:
            raise ValueError("op: new, rename, delete, nest or move")
        _save(d)
        return {"folders": d["folders"], "of": d["of"]}


def name(pid, value):
    """What the reader calls a document in the library: a name ("" or None: its own title again). The names after it."""
    if not store.valid_id(pid):
        raise ValueError("no such document")
    with _lock:
        d = load()
        v = re.sub(r"\s+", " ", str(value or "")).strip()[:200]
        if v:
            d["names"][pid] = v
        else:
            d["names"].pop(pid, None)
        _save(d)
        return d["names"]


def name_of(pid):
    return load()["names"].get(pid)


def _papers(pids):
    if not isinstance(pids, list) or not all(isinstance(p, str) and store.valid_id(p) for p in pids):
        raise ValueError("papers: a list of document ids")
    return pids


def stamp(papers, index=None):
    """The documents of the list (store.papers), each with when it came in ("added") and when it was last opened
    ("opened", or None), and the name the reader gave it, if any; one listed for the first time is dated here and kept."""
    with _lock:
        d = load()
        missing = [p["id"] for p in papers if p["id"] not in d["added"]]
        created = {row.id: row.created for row in (store.repository().list() if index is None else index)} if missing else {}
        new = {pid: created.get(pid, time.time()) for pid in missing}
        if new:
            d["added"].update(new)
            _save(d)
    return [{**p, "added": d["added"][p["id"]], "opened": d["opened"].get(p["id"]),
             **({"name": d["names"][p["id"]]} if p["id"] in d["names"] else {})} for p in papers]


def opened(pid):
    """The reader shows a document: when."""
    if not store.valid_id(pid):
        return
    with _lock:
        d = load()
        d["opened"][pid] = time.time()
        _save(d)


def forget(pid):
    """A document deleted from the library: out of its folder, its dates and name gone (read again, it comes in anew).
    What it had, to remember it by: {"of": folder, "names": name, "added": time, "opened": time}, those it had."""
    with _lock:
        d = load()
        gone = {k: d[k].pop(pid) for k in ("of", "names", "added", "opened") if pid in d[k]}
        if gone:
            _save(d)
        return gone


def remember(pid, kept):
    """A deleted document back (its Undo): in its folder if that is still there, with its name and dates."""
    if not store.valid_id(pid) or not isinstance(kept, dict):
        return
    with _lock:
        d = load()
        ids = {f["id"] for f in d["folders"]}
        for k, ok in (("of", lambda v: v in ids), ("names", lambda v: isinstance(v, str) and v.strip()),
                      ("added", lambda v: isinstance(v, (int, float))), ("opened", lambda v: isinstance(v, (int, float)))):
            if k in kept and ok(kept[k]):
                d[k][pid] = kept[k]
        _save(d)
