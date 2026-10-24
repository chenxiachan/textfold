"""What has been generated for each paper, as versioned layers on disk.

  papers/<id>/source.html, meta.json     the arXiv HTML, fetched once (or a document from this computer, local.py)
  papers/<id>/layers/en.json             the English ladder and the argument links
  papers/<id>/layers/tr-<lang>.json      one translation, stamped with the English version it was built on
  papers/.trash/<id>-<time>/             a paper the reader deleted (moved there whole, notes included; not listed), with
                                         library.json: its place in the library, for the reader's Undo (untrash);
                                         erased after 30 days (empty_trash)

A model samples differently on every run, so the same paper does not come out the same twice.
The layers are what makes a paper read the same every time it is opened: a reader is assembled
from them without any model call, and "regenerate" replaces one layer with a new version.
A translation whose English base has since been regenerated is reported as stale.
"""
import json
import contextlib
import functools
import locale
import os
import re
import sys
import time
from pathlib import Path
from .storage import DocumentStore, LocalDirectoryBackend
from ._storage_layout import decode_text

from . import ladder


def codepage():
    """The system's own encoding for a text file saved in its locale: a Chinese Windows's GBK, a German one's
    Windows-1252, UTF-8 on a Mac. Python 3.11's getencoding() is not swayed by UTF-8 mode, which the app runs in."""
    get = getattr(locale, "getencoding", None)
    return (get() if get else locale.getpreferredencoding(False)) or "utf-8"


def read_text(f):
    """A file of TextFold's own (a paper's meta.json, source.html and layers, the library, the settings, the notes, a
    kept reply), as text. It is written as UTF-8; TextFold 0.10.9 and before wrote it on Windows in the system's
    codepage (GBK on a Chinese Windows: a title, a translation, a note in Chinese), and such a file is read in that and
    written again as UTF-8, so the library an update finds still opens. Line ends as Path.read_text gives them."""
    raw = Path(f).read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode(codepage())
        except (UnicodeDecodeError, LookupError):
            return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        tmp = Path(f).with_name(Path(f).name + ".utf8")
        try:   # once: from now on it reads as UTF-8
            tmp.write_text(text, encoding="utf-8")
            os.replace(tmp, f)
        except OSError:
            tmp.unlink(missing_ok=True)
        return text
    return text.replace("\r\n", "\n").replace("\r", "\n")
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
_DEFAULT_DATA = DATA
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
        if not (IMPORTED_ID.fullmatch(ref) and has(ref)):
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
    return repository().local_path(pid)


_repository = None
_repository_default = None


def repository():
    """Default library, selected independently of logs, uploads and the engine."""
    global _repository, _repository_default
    default = (Path(DATA), Path(HOME))
    if _repository is None or (_repository_default is not None and _repository_default != default):
        from .storage_settings import active_path
        # Embedders and existing tests that explicitly replace DATA get an
        # isolated library, never a persisted selection from the user's home.
        root = active_path(DATA, HOME) if Path(DATA) == Path(_DEFAULT_DATA) else DATA
        from .cloud_settings import configured
        cloud = configured(HOME, DATA) if Path(DATA) == Path(_DEFAULT_DATA) else None
        _repository = DocumentStore(cloud or LocalDirectoryBackend(root))
        _repository_default = default
    return _repository


@contextlib.contextmanager
def use_backend(backend):
    """Install a backend explicitly (embedding and backend contract tests)."""
    global _repository, _repository_default
    previous = (_repository, _repository_default)
    _repository, _repository_default = DocumentStore(backend), None
    try:
        yield _repository
    finally:
        _repository, _repository_default = previous


def in_document(fn):
    """A file-based parser borrows one scoped working copy; nested calls join it."""
    @functools.wraps(fn)
    def wrapped(pid, *args, **kw):
        identity = pid["meta"]["id"] if isinstance(pid, dict) else pid
        with repository().workspace(identity):
            return fn(pid, *args, **kw)
    return wrapped


def document(pid):
    """One complete persisted document, independent of its backend layout."""
    return repository().get(pid)


def edit(pid, create=False):
    return repository().edit(pid, create=create)


def has(pid, name="meta.json"):
    if name == "meta.json":
        try:
            repository().info(pid)
            return True
        except FileNotFoundError:
            return False
    # Compatibility for file-based integrations.
    return repository().has(pid, name)


def readable(pid):
    try:
        return repository().info(pid).readable
    except FileNotFoundError:
        return False


def keep_original(pid, format, data, only_if_missing=False):
    with edit(pid) as doc:
        if format == "pdf":
            if not only_if_missing or doc.source.pdf is None:
                doc.source.pdf = data
        elif not only_if_missing or format not in doc.source.originals:
            doc.source.originals[format] = data


def asset(pid, name):
    return repository().read_asset(pid, name)


