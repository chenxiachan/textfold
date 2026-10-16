"""The reader's own order in the library, as the sidebar shows it: folders, and when each document came in and when it
was last opened. Kept in library.json in the data folder, beside papers/, so that the app and `textfold serve` show the
same and a copy of the library carries it.

Folders: one level, a document in one at most (dragged into another, it moves). A document in no folder stays under
its kind, or under when it came in or was opened; deleting a folder leaves its documents there.

When a document came in is noted the first time the list shows it, from the oldest file in its folder (so one read
before this was kept is dated as well); when it was opened, each time the reader shows it.

    {"folders": [{"id": "f-3a9c01d2", "name": "Thesis"}], "of": {"2512.24601": "f-3a9c01d2"},
     "added": {"2512.24601": 1791000000.0}, "opened": {"2512.24601": 1792000000.0}}
"""
import json
import re
import secrets
import threading
import time

from . import store

_lock = threading.Lock()


def path():
    return store.DATA / "library.json"


def _times(d, key):
    v = d.get(key) if isinstance(d.get(key), dict) else {}
    return {p: float(t) for p, t in v.items() if store.valid_id(p) and isinstance(t, (int, float))}


def load():
    try:
        d = json.loads(path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    folders = [{"id": f["id"], "name": f["name"]} for f in d.get("folders") or []
               if isinstance(f, dict) and isinstance(f.get("id"), str) and isinstance(f.get("name"), str)]
    ids = {f["id"] for f in folders}
    of = d.get("of") if isinstance(d.get("of"), dict) else {}
    return {"folders": folders, "of": {p: f for p, f in of.items() if f in ids and store.valid_id(p)},
            "added": _times(d, "added"), "opened": _times(d, "opened")}


def _save(d):
    f = path()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def folders():
    """The folders, in the order they were made, and which folder each document is in (none: not listed)."""
    d = load()
    return {"folders": d["folders"], "of": d["of"]}


def _name(s):
    s = re.sub(r"\s+", " ", str(s or "")).strip()[:80]
    if not s:
        raise ValueError("a folder needs a name")
    return s


def change(body):
    """One change to the folders, the folders after it: {op: "new", name, papers?: [ids] moved into it} ·
    {op: "rename", id, name} · {op: "delete", id} · {op: "move", papers: [ids], folder: id or null (out of any)}.
    ValueError for one that cannot be made."""
    op = body.get("op")
    with _lock:
        d = load()
        ids = {f["id"] for f in d["folders"]}
        if op == "new":
            f = {"id": f"f-{secrets.token_hex(4)}", "name": _name(body.get("name"))}
            pids = _papers(body.get("papers", []))
            d["folders"].append(f)
            d["of"].update(dict.fromkeys(pids, f["id"]))
        elif op in ("rename", "delete"):
            f = next((f for f in d["folders"] if f["id"] == body.get("id")), None)
            if f is None:
                raise ValueError("no such folder")
            if op == "rename":
                f["name"] = _name(body.get("name"))
            else:
                d["folders"].remove(f)
                d["of"] = {p: x for p, x in d["of"].items() if x != f["id"]}
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
            raise ValueError("op: new, rename, delete or move")
        _save(d)
        return {"folders": d["folders"], "of": d["of"]}


def _papers(pids):
    if not isinstance(pids, list) or not all(isinstance(p, str) and store.valid_id(p) for p in pids):
        raise ValueError("papers: a list of document ids")
    return pids


def _came_in(pid):
    """When a document came in, from its folder: its oldest file, or the folder itself when that is older."""
    d = store.pdir(pid)
    st = d.stat()
    times = [f.stat().st_mtime for f in d.rglob("*") if f.is_file()]
    return min(times + [getattr(st, "st_birthtime", st.st_mtime)])


def stamp(papers):
    """The documents of the list (store.papers), each with when it came in ("added") and when it was last opened
    ("opened", or None); one listed for the first time is dated here and kept."""
    with _lock:
        d = load()
        new = {p["id"]: _came_in(p["id"]) for p in papers if p["id"] not in d["added"]}
        if new:
            d["added"].update(new)
            _save(d)
    return [{**p, "added": d["added"][p["id"]], "opened": d["opened"].get(p["id"])} for p in papers]


def opened(pid):
    """The reader shows a document: when."""
    if not store.valid_id(pid):
        return
    with _lock:
        d = load()
        d["opened"][pid] = time.time()
        _save(d)


def forget(pid):
    """A document deleted from the library: out of its folder, its dates gone (read again, it comes in anew)."""
    with _lock:
        d = load()
        gone = [d[k].pop(pid, None) is not None for k in ("of", "added", "opened")]
        if any(gone):
            _save(d)
