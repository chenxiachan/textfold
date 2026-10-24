"""A paragraph's and a section's id, kept for the life of a document, so that what names one (a textfold:// link, a
note, a citation in an answer) still finds it when the text is edited, converted again, or read by a later TextFold.

- Frozen: when a text is stored (local.store_doc) or replaced (local.py, pdfdoc.py), the id parse.py gives each
  paragraph is written into source.html on the element it was read from (data-u: one id for each part a long paragraph
  is cut into), and each section's on its element (data-s); parse.py takes them from there. A document stored before
  this keeps the ids it is read with today.
- Carried: a text replaced or converted again keeps the ids of its paragraphs and sections that are still there, edited
  or moved, found as notes.reanchor finds them (notes.Moves). A paragraph new in it gets an id that no paragraph of the
  document had before, so an old link never lands on another paragraph (a PDF's paragraphs are named by their places:
  a new conversion would otherwise give u6 to the paragraph that was u7).
- Remembered: a paragraph or section that is gone keeps its id in papers/<id>/ids.json, with where it was: the section
  it was in, followed through later replaces, so that a link to it opens there. Its id is not given to another.
"""
import json
import re
import threading

from bs4 import BeautifulSoup

from . import notes, store
from .parse import parse
from .tokens import plain

FILE = "ids.json"
FORMAT, VERSION = "textfold/ids", 1
_LOCK = threading.Lock()
STAMP = "data-tf-k"   # (while ids are found: which element is which; never stored)


def load(pid):
    """A document's paragraphs and sections that are gone, by their ids: {"units": {id: {"section", "since"}},
    "sections": {id: {"now", "since"}}}, "now" the section its paragraphs went to (or None)."""
    try:
        data = json.loads(store.text(pid, FILE))
    except FileNotFoundError:
        data = {}
    return {"units": data.get("units") or {}, "sections": data.get("sections") or {}}


def freeze(html, meta, old=None, gone=None):
    """A document's normalized HTML (semantic.normalize) with its paragraphs' and sections' ids written in, and the
    document as parse.py reads it from there. old: the document it replaces (parsed), whose paragraphs and sections
    still there keep their ids; gone: the document's ids that are gone (load), not given to another."""
    soup = BeautifulSoup(html, "lxml")
    art = soup.select_one("article.ltx_document")
    if art is None:
        return html, parse(html, meta)
    marked = {}
    for k, el in enumerate(art.find_all(("p", "figcaption", "section"))):
        el[STAMP] = str(k)
        marked[str(k)] = el
    made = {}
    doc = parse(str(art), meta, made=made)
    units, chunks = carry(old, doc, gone or {"units": {}, "sections": {}}) if old else ({}, {})
    for k, el in marked.items():
        del el[STAMP]
        if k in made["units"]:
            el["data-u"] = " ".join(units.get(u, u) for u in made["units"][k])
        if k in made["chunks"]:
            el["data-s"] = chunks.get(made["chunks"][k], made["chunks"][k])
    out = str(art)
    return out, parse(out, meta)


def carry(old, new, gone):
    """The names the new text's paragraphs and sections take ({id: id}, for those that change): each one still there,
    edited, moved or not, its old id; one new in it whose id an old one had, or one that is gone, an id not used before."""
    moves = notes.Moves(old, new)
    units = {b: a for a, b in {**moves.near, **moves.same}.items()}
    # a paragraph moved elsewhere (another section) word for word: the alignment in reading order does not pair it
    left = {}
    for uid in notes._order(old):
        if uid not in units.values():
            left.setdefault(plain(old["units"][uid]["toks"], old["atoms"]), []).append(uid)
    for uid in notes._order(new):
        if uid not in units and (was := left.get(plain(new["units"][uid]["toks"], new["atoms"]))):
            units[uid] = was.pop(0)
    used = set(old["units"]) | set(gone["units"]) | set(new["units"])
    by_text = new["meta"].get("unit_ids") == "text"
    for uid in new["units"]:
        back = by_text and uid.startswith("p-")   # (named by its words: a paragraph gone before, back word for word)
        if uid not in units and (uid in old["units"] or uid in gone["units"] and not back):
            units[uid] = _fresh(uid, used)
            used.add(units[uid])
    # a section takes the id of the old one most of its paragraphs come from (one old section to one new, the same
    # title first on a tie); one with no paragraph from before keeps its own, unless an old section had it
    titles = {c["id"]: c.get("title") for c in old["chunks"]}
    votes = []
    for c in new["chunks"]:
        n = {}
        for uid in c["units"]:
            sid = old["units"].get(units.get(uid), {}).get("chunk")
            if sid:
                n[sid] = n.get(sid, 0) + 1
        votes += [(-k, titles.get(sid) != c.get("title"), c["id"], sid) for sid, k in n.items()]
    chunks, taken = {}, set()
    for *_, cid, sid in sorted(votes):
        if cid not in chunks and sid not in taken:
            chunks[cid] = sid
            taken.add(sid)
    sused = set(titles) | set(gone["sections"]) | {c["id"] for c in new["chunks"]}
    for c in new["chunks"]:
        cid = c["id"]
        if cid in chunks:
            continue
        if cid in taken or cid in gone["sections"]:
            chunks[cid] = _fresh(cid, sused)
            sused.add(chunks[cid])
        else:
            taken.add(cid)
    return {a: b for a, b in units.items() if a != b}, {a: b for a, b in chunks.items() if a != b}


