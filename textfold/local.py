"""Documents from this computer, kept as arXiv papers are: papers/<id>/ holds source.html (the normalized HTML that
parse.py reads), the file as it came (source.md), meta.json, and img/ with the images it shows.

A document's id is its title and a hash of its text: the same file opened twice is one paper, and an edited file is a
new one, since the stored layers belong to the text they were made from. A document already stored is not written
again: its source stays the one its layers were made from. Unless it is replaced by name (replace_text: a note edited
where it lives, folded again): then it keeps its id, its notes and its folder, and its levels are made again.

Its paragraphs are named by their words ("unit_ids": "text", parse.py), so an edit leaves every other paragraph's id,
and the notes and links on it, as they were. (Documents stored before keep the ids they were read with, u<n>, until
their text is replaced.)
"""
import base64
import hashlib
import json
import re
import copy
import tempfile
import threading
import urllib.parse
import urllib.request
from pathlib import Path

from . import ids, markdown, notes, picture, semantic, store
from .documents import Document, Source
from .fetch import UA
from .parse import parse

IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
MAX_IMG = 15_000_000
KINDS = {".md": "markdown", ".markdown": "markdown", ".mdown": "markdown", ".txt": "markdown"}


def make_id(title, text):
    words = re.findall(r"[a-z0-9]+", title.lower())
    stem, n = [], 0
    for w in words:
        if n + len(w) > 40:
            break
        stem.append(w)
        n += len(w) + 1
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return "md-" + "-".join(stem + [h])


class Images:
    """Where each image of the document is kept (img/NN-name, or NN-name.webp, kept smaller: picture.py): copied from
    beside the file, downloaded, or decoded from a data URI. One that cannot be had is left out (the page shows its
    name)."""

    def __init__(self, folder, base, get=None, pre=None):
        self.folder, self.base, self.seen, self.get = folder, base, {}, get   # get(url) -> (bytes, url): a source's own
        self.pre = pre or {}   # src -> (bytes, name), read beforehand (prefetch)

    def resolve(self, src):
        if src in self.seen:
            return self.seen[src]
        try:
            data, name = self._read(src)
        except Exception:
            data, name = None, ""
        where = None
        if data and len(data) <= MAX_IMG:
            name = re.sub(r"[^\w.\-]", "_", name)[-60:] or "image"
            data, fn = picture.compact(data, f"{len(self.seen) + 1:02d}-{name}")   # (kept smaller: picture.py)
            self.folder.mkdir(parents=True, exist_ok=True)
            (self.folder / fn).write_bytes(data)
            where = f"img/{fn}"
        self.seen[src] = where
        return where

    def _read(self, src):
        if src in self.pre:
            return self.pre[src]
        m = re.match(r"data:image/(png|jpe?g|gif|webp);base64,(.+)$", src, re.S)
        if m:
            return base64.b64decode(m.group(2)), f"image.{m.group(1)}"
        if re.match(r"https?://", src, re.I):
            path = urllib.parse.urlparse(src).path
            if self.get:
                data, _ = self.get(src)
                return (data if data[:4] != b"<!DO" and data[:5] != b"<html" else None), path.rsplit("/", 1)[-1] or "image"
            with urllib.request.urlopen(urllib.request.Request(src, headers=UA), timeout=20) as r:
                ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip()
                if not ctype.startswith("image/"):
                    return None, ""
                name = path.rsplit("/", 1)[-1] or "image"
                if Path(name).suffix.lower() not in IMG_EXT:   # a badge's address has none: the page needs it to draw it
                    name += {"image/svg+xml": ".svg", "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp"}.get(ctype, "")
                return r.read(MAX_IMG + 1), name
        if self.base is None or re.match(r"^[a-z][a-z0-9+.-]*:", src, re.I):
            return None, ""
        f = (self.base / urllib.parse.unquote(src.split("#")[0].split("?")[0])).resolve()
        if f.suffix.lower() not in IMG_EXT or not f.is_file() or f.stat().st_size > MAX_IMG:
            return None, ""
        return f.read_bytes(), f.name


def prefetch(srcs, workers=6):
    """Pictures on the web, read at the same time rather than one after the other (a README's GIFs take seconds
    each): src -> (bytes, name), for Images(pre=...). One that cannot be read is left to Images to try again."""
    from concurrent.futures import ThreadPoolExecutor

    web = sorted({s for s in srcs if re.match(r"https?://", s or "", re.I)})
    reader = Images(None, None)

    def one(src):
        try:
            return src, reader._read(src)
        except Exception:
            return src, None

    with ThreadPoolExecutor(workers) as ex:
        return {src: got for src, got in ex.map(one, web) if got and got[0]}


