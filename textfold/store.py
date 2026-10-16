"""What has been generated for each paper, as versioned layers on disk.

  papers/<id>/source.html, meta.json     the arXiv HTML, fetched once (or a document from this computer, local.py)
  papers/<id>/layers/en.json             the English ladder and the argument links
  papers/<id>/layers/tr-<lang>.json      one translation, stamped with the English version it was built on
  papers/.trash/<id>-<time>/             a paper the reader deleted (moved there whole, notes included; not listed)

A model samples differently on every run, so the same paper does not come out the same twice.
The layers are what makes a paper read the same every time it is opened: a reader is assembled
from them without any model call, and "regenerate" replaces one layer with a new version.
A translation whose English base has since been regenerated is reported as stale.
"""
import json
import os
import re
import sys
import time
from pathlib import Path

from . import ladder
from .fetch import paper_id
from .parse import parse

ROOT = Path(__file__).resolve().parent.parent   # the project folder, when run from a clone of it
SOURCE = (ROOT / "pyproject.toml").exists()     # run from a clone (else installed with pip)


def app_data():
    """The system's place for an application's own data (the desktop app's too, so both read one library)."""
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "TextFold"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "TextFold"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "textfold"


# the reader's papers and exports: the project folder when run from it, the system's data folder when installed with
# pip, or the folder TEXTFOLD_DATA names (the desktop app's; PAPERFOLD_DATA, its name before TextFold, is still read)
DATA = Path(os.environ.get("TEXTFOLD_DATA") or os.environ.get("PAPERFOLD_DATA") or (ROOT if SOURCE else app_data()))
HOME = Path.home() / ".textfold"   # the reader's settings and keys, outside the project folder
# where they were kept under earlier names, newest first: PaperFold, Dynamic Reader (read, never removed)
OLD_HOMES = (Path.home() / ".paperfold", Path.home() / ".dynamic-reader")


# a paper's id: an arXiv id; or, stored when it was first opened, a document from this computer (local.py:
# md-<title words>-<hash>), a paper from Europe PMC (epmc.py: pmc<n>, ppr<n>) or a preprint from bioRxiv or medRxiv
# (biorxiv.py: biorxiv-<DOI suffix>), a Wikipedia article (wiki.py: wiki-<language>-<page id>), a GitHub README
# (github.py: gh-<owner>--<repository>), a PDF read by the PDF engine (pdfdoc.py: pdf-<file hash>) or another file it
# read (a Word file, an e-book, a picture: doc-<file hash>) or any other web page (webpage.py: web-<host>-<address hash>)
ARXIV_ID = re.compile(r"\d{4}\.\d{4,5}")
IMPORTED_ID = re.compile(r"md-[a-z0-9]+(?:-[a-z0-9]+)*|pmc\d{4,9}|ppr\d{4,9}|(?:biorxiv|medrxiv)-[0-9.]{3,40}|wiki-[a-z][a-z-]{1,15}-\d{1,10}"
                         r"|gh-[a-z0-9-]{1,39}--[a-z0-9._-]{1,100}|(?:pdf|doc)-[0-9a-f]{16}|web-[a-z0-9-]{1,40}-[0-9a-f]{12}")


def valid_id(pid):
    return bool(ARXIV_ID.fullmatch(pid or "") or (IMPORTED_ID.fullmatch(pid or "") and len(pid) <= 80))


def is_imported(pid):
    return bool(IMPORTED_ID.fullmatch(pid or ""))


def resolve(ref):
    """The id a reference names (ValueError when it names none): a stored document's id as it is; an arXiv id or
    link; a bioRxiv or medRxiv link or DOI (read from the server itself: its own text, its newest version); a PubMed
    Central or Europe PMC preprint id or link; any other DOI, looked up in Europe PMC (ValueError("no-fulltext") when
    it holds no open full text); a Wikipedia article's link (ValueError("no-article") when there is no such article);
    a GitHub repository's link (its README; read when the paper is generated); any other web page's link (its article,
    read when it is generated)."""
    from . import biorxiv, epmc, github, wiki   # (they store through this module)
    ref = (ref or "").strip()
    if ref.startswith(("md-", "pdf-", "doc-")):
        if not (IMPORTED_ID.fullmatch(ref) and (pdir(ref) / "meta.json").exists()):
            raise ValueError(f"no such document: {ref}")
        return ref
    owner, repo = github.ref_of(ref)
    if owner:
        return github.pid_of(owner, repo)
    if re.search(r"arxiv\.org|^(arxiv:)?\d{4}\.\d{4,5}(v\d+)?$|10\.48550/", ref, re.I):
        return paper_id(ref)
    if IMPORTED_ID.fullmatch(ref):
        return ref
    lang, title = wiki.ref_of(ref)
    if lang:
        return wiki.pid_of(lang, wiki.lookup(lang, title)[0])
    server, bdoi = biorxiv.ref_of(ref)
    if bdoi:
        server, _ = biorxiv.find(bdoi, server)
        return biorxiv.pid_of(server, bdoi)
    pid, doi = epmc.ref_of(ref)
    if pid:
        return pid
    if doi:
        return epmc.by_doi(doi)
    if re.match(r"https?://[^\s/]+\.[^\s/]+", ref, re.I):   # any other web page: its article
        from . import webpage
        return webpage.register(ref)
    return paper_id(ref)


