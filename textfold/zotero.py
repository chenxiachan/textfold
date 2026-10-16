"""A Zotero library, browsed and read, through Zotero Desktop's local API on this computer.

  http://localhost:23119/api/   the web API's routes without a key: users/0/ is the reader's own library, groups/<id>/
                                a group's. Zotero 7 serves it once "Allow other applications on this computer to
                                communicate with Zotero" is on (Settings › Advanced). Read only: nothing here changes
                                the library, and nothing goes to Zotero's servers.

An item is read by the best text it has: its arXiv paper when it names one (arXiv's HTML, as any arXiv link); else its
file (a PDF, an e-book, a saved web page, a Word file), taken as a file dropped on the landing is (server.take_file:
the paper online when its DOI names one, else the PDF engine, else TextFold's own reading), so it is kept beside the
paper as any original is; else its DOI, in Europe PMC. zotero.json, in the data folder, says which item became which
document, so the library marks what is open, and keeps the item's record: a document read from the item's own file
takes its title, authors, date, DOI and citekey from Zotero (apply), which knows them better than the file does.

The library chosen is the reader's settings (~/.textfold/settings.json, "zotero": {"library"}). So are, for a Zotero
elsewhere, its address and its data folder ("base", "data_dir"), which only that file sets: no page may point the
server at another address, or at another folder to read files from.
"""
import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import engine, pdf, store
from .fetch import UA

ZOTERO = "http://localhost:23119"
KEY = re.compile(r"[23456789ABCDEFGHIJKLMNPQRSTUVWXYZ]{8}")   # Zotero's keys, of items and collections
LIB = re.compile(r"users/0|groups/\d{1,10}")
ARXIV = re.compile(r"(?:arxiv\.org/(?:abs|pdf|html)/|arXiv:\s*|10\.48550/arXiv\.)(\d{4}\.\d{4,5})", re.I)
CITEKEY = re.compile(r"^\s*Citation Key:\s*(\S+)", re.M | re.I)
SKIP_TYPES = {"attachment", "note", "annotation"}
# the files of an item TextFold reads, best first, by the type Zotero gives them, and their extension when the file's
# own name has none
READS = {"application/pdf": "pdf", "application/epub+zip": "epub",
         "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
         "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
         "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
         "application/vnd.oasis.opendocument.text": "odt", "application/rtf": "rtf", "application/msword": "doc",
         "text/html": "html", "application/xhtml+xml": "html"}
LABEL = {"pdf": "PDF", "epub": "EPUB", "html": "HTML", "docx": "Word", "doc": "Word", "pptx": "PowerPoint",
         "xlsx": "Excel", "odt": "ODT", "rtf": "RTF"}
_lock = threading.Lock()


class Off(RuntimeError):
    """Zotero is not running ("not-running"), or its local API is off ("api-off"): what the app says, by name."""


def settings():
    from .models import load_settings
    z = load_settings().get("zotero") or {}
    lib = z.get("library") or ""
    return {"library": lib if LIB.fullmatch(lib) else "users/0", "base": (z.get("base") or ZOTERO).rstrip("/"),
            "data_dir": z.get("data_dir") or str(Path.home() / "Zotero")}


def choose(lib):
    """The library the reader browses, kept for next time: the one setting a page may change."""
    _check(lib)
    from .models import load_settings, save_settings
    s = load_settings()
    s["zotero"] = {**(s.get("zotero") or {}), "library": lib}
    save_settings(s)


# ── the local API ─────────────────────────────────────────────────────

def _get(path, params=None, raw=False, timeout=20):
    """One request to Zotero's local API: (body, headers). Off when Zotero does not answer or its API is off."""
    url = f"{settings()['base']}/api/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={**UA, "Zotero-API-Version": "3"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
            return (body if raw else json.loads(body or b"null")), r.headers
    except urllib.error.HTTPError as e:
        if e.code == 403:   # "Local API is not enabled"
            raise Off("api-off")
        raise
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        raise Off("not-running")


def _all(path, params=None):
    """Every row of a listing: the API gives at most 100 a request."""
    rows, start = [], 0
    while True:
        page, headers = _get(path, {**(params or {}), "limit": 100, "start": start})
        rows += page or []
        total = int(headers.get("Total-Results") or len(rows))
        start += 100
        if not page or start >= total:
            return rows