def _read_md(text, name):
    """A Markdown text as (its title, its semantic HTML, the meta it is stored with but its id)."""
    kind = KINDS.get(Path(name).suffix.lower())
    if kind is None:
        raise ValueError(f"not a Markdown file: {name}")
    html, fm = markdown.to_html(text)
    title, html = markdown.take_title(html, fm, name)
    meta = {"version": "", "title": title, "authors": fm["authors"], "date": fm["date"],
            "abs_url": "", "html_url": "", "license": fm["license"] if re.match(r"https?://", fm["license"]) else "",
            "source": {"kind": kind, "name": Path(name).name}, "label": Path(name).name, "unit_ids": "text"}
    return title, html, meta


def import_text(text, name="document.md", base=None):
    """Store a document given as text (base: the folder its relative image paths start from, if known). Returns its id."""
    title, html, meta = _read_md(text, name)
    pid = make_id(title, text)
    return store_doc(pid, html, {"id": pid, **meta}, "markdown", text, lambda folder: Images(folder, base).resolve, sizes=True)


_REPLACING = threading.Lock()   # serialize replacements of a document


def replace_text(pid, text, name=None, base=None):
    """A stored Markdown document's text replaced by an edited one, under the same id. Its notes, its folder and the
    model's replies (llm-cache) stay; its notes are found again in the new text (notes.reanchor). Its levels belonged
    to the old text: they go to previous/ with it (the one replaced last, notes as they were included), and the next
    generation makes them again from the replies kept, so the model is asked only for the sections whose text changed
    (pipeline.py). Returns the new title. LookupError: no such Markdown document."""
    if not store.valid_id(pid) or not pid.startswith("md-"):
        raise LookupError(pid)
    with _REPLACING:
        return _replace(pid, text, name, base)


def _normalized(html, resolver, sizes=False):
    """Image downloading needs scratch files; persistence gets resource values."""
    with tempfile.TemporaryDirectory(prefix="textfold-images-") as temp:
        folder = Path(temp)
        html = semantic.normalize(html, resolve_img=resolver(folder), sizes=sizes)
        images = {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob("*") if p.is_file()}
        return html, images


def _replace(pid, text, name, base):
    if not store.readable(pid):
        raise LookupError(pid)
    old = store.parsed(pid)
    gone = ids.load(pid)
    with store.edit(pid) as document:
        was = document.meta
        title, html, meta = _read_md(text, name or (was.get("source") or {}).get("name") or "document.md")
        meta = {**was, **meta, "id": pid, "replaced": store.stamp()}
        html, images = _normalized(html, lambda folder: Images(folder, base).resolve, sizes=True)
        html, new = ids.freeze(html, meta, old, gone)
        previous = Document(pid, meta=copy.deepcopy(was),
                            source=Source(html=document.source.html, markdown=document.source.markdown),
                            layers=copy.deepcopy(document.layers), notes=copy.deepcopy(document.notes),
                            images=dict(document.images), _legacy=dict(document._legacy))
        document.previous = previous
        document.meta, document.source.html, document.source.markdown = meta, html, text
        document.images, document.layers = images, {}
    ids.remember(pid, old, new)
    notes.reanchor(pid, old, new)
    return title


def store_doc(pid, html, meta, raw_kind, raw, resolver, sizes=False, replace=False, old=None):
    """Save a complete normalized document. Backends choose its representation."""
    if raw_kind not in ("markdown", "xml", "wiki_html"):
        raise ValueError("Unsupported source kind")
    if not replace and store.readable(pid):
        return pid
    gone = ids.load(pid) if old is not None else None
    html, images = _normalized(html, resolver, sizes)
    html, _ = ids.freeze(html, meta, old, gone)
    with store.edit(pid, create=True) as document:
        if not replace and document.meta is not None and document.source.html is not None:
            return pid
        document.meta, document.source.html, document.images = meta, html, images
        setattr(document.source, raw_kind, raw)
        if replace:
            document.layers = {}   # these belonged to the previous source text
    return pid


def _codepage():
    """The system's own encoding for a text file saved in its locale: a Chinese Windows's GBK, a German one's
    Windows-1252, UTF-8 on a Mac. Python 3.11's getencoding() is not swayed by UTF-8 mode, which the app runs in."""
    return store.codepage()


def _read(p):
    """A text file as text: UTF-8 first (the encoding of the web, and of this app's own files), then the system's own
    codepage (a Windows file saved in its locale), and as UTF-8 with stand-ins when neither holds. The BOM comes off
    with -sig. (Not GBK whatever the system: a German file read as GBK turns "für" into "f黵".)"""
    data = p.read_bytes()
    for enc in dict.fromkeys(("utf-8-sig", _codepage())):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode("utf-8", "replace")


def import_file(path):
    p = Path(path).expanduser().resolve()
    return import_text(_read(p), p.name, p.parent)