def pdir(pid):
    return DATA / "papers" / pid


def _file(pid, name):
    return pdir(pid) / "layers" / f"{name}.json"


def read(pid, name):
    f = _file(pid, name)
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def write(pid, name, data):
    f = _file(pid, name)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    tmp.replace(f)


def stamp():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def save_en(doc, model):
    write(doc["meta"]["id"], "en", {
        "version": stamp(), "model": model,
        "units": {uid: u["lad"] for uid, u in doc["units"].items() if "lad" in u},
        "chunks": {c["id"]: c.get("lad") for c in doc["chunks"]},
        "edges": [e for e in doc["edges"] if e["rel"] != "ref"],
    })


def save_tr(doc, lang, base, model):
    write(doc["meta"]["id"], f"tr-{lang}", {
        "version": stamp(), "base": base, "model": model,
        "units": {uid: u["tr"][lang] for uid, u in doc["units"].items() if lang in u.get("tr", {})},
        "chunks": {c["id"]: c["tr"][lang] for c in doc["chunks"] if lang in c.get("tr", {})},
        "title": doc["meta"].get("titles", {}).get(lang),
    })


def apply_en(doc, en):
    for uid, u in doc["units"].items():
        u["lad"] = en["units"].get(uid) or ladder.local(u, doc["atoms"])
    for c in doc["chunks"]:
        c["lad"] = en["chunks"].get(c["id"])
    have = {(e["f"], e["t"]) for e in en["edges"]}
    doc["edges"] = [e for e in doc["edges"] if (e["f"], e["t"]) not in have] + [
        e for e in en["edges"] if e["f"] in doc["units"] and (e["tk"] != "unit" or e["t"] in doc["units"])]


def apply_tr(doc, lang, tr):
    for uid, z in tr["units"].items():
        if uid in doc["units"]:
            doc["units"][uid].setdefault("tr", {})[lang] = z
    for c in doc["chunks"]:
        if c["id"] in tr["chunks"]:
            c.setdefault("tr", {})[lang] = tr["chunks"][c["id"]]
    if tr.get("title"):
        doc["meta"].setdefault("titles", {})[lang] = tr["title"]


def languages(pid):
    d = pdir(pid) / "layers"
    return sorted(f.stem[3:] for f in d.glob("tr-*.json")) if d.exists() else []


def parsed(pid):
    d = pdir(pid)
    return parse((d / "source.html").read_text(encoding="utf-8"), json.loads((d / "meta.json").read_text(encoding="utf-8")))


def assemble(pid, only=None):
    """The paper as the reader needs it, from the layers alone (no model call). only: the translations to carry (a page
    saved in one language), else all of them."""
    doc = parsed(pid)
    en = read(pid, "en")
    meta = doc["meta"]
    meta["langs"], meta["stale"], meta["versions"] = [], [], {}
    if en:
        apply_en(doc, en)
        meta["langs"].append("en")
        meta["versions"]["en"] = en["version"]
    else:
        for u in doc["units"].values():
            u["lad"] = ladder.local(u, doc["atoms"])
    for lang in languages(pid):
        if only is not None and lang not in only:
            continue
        tr = read(pid, f"tr-{lang}")
        apply_tr(doc, lang, tr)
        meta["langs"].append(lang)
        meta["versions"][lang] = tr["version"]
        if not en or tr.get("base") != en["version"]:
            meta["stale"].append(lang)
    return doc


def papers():
    """Every paper on disk, newest layer first."""
    out = []
    root = DATA / "papers"
    for d in root.iterdir() if root.exists() else []:
        mf = d / "meta.json"
        if d.name.startswith(".") or not mf.exists():
            continue
        meta = json.loads(mf.read_text(encoding="utf-8"))
        en = read(d.name, "en")
        langs_ = (["en"] if en else []) + languages(d.name)
        titles = {}
        for lang in languages(d.name):
            t = (read(d.name, f"tr-{lang}") or {}).get("title")
            if t:
                titles[lang] = t
        mtimes = [f.stat().st_mtime for f in (d / "layers").glob("*.json")] if (d / "layers").exists() else []
        nf = d / "notes.json"
        out.append({"id": d.name, "title": meta["title"], "titles": titles, "authors": meta.get("authors", []),
                    "langs": langs_, "updated": max(mtimes) if mtimes else mf.stat().st_mtime,
                    "notes": len(json.loads(nf.read_text(encoding="utf-8")).get("notes", [])) if nf.exists() else 0,
                    **({"zotero": True} if meta.get("zotero") else {})})
    return sorted(out, key=lambda p: -p["updated"])


def trash(pid):
    """Delete a paper from the list: its folder (source, layers, model replies, notes) moves to papers/.trash, from
    where it can be moved back by hand. Nothing is erased."""
    to = DATA / "papers" / ".trash" / f"{pid}-{time.strftime('%Y%m%d-%H%M%S')}"
    to.parent.mkdir(parents=True, exist_ok=True)
    pdir(pid).rename(to)
    return to