def status():
    """Whether Zotero answers, and the libraries it holds: the reader's own (named by the page, in its language), then
    each group."""
    lib = settings()["library"]
    try:
        groups, _ = _get("users/0/groups", {"limit": 100})
    except Off as e:
        return {"ok": False, "error": str(e), "library": lib, "libraries": []}
    libs = [{"id": "users/0", "name": ""}] + [
        {"id": f"groups/{g['id']}", "name": g["data"]["name"], "items": g.get("meta", {}).get("numItems")} for g in groups or []]
    if lib not in {x["id"] for x in libs}:   # a group left since
        lib = "users/0"
    return {"ok": True, "library": lib, "libraries": libs}


def collections(lib):
    """The library's collections as a flat list, each with its parent ("" at the top), sorted by name."""
    _check(lib)
    out = [{"key": c["key"], "name": c["data"]["name"], "parent": c["data"].get("parentCollection") or "",
            "items": c.get("meta", {}).get("numItems", 0)} for c in _all(f"{lib}/collections")]
    return sorted((c for c in out if KEY.fullmatch(c["key"])), key=lambda c: c["name"].lower())


def items(lib, collection="", q=""):
    """The items of a collection, or of the whole library (none chosen, or a search), each as the library shows it,
    with the document it became when that is still here."""
    _check(lib)
    if collection and not KEY.fullmatch(collection):
        raise ValueError("bad-collection")
    params = {"q": q, "qmode": "titleCreatorYear"} if q else {}
    path = f"{lib}/collections/{collection}/items/top" if collection and not q else f"{lib}/items/top"
    opened = links()
    out = []
    for r in _all(path, params):
        it = item_of(r)
        if it["type"] in SKIP_TYPES or not KEY.fullmatch(it["key"]):
            continue
        pid = opened.get(f"{lib}/{it['key']}", "")
        it["paper"] = pid if pid and store.valid_id(pid) and (store.pdir(pid) / "meta.json").exists() else ""
        out.append(it)
    return out


def item_of(r):
    """An item of the API, as the library shows it."""
    d, m = r["data"], r.get("meta", {})
    att = (r.get("links") or {}).get("attachment") or {}   # the item's best attachment, as Zotero picks it
    authors = [c.get("lastName") or c.get("name") or "" for c in d.get("creators", []) if c.get("creatorType") in ("author", "editor", None)]
    ck = CITEKEY.search(d.get("extra") or "")
    return {"key": r["key"], "type": d.get("itemType", ""), "title": d.get("title") or "(untitled)",
            "authors": [a for a in authors if a], "year": (m.get("parsedDate") or d.get("date") or "")[:4],
            "doi": d.get("DOI") or "", "arxiv": arxiv_of(d), "citekey": d.get("citationKey") or (ck.group(1) if ck else ""),
            "file": LABEL.get(READS.get(att.get("attachmentType") or ""), ""), "children": m.get("numChildren", 0)}


def arxiv_of(d):
    for v in (d.get("url"), d.get("extra"), d.get("archiveID"), d.get("DOI"), d.get("publicationTitle")):
        m = ARXIV.search(v or "")
        if m:
            return m.group(1)
    if (d.get("repository") or d.get("libraryCatalog") or "").lower() == "arxiv":
        m = re.search(r"\d{4}\.\d{4,5}", d.get("number") or d.get("archiveID") or "")
        if m:
            return m.group(0)
    return ""


def _check(lib):
    if not LIB.fullmatch(lib or ""):
        raise ValueError("bad-library")


# ── an item, read ─────────────────────────────────────────────────────

def read(lib, key):
    """What an item is read from: {"ref", "title"} for a paper online (its arXiv id, else its DOI when it has no file),
    or {"data", "name", "refs", "title", "record"} for its file, to be taken as a dropped file is. ValueError("no-file")
    when it has neither."""
    _check(lib)
    if not KEY.fullmatch(key or ""):
        raise ValueError("bad-item")
    r, _ = _get(f"{lib}/items/{key}")
    it = item_of(r)
    if it["arxiv"]:
        return {"ref": it["arxiv"], "title": it["title"]}
    for att in _attachments(lib, key):
        f = _file(lib, att)
        if f:
            name = f.name if engine.kind_of(f.name) else f"{f.name}.pdf"   # (a PDF kept under a name without .pdf)
            return {"data": f.read_bytes(), "name": name, "refs": [it["doi"]] if it["doi"] else [], "title": it["title"],
                    "record": record_of(r, it)}
    if it["doi"]:
        return {"ref": it["doi"], "title": it["title"]}
    raise ValueError("no-file")