def put_asset(pid, name, data):
    repository().write_asset(pid, name, data)


def text(pid, name):
    return decode_text(asset(pid, name))


def storage_status():
    from . import storage_settings
    backend = repository().backend
    if not isinstance(backend, LocalDirectoryBackend):
        local = storage_settings.status(storage_settings.active_path(DATA, HOME), HOME)
        return {"backend": getattr(backend, "name", "custom"), "path": "", "pending": "", "error": "", "local": local}
    return storage_settings.status(backend.root, HOME)


def schedule_storage(path=None, cancel=False):
    from . import storage_settings
    backend = repository().backend
    if not isinstance(backend, LocalDirectoryBackend):
        from .offline_storage import OfflineBackend
        if not isinstance(backend, OfflineBackend):
            raise ValueError("The selected backend does not expose a local folder setting")
        storage_settings.schedule(storage_settings.active_path(DATA, HOME), HOME, path, cancel)
        return storage_status()
    return storage_settings.schedule(backend.root, HOME, path, cancel)


def initialize_storage():
    """Called before a server accepts work; never relocates a live library."""
    global _repository, _repository_default
    if _repository is not None and _repository_default is None:
        return   # an embedding explicitly installed a backend
    from .cloud_settings import configured
    cloud = configured(HOME, DATA) if Path(DATA) == Path(_DEFAULT_DATA) else None
    from . import storage_settings
    # Cloud selection must not activate a scheduled local-folder migration.
    root = None if cloud else storage_settings.activate(DATA, HOME)
    _repository = DocumentStore(cloud or LocalDirectoryBackend(root))
    _repository_default = (Path(DATA), Path(HOME))


def discard_document(pid):
    try:
        repository().delete(pid)
    except FileNotFoundError:
        pass


def read(pid, name):
    try:
        return document(pid).layers.get(name)
    except FileNotFoundError:
        return None


def write(pid, name, data):
    with edit(pid) as doc:
        doc.layers[name] = data


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
    cjk = ladder.cjk_doc(doc)
    for uid, u in doc["units"].items():
        u["lad"] = en["units"].get(uid) or ladder.local(u, doc["atoms"], cjk)
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
    return sorted(k[3:] for k in document(pid).layers if k.startswith("tr-"))


def parsed(pid):
    doc = document(pid)
    if doc.source.html is None or doc.meta is None:
        raise FileNotFoundError(pid)
    return parse(doc.source.html, doc.meta)


def assemble(pid, only=None):
    """The paper as the reader needs it, from the layers alone (no model call). only: the translations to carry (a page
    saved in one language), else all of them."""
    saved = document(pid)
    if saved.source.html is None or saved.meta is None:
        raise FileNotFoundError(pid)
    doc = parse(saved.source.html, saved.meta)
    en = saved.layers.get("en")
    meta = doc["meta"]
    meta["langs"], meta["stale"], meta["versions"] = [], [], {}
    cjk = ladder.cjk_doc(doc)
    if cjk:   # the reader's CJK rules (an English paper's page carries nothing new)
        meta["cjk"] = True
    if en:
        apply_en(doc, en)
        meta["langs"].append("en")
        meta["versions"]["en"] = en["version"]
    else:
        for u in doc["units"].values():
            u["lad"] = ladder.local(u, doc["atoms"], cjk)
    for lang in sorted(k[3:] for k in saved.layers if k.startswith("tr-")):
        if only is not None and lang not in only:
            continue
        tr = saved.layers[f"tr-{lang}"]
        apply_tr(doc, lang, tr)
        meta["langs"].append(lang)
        meta["versions"][lang] = tr["version"]
        if not en or tr.get("base") != en["version"]:
            meta["stale"].append(lang)
        else:   # paragraphs it has no translation for (a call that failed): asked for when it is generated again
            from .translate import gaps   # (translate imports ladder and llm, which read through this module)
            n = len(gaps(doc, lang))
            if n:
                meta.setdefault("gaps", {})[lang] = n
    return doc


def papers(index=None):
    """The backend's document index, without loading originals or attachments."""
    out = []
    for info in repository().list() if index is None else index:
        meta = info.meta
        out.append({"id": info.id, "title": meta["title"], "titles": info.titles,
                    "authors": meta.get("authors", []), "langs": info.languages,
                    "updated": info.updated, "notes": info.note_count, "readable": info.readable,
                    **({"zotero": True} if meta.get("zotero") else {})})
    return sorted(out, key=lambda p: -p["updated"])


def trash(pid, kept=None):
    return repository().backend.trash(pid, kept)


def untrash(pid):
    if not valid_id(pid):
        return None
    return repository().backend.restore(pid)


TRASH_DAYS = 30


def empty_trash(days=TRASH_DAYS, now=None):
    before = (time.time() if now is None else now) - days * 86400
    return repository().backend.purge(before)
