"""Reader pages. The same reader in two modes:
  app     served by the local server, with the sidebar, the language menu that can generate, settings
  static  one self-contained file (python -m textfold export): CSS, JS, data and images all inside

A page asks no other server for anything. Its fonts (Inter, Source Serif 4) and KaTeX are kept in textfold/web/vendor: the app
serves them, a gallery keeps them beside its pages (assets), and a single file carries them inside.
"""
import base64
import io
import json
import re
import urllib.parse
from html import escape
from pathlib import Path

from PIL import Image

from . import store
from .fetch import fetch_images

WEB = Path(__file__).resolve().parent / "web"   # the reader's pages, styles, scripts and vendor files
VENDOR = WEB / "vendor"
APP_ASSETS = "/static/vendor/"


def _data(f, kind):
    return f"url(data:{kind};base64,{base64.b64encode(f.read_bytes()).decode()})"


def fonts_head(base=None):
    """The fonts, from base (the app's /static/vendor/, a gallery's assets/) or, with none, inside the page: there the
    Latin faces only, the rest falling back to the system's."""
    if base:
        return f'<link rel="stylesheet" href="{base}fonts/fonts.css">'
    css = (VENDOR / "fonts" / "fonts.css").read_text(encoding="utf-8")
    faces = [m.group(0) for m in re.finditer(r"/\* latin \*/\n@font-face \{.*?\}", css, re.S)]
    return "<style>" + "\n".join(re.sub(r"url\(([\w.-]+\.woff2)\)", lambda u: _data(VENDOR / "fonts" / u.group(1), "font/woff2"), f)
                                 for f in faces) + "</style>"


def _katex_inside():
    css = re.sub(r"url\((fonts/[\w-]+\.woff2)\)", lambda u: _data(VENDOR / "katex" / u.group(1), "font/woff2"),
                 (VENDOR / "katex" / "katex.min.css").read_text(encoding="utf-8"))
    return f'<style id="katex-css">{css}</style><script>{(VENDOR / "katex" / "katex.min.js").read_text(encoding="utf-8")}</script>'


def _has_tex(doc):
    """Math the model wrote that matches no formula of the paper ({"x": latex}): only that needs KaTeX."""
    return '"x":' in json.dumps([doc["units"], doc["chunks"]])


ANIMATED_INSIDE = 2_000_000   # a moving picture larger than this goes into a single-file page as its first frame


def data_uri(f, max_side=1600):
    if f.suffix.lower() == ".svg":  # vector stays vector: crisp at every zoom, and small for line plots
        svg = f.read_text(encoding="utf-8", errors="replace")
        # "&" escaped too: in the page's src="…" an entity of the SVG's own (&quot;) would be decoded first and break it
        return "data:image/svg+xml;charset=utf-8," + urllib.parse.quote(svg, safe=" /:=;,'()-._~!*+@?$")
    im = Image.open(f)
    if getattr(im, "is_animated", False) and f.stat().st_size <= ANIMATED_INSIDE:   # a small demo that moves, as it is
        return f"data:image/{(im.format or 'gif').lower()};base64," + base64.b64encode(f.read_bytes()).decode()
    im.thumbnail((max_side, max_side))
    if im.mode not in ("RGB", "RGBA"):
        im = im.convert("RGBA")
    buf = io.BytesIO()
    im.save(buf, "WEBP", quality=80, method=6)
    return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()


def inline_images(doc, app=False):
    """Figures go inside the page: one file that still works when forwarded, previewed, or opened on a phone. In the app
    a picture other than a drawing is the server's to give (/p/<id>/img/<name>): a page carrying a README's GIFs inside
    weighed ten megabytes, and the zoom copies its figures; that way they move as they should, and the page stays light."""
    meta = doc["meta"]
    cache = store.pdir(meta["id"]) / "img"
    local = fetch_images(doc["images"], meta["html_url"], cache)
    pid = urllib.parse.quote(meta["id"])
    uris = {src: (f"/p/{pid}/{path}" if app and not path.lower().endswith(".svg") else data_uri(cache / path.split("/", 1)[1]))
            for src, path in local.items()}
    for b in doc["blocks"]:
        if "html" in b:
            for src, uri in uris.items():
                b["html"] = b["html"].replace(f'src="{src}"', f'src="{uri}"')


def payload(doc, app, katex=None, saved=None, pdf=None):
    data = {k: doc[k] for k in ("meta", "atoms", "units", "chunks", "blocks", "bib", "edges", "alias")}
    data["app"] = app
    data["katex"] = katex   # where the reader finds KaTeX: a folder, "inside" the page, or nowhere (math stays as written)
    if pdf:   # a gallery's page: its PDF and PDF.js beside it ({"file", "pdfjs"}, relative to the page)
        data["pdfAt"] = pdf
    if saved:   # saved from the reader: its notes (kept apart from any other page's in the viewer's browser), its language
        data.update(saved)
    return json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


def page(doc, app=False, assets=None, saved=None, pdf=None):
    """assets: for a static page published beside a folder holding web/vendor's fonts/ and katex/ (a gallery). saved:
    a page the reader saved for themself ({"notes", "saved", "lang"}: the notes chosen, an id of the file, its language).
    pdf: the paper's PDF beside a gallery's page, for the original pages next to the text ({"file", "pdfjs"})."""
    inline_images(doc, app)
    t = (WEB / "template.html").read_text(encoding="utf-8")
    if app:
        head = fonts_head(APP_ASSETS) + '<link rel="stylesheet" href="/static/reader.css"><link rel="stylesheet" href="/static/app.css">'
        scripts = ('<script src="/static/i18n.js"></script><script src="/static/tutorial.js"></script><script src="/static/reader.js"></script>'
                   '<script src="/static/app.js"></script>')
        side, cls, katex = '<aside id="sidebar" class="collapsed"></aside>', "app", APP_ASSETS + "katex/"
    else:
        head = fonts_head(assets) + f"<style>{(WEB / 'reader.css').read_text(encoding="utf-8")}</style>"
        scripts = f"<script>{(WEB / 'i18n.js').read_text(encoding="utf-8")}</script><script>{(WEB / 'reader.js').read_text(encoding="utf-8")}</script>"
        side, cls, katex = "", "static", (assets + "katex/") if assets else None
        if not assets and (_has_tex(doc) or (saved and saved.get("notes"))):   # (an answer's formulas need it too)
            head += _katex_inside()
            katex = "inside"
    return (t.replace("{{TITLE}}", escape(doc["meta"]["title"])).replace("{{HEAD}}", head).replace("{{CLASS}}", cls)
             .replace("{{SIDEBAR}}", side).replace("{{SCRIPTS}}", scripts).replace("{{DATA}}", payload(doc, app, katex, saved, pdf)))


def export(pid):
    doc = store.assemble(pid)
    out = store.DATA / "out" / pid
    out.mkdir(parents=True, exist_ok=True)
    f = out / "index.html"
    f.write_text(page(doc, app=False), encoding="utf-8")
    return f