def record_of(r, it):
    """The item's record, as a document's meta takes it: authors as "Last, First", as arXiv's are."""
    d, m = r["data"], r.get("meta", {})
    authors = []
    for c in d.get("creators", []):
        if c.get("creatorType") in ("author", None):
            name = f"{c['lastName']}, {c.get('firstName', '')}".rstrip(", ") if c.get("lastName") else c.get("name", "")
            if name:
                authors.append(name)
    return {"title": it["title"] if it["title"] != "(untitled)" else "", "authors": authors,
            "date": m.get("parsedDate") or d.get("date") or "", "doi": it["doi"], "url": d.get("url") or "", "citekey": it["citekey"]}


def _attachments(lib, key):
    """The item's files TextFold reads, best first (a stored file before a linked one, in READS's order)."""
    kids = [k["data"] for k in _all(f"{lib}/items/{key}/children")]
    files = [a for a in kids if a.get("itemType") == "attachment" and a.get("contentType") in READS
             and a.get("linkMode") != "linked_url" and KEY.fullmatch(a.get("key") or "")]
    order = list(READS)
    return sorted(files, key=lambda a: (order.index(a["contentType"]), a.get("linkMode") == "linked_file"))


def _file(lib, att):
    """Where an attachment's file is, as Zotero says (else, for a file it stores, in its data folder's storage/), when it
    is a file TextFold reads: a PDF by its first bytes, any other by its name; at most pdf.LIMIT."""
    p = None
    try:
        url, _ = _get(f"{lib}/items/{att['key']}/file/view/url", raw=True, timeout=5)
        u = urllib.parse.urlparse(url.decode().strip())
        if u.scheme == "file":
            p = Path(urllib.request.url2pathname(u.path))
    except (Off, urllib.error.HTTPError, OSError, ValueError):
        pass
    if p is None and att.get("linkMode") in ("imported_file", "imported_url"):
        name = att.get("filename") or ""
        if name and Path(name).name == name:
            p = Path(settings()["data_dir"]).expanduser() / "storage" / att["key"] / name
    if p is None and att.get("linkMode") == "linked_file" and lib == "users/0":   # a file of the reader's own, linked
        p = Path(att.get("path") or "")
        p = p if p.is_absolute() else None
    try:
        if not (p and p.is_file() and 0 < p.stat().st_size <= pdf.LIMIT):
            return None
        if att["contentType"] == "application/pdf":
            with open(p, "rb") as fh:
                return p if fh.read(5) == b"%PDF-" else None
        return p if engine.kind_of(p.name) else None
    except OSError:
        return None


# ── which item became which document ──────────────────────────────────

def _links_file():
    return store.DATA / "zotero.json"


def links():
    try:
        return {k: v["paper"] for k, v in json.loads(_links_file().read_text(encoding="utf-8")).items()}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


def link(lib, key, pid, title, record=None):
    f = _links_file()
    with _lock:
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data[f"{lib}/{key}"] = {"paper": pid, "title": title, **({"record": record} if record else {})}
        f.parent.mkdir(parents=True, exist_ok=True)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
        tmp.replace(f)


def apply(pid):
    """A document read from an item's own file (pdf-…, doc-…) given the item's record: its title, authors, date and DOI
    as Zotero has them, and its citekey as the name of its source. Called once the document is stored (pdfdoc.store_pdf),
    and when the item is read (a document read at once, or read before); True when it changed."""
    if not pid.startswith(("pdf-", "doc-")):
        return False
    mf = store.pdir(pid) / "meta.json"
    try:
        data = json.loads(_links_file().read_text(encoding="utf-8"))
        meta = json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    found = [(where, v) for where, v in data.items() if isinstance(v, dict) and v.get("paper") == pid and v.get("record")]
    if not found:
        return False
    where, v = found[-1]
    rec, (lib, _, key) = v["record"], where.rpartition("/")
    for k in ("title", "authors", "date", "doi"):
        if rec.get(k):
            meta[k] = rec[k]
    if rec.get("url") or rec.get("doi"):
        meta["abs_url"] = rec.get("url") or f"https://doi.org/{rec['doi']}"
    meta["label"] = f"Zotero · {rec.get('citekey') or (meta.get('source') or {}).get('name') or meta['title']}"
    meta["zotero"] = {"library": lib, "item": key, "citekey": rec.get("citekey", "")}
    mf.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return True
