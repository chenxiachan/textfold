"""Download a paper's arXiv HTML (LaTeXML) and its metadata, once."""
import json
import re
import urllib.parse
import urllib.request
import uuid
from html import unescape
from pathlib import Path

from . import __version__

UA = {"User-Agent": f"TextFold/{__version__} (research prototype; github.com/chenxiachan/textfold)"}


def paper_id(ref):
    """'https://arxiv.org/abs/2512.23916v2' -> '2512.23916' (the reader always follows the latest version)."""
    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?", ref)
    if not m:
        raise ValueError(f"not an arXiv id: {ref}")
    return m.group(1)


def get(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
        return r.read(), r.geturl()


def fetch(pid, root: Path, force=False):
    from . import store
    from .storage import DocumentStore, LocalDirectoryBackend
    repo = store.repository() if Path(root) == Path(store.DATA) else DocumentStore(LocalDirectoryBackend(root))
    with repo.edit(pid, create=True) as document:
        if document.source.html is not None and document.meta is not None and not force:
            return document
        html, final = get(f"https://arxiv.org/html/{pid}")
        abs_html = get(f"https://arxiv.org/abs/{pid}")[0].decode("utf-8", "replace")
        metas = re.findall(r'<meta name="citation_(\w+)" content="([^"]*)"', abs_html)
        lic = re.search(r'href="(https?://(?:arxiv\.org/licenses|creativecommons\.org)[^"]+)"', abs_html)
        version = re.search(r"/html/\d{4}\.\d{4,5}(v\d+)", final) or re.search(r'src="\d{4}\.\d{4,5}(v\d+)/', html.decode("utf-8", "replace"))
        meta = {
            "id": pid,
            "version": version.group(1) if version else "",
            "title": unescape(next((v for k, v in metas if k == "title"), pid)),
            "authors": [unescape(v) for k, v in metas if k == "author"],
            "date": next((v for k, v in metas if k == "date"), ""),
            "abs_url": f"https://arxiv.org/abs/{pid}",
            "html_url": final,
            "license": lic.group(1) if lic else "",
        }
        document.meta = meta
        document.source.html = html.decode("utf-8", "replace")
        return document


def fetch_images(srcs, base_url, out_dir: Path):
    """Download figure images next to the built page, four at a time, each kept smaller (picture.py; one kept before
    that, as it came, stays as it is); returns {original src: local path}."""
    from concurrent.futures import ThreadPoolExecutor

    from . import picture

    out_dir.mkdir(parents=True, exist_ok=True)

    def one(s):
        name = re.sub(r"[^\w.\-]", "_", s.split("/")[-1])
        f = next((g for g in (out_dir / name, out_dir / (name + ".webp")) if g.exists()), None)
        if f is None:
            data, kept = picture.compact(get(urllib.parse.urljoin(base_url, s))[0], name)
            f = out_dir / kept
            tmp = f.with_name(f"{f.name}.{uuid.uuid4().hex[:8]}.part")   # (a page built meanwhile never finds half a picture)
            tmp.write_bytes(data)
            tmp.replace(f)
        return s, f"{out_dir.name}/{f.name}"

    with ThreadPoolExecutor(4) as ex:
        return dict(ex.map(one, srcs))

