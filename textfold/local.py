"""Documents from this computer, kept as arXiv papers are: papers/<id>/ holds source.html (the normalized HTML that
parse.py reads), the file as it came (source.md), meta.json, and img/ with the images it shows.

A document's id is its title and a hash of its text: the same file opened twice is one paper, and an edited file is a
new one, since the stored layers belong to the text they were made from. A document already stored is not written
again: its source stays the one its layers were made from.
"""
import base64
import hashlib
import json
import re
import shutil
import urllib.parse
import urllib.request
from pathlib import Path

from . import markdown, semantic, store
from .fetch import UA

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
    """Where each image of the document is kept (img/NN-name): copied from beside the file, downloaded, or decoded
    from a data URI. One that cannot be had is left out (the page shows its name)."""

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
            fn = f"{len(self.seen) + 1:02d}-{name}"
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


def import_text(text, name="document.md", base=None):
    """Store a document given as text (base: the folder its relative image paths start from, if known). Returns its id."""
    kind = KINDS.get(Path(name).suffix.lower())
    if kind is None:
        raise ValueError(f"not a Markdown file: {name}")
    html, fm = markdown.to_html(text)
    title, html = markdown.take_title(html, fm, name)
    pid = make_id(title, text)
    meta = {"id": pid, "version": "", "title": title, "authors": fm["authors"], "date": fm["date"],
            "abs_url": "", "html_url": "", "license": fm["license"] if re.match(r"https?://", fm["license"]) else "",
            "source": {"kind": kind, "name": Path(name).name}, "label": Path(name).name}
    return store_doc(pid, html, meta, "source.md", text, lambda folder: Images(folder, base).resolve, sizes=True)


def store_doc(pid, html, meta, raw_name, raw, resolver, sizes=False):
    """A document's semantic HTML stored as a paper: normalized (with its images, through resolver(img folder)) into
    papers/<pid>/, written whole or not at all. A paper already stored stays as it is."""
    d = store.pdir(pid)
    if (d / "meta.json").exists() and (d / "source.html").exists():
        return pid
    tmp = d.with_name(d.name + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True)
    try:
        (tmp / "source.html").write_text(semantic.normalize(html, resolve_img=resolver(tmp / "img"), sizes=sizes), encoding="utf-8")
        (tmp / raw_name).write_text(raw, encoding="utf-8")
        (tmp / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
        shutil.rmtree(d, ignore_errors=True)
        tmp.rename(d)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return pid


def _read(p):
    """A text file as text: UTF-8 first (the encoding of the web, and of this app's own files), then GBK (a Windows
    file saved in its locale), and as UTF-8 with stand-ins when neither holds. The BOM comes off with -sig."""
    data = p.read_bytes()
    for enc in ("utf-8-sig", "gbk"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", "replace")


def import_file(path):
    p = Path(path).expanduser().resolve()
    return import_text(_read(p), p.name, p.parent)
