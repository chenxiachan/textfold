"""Download a paper's arXiv HTML (LaTeXML) and its metadata, once."""
import json
import re
import urllib.parse
import urllib.request
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
    d = root / "papers" / pid
    d.mkdir(parents=True, exist_ok=True)
    src, meta_f = d / "source.html", d / "meta.json"
    if src.exists() and meta_f.exists() and not force:
        return d
    html, final = get(f"https://arxiv.org/html/{pid}")
    src.write_bytes(html)
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
    meta_f.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    return d


def fetch_images(srcs, base_url, out_dir: Path):
    """Download figure images next to the built page; returns {original src: local path}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    local = {}
    for s in srcs:
        name = re.sub(r"[^\w.\-]", "_", s.split("/")[-1])
        f = out_dir / name
        if not f.exists():
            data, _ = get(urllib.parse.urljoin(base_url, s))
            f.write_bytes(data)
        local[s] = f"{out_dir.name}/{name}"
    return local