def _fresh(uid, used):
    """An id like uid that is not in used: the next number for one named by its place (u12 -> u40, c3 -> c9), else a
    count (-2, -3, …). The part of a cut paragraph keeps its mark (u6~2 -> u40~2)."""
    base, _, part = uid.partition("~")
    part = f"~{part}" if part else ""
    m = re.fullmatch(r"([A-Za-z]+)(\d+)", base)
    if m:
        head = m.group(1)
        nums = [int(x.group(1)) for u in used if (x := re.fullmatch(re.escape(head) + r"(\d+)", u.partition("~")[0]))]
        n = max(nums, default=0) + 1
        while f"{head}{n}{part}" in used:
            n += 1
        return f"{head}{n}{part}"
    n = 2
    while f"{base}-{n}{part}" in used:
        n += 1
    return f"{base}-{n}{part}"


def remember(pid, old, new):
    """After a text was replaced or converted again (old, new: the documents, parsed, new with the ids carried): its
    paragraphs and sections that are gone, kept with where they went; those gone before follow their sections."""
    with _LOCK:
        moves = notes.Moves(old, new)
        data = load(pid)
        here = {c["id"] for c in new["chunks"]}
        when = store.stamp()
        for c in old["chunks"]:
            if c["id"] not in here:
                data["sections"][c["id"]] = {"now": moves.section(c["id"], c["units"]), "since": when}

        def follow(sid):   # (a section gone now: where its paragraphs went, a section of the new text)
            return sid if sid in here else (data["sections"].get(sid) or {}).get("now")

        for x in data["sections"].values():
            x["now"] = follow(x.get("now"))
        for uid, u in old["units"].items():
            if uid not in new["units"]:
                data["units"][uid] = {"section": moves.section(u.get("chunk"), [uid]), "since": when}
        for x in data["units"].values():
            x["section"] = follow(x.get("section"))
        for uid in new["units"]:   # (back again)
            data["units"].pop(uid, None)
        for sid in here:
            data["sections"].pop(sid, None)
        if data["units"] or data["sections"] or store.has(pid, FILE):
            store.put_asset(pid, FILE, json.dumps({"format": FORMAT, "version": VERSION, **data}, indent=1, ensure_ascii=False).encode("utf-8"))
        return data


def lookup(pid, doc, units=(), sections=()):
    """What each id names now (GET /api/ids): {"units": {id: …}, "sections": {id: …}}, each a paragraph or section still
    there ({"here": true}); a section whose paragraphs went to another ({"now": id}); one that is gone ({"gone": true},
    a paragraph with the section it was in, or null); or an id the document has no record of ({"unknown": true})."""
    gone, here = load(pid), {c["id"] for c in doc["chunks"]}
    out = {"units": {}, "sections": {}}
    for uid in units:
        if uid in doc["units"] or uid in doc.get("alias", {}):
            out["units"][uid] = {"here": True}
        elif uid in gone["units"]:
            sid = gone["units"][uid].get("section")
            out["units"][uid] = {"gone": True, "section": sid if sid in here else None}
        else:
            out["units"][uid] = {"unknown": True}
    for sid in sections:
        if sid in here:
            out["sections"][sid] = {"here": True}
        elif sid in gone["sections"]:
            now = gone["sections"][sid].get("now")
            out["sections"][sid] = {"now": now} if now in here else {"gone": True}
        else:
            out["sections"][sid] = {"unknown": True}
    return out
