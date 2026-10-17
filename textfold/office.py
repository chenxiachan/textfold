"""The files TextFold reads by itself, without the PDF engine: Word (.docx, through mammoth, its formulas as LaTeX),
PowerPoint (.pptx, its slides as sections), Excel (.xlsx) and CSV tables, saved web pages (.html, .mhtml, their
article found by trafilatura) and e-books (.epub); on a Mac also .doc, .rtf and .odt, which the system's textutil turns
into a Word file first. Written in the shape the engine writes (structured_content.json: pages of blocks, and the
pictures in images/), so that pdfdoc stores them as it stores the engine's.

When the engine is installed, these files go to it instead (it reads slides with formulas, OpenDocument and old Office
files more completely); this is the reader for everyone else, and for a file the engine cannot read."""
import copy
import csv
import email
import html
import io
import json
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import zipfile
from pathlib import Path

from lxml import etree
from lxml import html as lhtml

from . import omml
from .pdfdoc import CAPTION, _unwrap as _plain

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
PML = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
DML = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"
MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
DC = "{http://purl.org/dc/elements/1.1/}"
DCT = "{http://purl.org/dc/terms/}"
OPF = "{http://www.idpf.org/2007/opf}"
# a formula inside a run of text, until the HTML is read back: inline ($...$) and on a line of its own ($$...$$)
IN, OUT, DIN, DOUT = chr(0xE000), chr(0xE001), chr(0xE002), chr(0xE003)
TEXTUTIL = {"doc", "rtf", "odt"}   # read through macOS's textutil, which writes them as .docx
MAX_ROWS, MAX_COLS = 1000, 50      # of a sheet: a table longer than this is not read as a text
PICTURES = {"png": "png", "jpeg": "jpg", "jpg": "jpg", "gif": "gif", "webp": "webp", "svg+xml": "svg", "svg": "svg"}
# names an Office file gives as its author that are no one's: the account, the program that wrote it
NOBODY = re.compile(r"^(?:microsoft office user|microsoft account|administrator|admin|user|owner|unknown|author|"
                    r"openpyxl|python-docx|python-pptx|apache poi|pandoc)$", re.I)
STYLE_MAP = "\n".join([   # Word's paragraph styles that say what a paragraph is (mammoth knows the headings')
    "p[style-name='Title'] => h1.tf-title:fresh",
    "p[style-name='Subtitle'] => p.tf-subtitle:fresh",
    "p[style-name='Author'] => p.tf-author:fresh",
    "p[style-name='Date'] => p.tf-date:fresh",
    "p[style-name='Abstract Title'] => h1:fresh",
    "p[style-name='Caption'] => p.tf-caption:fresh",
    "p[style-name='caption'] => p.tf-caption:fresh",
    "p[style-name='Image Caption'] => p.tf-caption:fresh",
    "p[style-name='Table Caption'] => p.tf-caption:fresh",
    "p[style-name='Figure Caption'] => p.tf-caption:fresh",
    "p[style-name='Quote'] => blockquote > p:fresh",
    "p[style-name='Block Text'] => blockquote > p:fresh",
    "p[style-name='Source Code'] => pre:separator('\\n')",
])


def reads(ext):
    """Whether TextFold reads a file of this extension (engine.kind_of's) by itself."""
    return ext in {"docx", "pptx", "xlsx", "csv", "tsv", "html", "htm", "mhtml", "mht", "epub"} or \
        (ext in TEXTUTIL and sys.platform == "darwin" and bool(shutil.which("textutil")))


def _xml(data):
    return etree.fromstring(data, etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=True))


def _tag(el):
    return etree.QName(el).localname.lower() if isinstance(el.tag, str) else ""


def _escape(s):
    """Text as Markdown reads it: no "<" taken for a tag, no "&" for an entity; a formula's LaTeX as it is (one on
    a line of its own stays marked, for the paragraph to set it apart)."""
    out, pos = [], 0
    for m in re.finditer(f"{IN}(.*?){OUT}|{DIN}(.*?){DOUT}", s, re.S):
        out.append(html.escape(s[pos:m.start()], quote=False))
        if m.group(1) is not None:
            out.append(f"${m.group(1)}$" if m.group(1).strip() else "")
        else:
            out.append(f"{DIN}{m.group(2)}{DOUT}")
        pos = m.end()
    out.append(html.escape(s[pos:], quote=False))
    return "".join(out)


def _wrap(mark, s):
    """Words in bold, italics or struck through, with the spaces around them outside the marks."""
    core = s.strip()
    if not core or mark in core:
        return s
    return s[:len(s) - len(s.lstrip())] + mark + core + mark + s[len(s.rstrip()):]


def _start(s):
    """A paragraph whose first words Markdown would read as a list, a heading or a quote ("1. ...", "- ...")."""
    return re.sub(r"^(\d+)([.)])(\s)", r"\1\\\2\3", re.sub(r"^([-+*#>])(\s)", r"\\\1\2", s))


class Doc:
    """The blocks of a document, as the engine writes them, and its pictures in out/images/."""

    def __init__(self, out, ext):
        self.out, self.ext = Path(out), ext
        self.pages, self.blocks = [], []
        self.meta = {}
        self.authors, self.n = [], 0
        self.caption = None   # a caption above its table, until the table

    def page(self):
        if self.blocks:
            self.pages.append({"page_idx": len(self.pages), "blocks": self.blocks})
        self.blocks = []

    def add(self, kind, content="", **kw):
        block = {"type": kind, "content": content, **kw}
        if self.caption:   # a caption above: its table's (or picture's); above anything else, a paragraph after all
            if kind in ("table", "image") and not kw.get("captions"):
                block["captions"] = [{"content": self.caption}]
            else:
                self.blocks.append({"type": "text", "content": _start(self.caption)})
            self.caption = None
        self.blocks.append(block)
        return block

    def picture(self, data, kind=""):
        """A picture's bytes kept in images/ (one a page cannot show, as PNG): its path there, or None."""
        kind = (kind or "").lower().split("/")[-1].split(";")[0]
        ext = PICTURES.get(kind)
        if not data or len(data) > 15_000_000:
            return None
        if ext is None:   # a TIFF, a BMP (an EMF or WMF, which Pillow cannot draw here, is left out)
            try:
                from PIL import Image
                buf = io.BytesIO()
                Image.open(io.BytesIO(data)).convert("RGB").save(buf, "PNG")
                data, ext = buf.getvalue(), "png"
            except Exception:
                return None
        self.n += 1
        (self.out / "images").mkdir(parents=True, exist_ok=True)
        name = f"images/picture-{self.n}.{ext}"
        (self.out / name).write_bytes(data)
        return name

    def write(self):
        if self.caption:
            self.blocks.append({"type": "text", "content": _start(self.caption)})
        self.page()
        authors = [a for a in self.authors if a and not NOBODY.match(a.strip())]
        doc = {k: v for k, v in {**self.meta, "authors": authors or [a for a in self.meta.get("authors", []) if not NOBODY.match(a)]}.items() if v}
        sc = {"pages": self.pages, "metadata": {"file_suffix": self.ext, "producer": {"name": "textfold"}, "document": doc}}
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "structured_content.json").write_text(json.dumps(sc, ensure_ascii=False))
        return self.out


# ── HTML (Word's through mammoth, an e-book's chapters, a web page's article) as blocks ──

BLOCK = {"p", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "table", "figure", "img", "pre", "blockquote", "div",
         "section", "article", "main", "header", "footer", "aside", "dl", "hr", "body", "html", "center", "details",
         "address", "figcaption", "picture"}
SKIP = {"script", "style", "noscript", "template", "head", "nav", "button", "form", "input", "select", "textarea",
        "svg", "iframe", "object", "video", "audio"}
NOTE_ID = re.compile(r"^(?:footnote|endnote|fn)[-_]?\d+$")
MATH_CLASS = re.compile(r"(?:^|\s)math(?:\s|$)")   # a formula as pandoc and Markdown write one in HTML ("math inline")


def _is_tex(s):
    """Whether a formula's element holds its LaTeX (pandoc's without MathJax holds it set in HTML instead)."""
    s = s.strip()
    return bool(re.search(r"[\\^_{}]", s)) or s.startswith("$")


def _tex(s):
    """A formula's LaTeX without the marks around it ($$, \\[ \\], \\( \\))."""
    s = s.strip()
    for a, b in (("$$", "$$"), ("\\[", "\\]"), ("\\(", "\\)"), ("$", "$")):
        if s.startswith(a) and s.endswith(b) and len(s) >= len(a) + len(b):
            return s[len(a):len(s) - len(b)].strip()
    return s


class Flow:
    """HTML read into a document's blocks. resolve(src): a picture's path in images/ (or an address the store
    fetches), or None for one that cannot be had."""

    def __init__(self, doc, resolve):
        self.doc, self.resolve = doc, resolve

    def walk(self, el):
        """A container: its blocks, and the loose text between them as paragraphs."""
        loose = [el.text or ""]
        for c in el:
            tag = _tag(c)
            if tag in SKIP or not tag:
                pass
            elif tag in BLOCK or any(_tag(d) in BLOCK for d in c.iterdescendants()):
                self.paragraph(loose)
                loose = []
                self.block(c)
            else:
                loose.append(c)
            loose.append(c.tail or "")
        self.paragraph(loose)

    def block(self, el):
        tag, cls = _tag(el), (el.get("class") or "")
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            text = self.inline(el).strip()
            if text:
                if "tf-title" in cls:
                    self.doc.add("doc_title", text, level=1)
                else:
                    self.doc.add("paragraph_title", text, level=int(tag[1]) + 1)
        elif tag == "p" and "tf-author" in cls:
            self.doc.authors.append(re.sub(r"\s+", " ", el.text_content()).strip())
        elif tag == "p" and "tf-date" in cls:
            self.doc.meta.setdefault("created_at", el.text_content().strip())
        elif tag in ("p", "figcaption") and ("tf-caption" in cls or tag == "figcaption"):
            self.caption(self.inline(el).strip())
        elif tag == "p":
            self.paragraph([el], whole=True)
        elif tag in ("ul", "ol"):
            items = [li for li in el if _tag(li) == "li"]
            if items and all(NOTE_ID.match(li.get("id") or "") for li in items):
                for n, li in enumerate(items, 1):   # a document's footnotes, at its end
                    text = self.inline(li).strip()
                    if text:
                        self.doc.add("page_footnote", f"[{n}] {text}")
            else:
                lines = self.listing(el)
                if lines:
                    self.doc.add("list", "\n".join(lines))
        elif tag == "table":
            self.doc.add("table", self.table(el))
        elif tag in ("figure", "picture"):
            caps = [self.inline(c).strip() for c in el.iter() if _tag(c) == "figcaption"]
            for img in el.iter():
                if _tag(img) == "img":
                    self.image(img, [c for c in caps if c])
                    caps = []
            for t in el.iter():
                if _tag(t) == "table":
                    self.doc.add("table", self.table(t))
        elif tag == "img":
            self.image(el)
        elif tag == "pre":
            for br in el.iter("br"):   # (Word's lines in a code paragraph are breaks)
                br.tail = "\n" + (br.tail or "")
            code = el.text_content().strip("\n")
            if code.strip():
                self.doc.add("code", code)
        elif tag == "dl":
            for c in el:
                text = self.inline(c).strip()
                if text:
                    self.doc.add("text", _wrap("**", text) if _tag(c) == "dt" else _start(text))
        elif tag == "aside" and re.search(r"(?:foot|end|rear)note", el.get("{http://www.idpf.org/2007/ops}type") or el.get("epub:type") or ""):
            text = self.inline(el).strip()
            if text:
                self.doc.add("page_footnote", text)
        elif tag in ("div", "span") and MATH_CLASS.search(cls) and _is_tex(el.text_content()):
            tex = _tex(el.text_content())
            if tex:
                self.doc.add("equation", tex)
        elif tag == "hr":
            pass
        else:
            self.walk(el)

    def caption(self, text):
        """A caption: its picture's or table's just before it, or the table's just after it."""
        last = self.doc.blocks[-1] if self.doc.blocks else None
        if not text:
            return
        if last is not None and last["type"] in ("image", "table") and not last.get("captions"):
            last["captions"] = [{"content": text}]
        else:
            self.doc.caption = text   # (the next block takes it, if it is a table or a picture: Doc.add)

    def paragraph(self, nodes, whole=False):
        """Text and inline elements as a paragraph (its pictures as pictures, a formula of its own as an equation)."""
        imgs = []
        parts = []
        for n in nodes:
            if isinstance(n, str):
                parts.append(_escape(re.sub(r"\s+", " ", n)))
            else:
                parts.append(self.inline(n) if whole else self.element(n))
                imgs += [n] if _tag(n) == "img" else [i for i in n.iter() if _tag(i) == "img"]
        text = "".join(parts)
        lead = bool(imgs) and not text.split(DIN)[0].strip()
        if lead:
            for img in imgs:
                self.image(img)
        last = self.doc.blocks[-1] if self.doc.blocks else None
        if not imgs and DIN not in text and CAPTION.match(_plain(text)) and last is not None and \
                last["type"] in ("image", "table") and not last.get("captions"):
            last["captions"] = [{"content": text.strip()}]   # "Table 1. ...", just under its table: its caption
            return
        for k, piece in enumerate(re.split(f"{DIN}(.*?){DOUT}", text, flags=re.S)):
            piece = piece.strip()
            if k % 2:
                if piece:
                    self.doc.add("equation", piece)
            elif piece:
                self.doc.add("text", _start(piece))
        if not lead:
            for img in imgs:
                self.image(img)

    def image(self, img, caps=()):
        src = self.resolve(img.get("src") or "")
        if src:
            block = self.doc.add("image", img.get("alt") or "", image_source=src)
            if caps:
                block["captions"] = [{"content": c} for c in caps]

    def inline(self, el):
        """An element's words as Markdown (its own text and its children's, not what follows it)."""
        parts = [_escape(re.sub(r"\s+", " ", el.text or ""))]
        for c in el:
            parts.append(self.element(c))
            parts.append(_escape(re.sub(r"\s+", " ", c.tail or "")))
        return "".join(parts)

    def element(self, c):
        tag = _tag(c)
        if tag in SKIP or tag == "img" or not tag:
            return ""
        if tag in ("strong", "b"):
            return _wrap("**", self.inline(c))
        if tag in ("em", "i", "cite", "var"):
            return _wrap("*", self.inline(c))
        if tag in ("s", "del", "strike"):
            return _wrap("~~", self.inline(c))
        if tag == "code":
            return f"`{c.text_content()}`" if c.text_content().strip() and "`" not in c.text_content() else self.inline(c)
        if tag in ("sup", "sub"):
            inner = self.inline(c).strip()
            return f"<{tag}>{inner}</{tag}>" if inner else ""
        if tag == "br":
            return " "
        if tag == "math":
            tex = _mathml_tex(c)
            return f"{DIN}{tex}{DOUT}" if c.get("display") == "block" else f"${tex}$"
        if tag in ("span", "div") and MATH_CLASS.search(c.get("class") or "") and _is_tex(c.text_content()):
            tex = _tex(c.text_content())
            return f"{DIN}{tex}{DOUT}" if re.search(r"display|block", c.get("class") or "") else f"${tex}$"
        if tag == "a":
            text, href = self.inline(c), (c.get("href") or "").strip()
            if text.strip() in ("↑", "↩", "↩︎", "^"):
                return ""   # a footnote's way back to its mark
            if re.match(r"https?://|mailto:", href, re.I) and text.strip():
                return f"[{text.strip()}]({href.replace(' ', '%20').replace(')', '%29')})"
            return text
        return self.inline(c)

    def listing(self, el, depth=0):
        lines, n = [], int(el.get("start") or 1) if (el.get("start") or "").isdigit() else 1
        for li in el:
            if _tag(li) != "li":
                continue
            nested = [c for c in li if _tag(c) in ("ul", "ol")]
            words = [_escape(re.sub(r"\s+", " ", li.text or ""))]
            for c in li:
                if c not in nested:
                    words.append(self.inline(c) if _tag(c) in BLOCK else self.element(c))
                words.append(_escape(re.sub(r"\s+", " ", c.tail or "")))
            text = re.sub(f"{DIN}(.*?){DOUT}", r"$\1$", "".join(words), flags=re.S).strip()
            mark = f"{n}." if _tag(el) == "ol" else "-"
            if text:
                lines.append("  " * depth + f"{mark} {text}")
                n += 1
            for sub in nested:
                lines += self.listing(sub, depth + 1)
        return lines

    def table(self, el):
        """A table as clean HTML: its cells' words and spans, its pictures kept, its formulas as LaTeX."""
        t = copy.deepcopy(el)
        for x in list(t.iter()):
            if not isinstance(x.tag, str):
                continue
            for a in list(x.attrib):
                if a not in ("colspan", "rowspan", "src", "alt"):
                    del x.attrib[a]
            if _tag(x) == "img":
                src = self.resolve(x.get("src") or "")
                if src:
                    x.set("src", src)
                elif x.getparent() is not None:
                    x.drop_tree()
                    continue
            for side in ("text", "tail"):
                v = getattr(x, side)
                if v and (IN in v or DIN in v):
                    setattr(x, side, re.sub(f"[{IN}{DIN}](.*?)[{OUT}{DOUT}]", r"$\1$", v, flags=re.S))
        for x in [x for x in t.iter("p") if not x.text_content().strip() and x.find(".//img") is None]:
            x.drop_tree()   # an empty paragraph in a cell (textutil writes one under each)
        return re.sub(r">\s+<", "><", lhtml.tostring(t, encoding="unicode", method="html")).strip()


def _mathml_tex(m):
    """A MathML formula's LaTeX, when it carries it (an alttext, a TeX annotation); else its text."""
    if m.get("alttext"):
        return m.get("alttext")
    for a in m.iter():
        if _tag(a) == "annotation" and "tex" in (a.get("encoding") or "").lower():
            return (a.text or "").strip()
    return re.sub(r"\s+", " ", m.text_content()).strip()


def _fragment(markup):
    """Some HTML as an element holding it (a whole page's body, or a fragment in a div)."""
    root = lhtml.fromstring(f"<div>{markup}</div>") if isinstance(markup, str) else lhtml.fromstring(markup)
    body = root.find(".//body")
    return body if body is not None else root


def _core(z):
    """An Office file's title, authors and date (docProps/core.xml)."""
    try:
        root = _xml(z.read("docProps/core.xml"))
    except (KeyError, etree.XMLSyntaxError):
        return {}
    creators = [a.strip() for a in re.split(r"[;\n]", root.findtext(DC + "creator") or "") if a.strip()]
    return {"title": (root.findtext(DC + "title") or "").strip(), "authors": creators,
            "created_at": (root.findtext(DCT + "created") or "").strip()}


# ── Word ──

def _with_math(data):
    """A .docx with its formulas (OMML) as LaTeX in runs of text, which mammoth keeps (it drops OMML)."""
    zin, buf = zipfile.ZipFile(io.BytesIO(data)), io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zout:
        for info in zin.infolist():
            raw = zin.read(info.filename)
            if info.filename in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml") and b"oMath" in raw:
                try:
                    root = _xml(raw)
                    for para in list(root.iter(omml.M + "oMathPara")):
                        tex = "".join(f"{DIN}{omml.latex(m)}{DOUT}" for m in para.findall(omml.M + "oMath"))
                        _replace(para, tex)
                    for m in list(root.iter(omml.M + "oMath")):
                        _replace(m, f"{IN}{omml.latex(m)}{OUT}")
                    raw = etree.tostring(root, xml_declaration=True, encoding="UTF-8", standalone=True)
                except Exception:
                    pass   # its formulas are left out, as mammoth leaves them
            zout.writestr(info, raw)
    buf.seek(0)
    return buf


def _replace(el, text):
    run = etree.Element(W + "r")
    t = etree.SubElement(run, W + "t")
    t.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
    t.text = text
    run.tail = el.tail
    el.getparent().replace(el, run)


def _docx(path, doc):
    import mammoth
    data = Path(path).read_bytes()
    doc.meta.update(_core(zipfile.ZipFile(io.BytesIO(data))))

    def picture(image):
        with image.open() as f:
            return {"src": doc.picture(f.read(), image.content_type) or "", "alt": image.alt_text or ""}

    result = mammoth.convert_to_html(_with_math(data), style_map=STYLE_MAP, convert_image=mammoth.images.img_element(picture))
    Flow(doc, lambda src: src if src.startswith("images/") else None).walk(_fragment(result.value))


# ── PowerPoint ──

def _rels(z, part):
    """A part's relationships: id -> (the part it points to, its kind)."""
    d, f = posixpath.split(part)
    try:
        root = _xml(z.read(f"{d}/_rels/{f}.rels"))
    except KeyError:
        return {}
    return {r.get("Id"): (r.get("Target") if r.get("TargetMode") == "External" else posixpath.normpath(posixpath.join(d, r.get("Target") or "")),
                          (r.get("Type") or "").rsplit("/", 1)[-1]) for r in root.iter(PKG + "Relationship")}


def _shapes(tree, at=None):
    """A slide's shapes in order, groups opened and the newer of two versions taken: (shape, its place or None)."""
    for c in tree:
        tag = _tag(c)
        if tag == "alternatecontent":
            pick = c.find(MC + "Choice")
            pick = pick if pick is not None else c.find(MC + "Fallback")
            yield from _shapes(pick if pick is not None else [], at)
        elif tag == "grpsp":
            yield from _shapes(c, at or _offset(c))
        elif tag in ("sp", "pic", "graphicframe"):
            yield c, at or _offset(c)


def _offset(el):
    off = next((o for o in el.iter(DML + "off")), None)
    return (int(off.get("y", 0)), int(off.get("x", 0))) if off is not None else None


def _runs(p):
    """A paragraph of a slide as Markdown: its runs with their bold and italics, its formulas as LaTeX."""
    runs = []
    for c in p:
        tag = _tag(c)
        if tag in ("r", "fld"):
            pr = c.find(DML + "rPr")
            on = lambda k: pr is not None and pr.get(k) in ("1", "true")   # noqa: E731
            runs.append((c.findtext(DML + "t") or "", on("b"), on("i")))
        elif tag == "br":
            runs.append((" ", False, False))
        elif tag == "m":   # a formula (a14:m), PowerPoint's own
            para = c.find(f".//{omml.M}oMathPara")
            if para is not None:
                runs.append((DIN + omml.latex(para) + DOUT, False, False))
            elif c.find(f".//{omml.M}oMath") is not None:
                runs.append((IN + omml.latex(c.find(f".//{omml.M}oMath")) + OUT, False, False))
        elif tag == "alternatecontent":
            pick = c.find(MC + "Choice")
            maths = [m for m in (pick.iter() if pick is not None else []) if m.tag in (omml.M + "oMathPara", omml.M + "oMath")]
            if maths and maths[0].tag == omml.M + "oMathPara":
                runs.append((DIN + omml.latex(maths[0]) + DOUT, False, False))
            elif maths:
                runs.append((IN + omml.latex(maths[0]) + OUT, False, False))
            else:
                runs += _runs(c.find(MC + "Fallback") if c.find(MC + "Fallback") is not None else [])
    return runs


def _markdown(runs):
    out, i = [], 0
    while i < len(runs):
        j, (_, b, it) = i, runs[i]
        while j < len(runs) and runs[j][1:] == (b, it):
            j += 1
        text = _escape("".join(r[0] for r in runs[i:j]))
        out.append(_wrap("**", _wrap("*", text) if it else text) if b else _wrap("*", text) if it else text)
        i = j
    return "".join(out)


def _slide_text(doc, body, bulleted):
    """A text box's paragraphs: a list where they are bulleted, else paragraphs (and formulas of their own)."""
    items = []

    def flush():
        if items:
            doc.add("list", "\n".join(items))
            items.clear()

    for p in body.iter(DML + "p"):
        text = _markdown(_runs(p)).strip()
        if not text:
            continue
        pr = p.find(DML + "pPr")
        if pr is not None and pr.find(DML + "buNone") is not None:
            bullet = False
        elif pr is not None and (pr.find(DML + "buChar") is not None or pr.find(DML + "buAutoNum") is not None):
            bullet = True
        else:   # as its box has it: a placeholder for the slide's points is bulleted, a text box is not
            bullet = bulleted
        whole = re.fullmatch(f"{DIN}(.*?){DOUT}", text, re.S)
        if bullet and not whole:
            items.append("  " * int(pr.get("lvl", 0) if pr is not None else 0) + "- " + re.sub(f"{DIN}(.*?){DOUT}", r"$\1$", text))
            continue
        flush()
        if whole:
            doc.add("equation", whole.group(1).strip())
        else:
            for k, piece in enumerate(re.split(f"{DIN}(.*?){DOUT}", text, flags=re.S)):
                if piece.strip():
                    doc.add("equation" if k % 2 else "text", piece.strip() if k % 2 else _start(piece.strip()))
    flush()


def _pptx(path, doc):
    z = zipfile.ZipFile(path)
    doc.meta.update(_core(z))
    prels = _rels(z, "ppt/presentation.xml")
    order = [prels.get(s.get(REL + "id"), (None,))[0] for s in _xml(z.read("ppt/presentation.xml")).iter(PML + "sldId")]
    for part in [p for p in order if p and p in z.namelist()]:
        root, rels = _xml(z.read(part)), _rels(z, part)
        tree = root.find(f"{PML}cSld/{PML}spTree")
        shapes = list(_shapes(tree if tree is not None else []))
        kind = lambda s: (s.find(f".//{PML}ph").get("type", "body") if s.find(f".//{PML}ph") is not None else None)   # noqa: E731
        titles = [s for s, _ in shapes if kind(s) in ("title", "ctrTitle")]
        rest = [(s, at) for s, at in shapes if s not in titles and kind(s) not in ("dt", "ftr", "sldNum", "hdr")]
        if all(at for _, at in rest):   # as they are read: top to bottom, then left to right, in bands of a quarter inch
            rest.sort(key=lambda x: (x[1][0] // 228600, x[1][1]))
        for s in titles:
            text = " ".join(_markdown(_runs(p)).strip() for p in s.iter(DML + "p")).strip()
            if text:
                doc.add("paragraph_title", re.sub(f"[{IN}{DIN}](.*?)[{OUT}{DOUT}]", r"$\1$", text), level=2)
        for s, _ in rest:
            tag, ph = _tag(s), kind(s)
            if tag == "sp" and s.find(f"{PML}txBody") is not None:
                _slide_text(doc, s.find(f"{PML}txBody"), bulleted=ph in ("body", "obj"))
            elif tag == "pic":
                blip = next(s.iter(DML + "blip"), None)
                target = rels.get(blip.get(REL + "embed"), (None,))[0] if blip is not None else None
                if target and target in z.namelist():
                    src = doc.picture(z.read(target), Path(target).suffix.lstrip(".").replace("jpg", "jpeg"))
                    if src:
                        doc.add("image", "", image_source=src)
            elif tag == "graphicframe":
                tbl = next(s.iter(DML + "tbl"), None)
                if tbl is not None:
                    doc.add("table", _slide_table(tbl))
        for target, what in rels.values():   # the speaker's notes, after the slide
            if what == "notesSlide" and target in z.namelist():
                for sp in _xml(z.read(target)).iter(PML + "sp"):
                    ph = sp.find(f".//{PML}ph")
                    if ph is not None and ph.get("type") == "body" and sp.find(f"{PML}txBody") is not None:
                        _slide_text(doc, sp.find(f"{PML}txBody"), bulleted=False)
        doc.page()


def _slide_table(tbl):
    rows = []
    head = (tbl.find(f"{DML}tblPr") is not None and tbl.find(f"{DML}tblPr").get("firstRow") in ("1", "true"))
    for n, tr in enumerate(tbl.iter(DML + "tr")):
        cells = []
        for tc in tr.iter(DML + "tc"):
            if tc.get("hMerge") or tc.get("vMerge"):
                continue
            text = "<br>".join(re.sub(f"[{IN}{DIN}](.*?)[{OUT}{DOUT}]", r"$\1$", _markdown(_runs(p)).strip()) for p in tc.iter(DML + "p"))
            span = "".join(f' {a}="{tc.get(b)}"' for a, b in (("colspan", "gridSpan"), ("rowspan", "rowSpan")) if tc.get(b))
            cell = "th" if head and n == 0 else "td"
            cells.append(f"<{cell}{span}>{text}</{cell}>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    return "<table>" + "".join(rows) + "</table>"


# ── tables: Excel, CSV ──

def _cell(v):
    if v is None:
        return ""
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else f"{v:.10g}"
    if hasattr(v, "isoformat"):
        s = v.isoformat()
        return s[:10] if s.endswith("T00:00:00") or len(s) == 10 else s.replace("T", " ")
    return str(v).strip()


def _grid(rows):
    """Rows of values as an HTML table: empty rows and columns at the edges left out, the first row its head; one
    longer than MAX_ROWS ends in a row of "…"."""
    grid = [[_cell(v) for v in r][:MAX_COLS] for r in rows]
    grid = [r for r in grid if any(r)]
    if not grid:
        return ""
    width = max(max((i + 1 for i, v in enumerate(r) if v), default=0) for r in grid)
    more = len(grid) > MAX_ROWS
    grid = [r[:width] + [""] * (width - len(r[:width])) for r in grid[:MAX_ROWS]]
    head = "<tr>" + "".join(f"<th>{html.escape(v)}</th>" for v in grid[0]) + "</tr>"
    body = "".join("<tr>" + "".join(f"<td>{html.escape(v)}</td>" for v in r) + "</tr>" for r in grid[1:])
    if more:
        body += "<tr>" + "".join("<td>…</td>" for _ in range(width)) + "</tr>"
    return f"<table><thead>{head}</thead><tbody>{body}</tbody></table>"


def _xlsx(path, doc):
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    p = wb.properties
    doc.meta.update({"title": p.title or "", "authors": [p.creator] if p.creator else [],
                     "created_at": p.created.isoformat() if p.created else ""})
    sheets = []
    for ws in wb.worksheets:
        rows = []
        for r in ws.iter_rows(values_only=True):
            rows.append(r)
            if len(rows) > MAX_ROWS * 2:   # (blank rows among them are left out later)
                break
        table = _grid(rows)
        if table:
            sheets.append((ws.title, table))
    for name, table in sheets:
        if len(sheets) > 1:
            doc.add("paragraph_title", _escape(name), level=2)
        doc.add("table", table)
    wb.close()


def _decode(data):
    for enc in ("utf-8-sig", "gb18030", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def _csv(path, doc, ext):
    text = _decode(Path(path).read_bytes())
    if ext == "tsv":
        dialect = csv.excel_tab
    else:
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
    rows = []
    for r in csv.reader(io.StringIO(text), dialect):
        rows.append(r)
        if len(rows) > MAX_ROWS * 2:
            break
    table = _grid(rows)
    if table:
        doc.add("table", table)


# ── web pages (.html, .mhtml) and e-books ──

def _page(data, doc, files=None, url=None):
    """A saved web page: its article (trafilatura's, as on the web), and its pictures, from inside the file when it
    holds them (files: address -> bytes), else from where they are on the web."""
    import warnings
    warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")
    import trafilatura
    from . import webpage
    tree = webpage.visible(data, url or "file:///page.html")   # (a picture beside the file cannot be had)
    m = trafilatura.extract_metadata(tree)
    doc.meta.update({"title": (m.title if m and m.title else "") or "", "created_at": (m.date if m and m.date else "") or "",
                     "authors": [a.strip() for a in re.split(r";|\band\b", m.author) if a.strip()] if m and m.author else []})
    files = files or {}

    def resolve(src):
        if src in files:
            return doc.picture(*files[src])
        return src if re.match(r"https?://|data:image/", src, re.I) else None

    # its article, where it marks one (the longest); else its main part; else its body, without the site's
    # header, footer and side columns
    arts = [a for a in tree.iter("article")]
    root = max(arts, key=lambda a: len(a.text_content())) if arts else next(tree.iter("main"), None)
    if root is None:
        root = tree.find(".//body") if tree.find(".//body") is not None else tree
        for el in [e for e in root.iter("header", "footer", "aside") if e.getparent() is not None]:
            el.drop_tree()
    h1s = list(root.iter("h1"))
    if len(h1s) == 1:   # its one top heading: its title
        h1s[0].set("class", "tf-title")
    Flow(doc, resolve).walk(root)


def _mhtml(path, doc):
    msg = email.message_from_bytes(Path(path).read_bytes())
    page, files, url = None, {}, None
    for part in msg.walk():
        kind = part.get_content_type()
        body = part.get_payload(decode=True)
        if body is None:
            continue
        if kind == "text/html" and page is None:
            page, url = body.decode(part.get_content_charset() or "utf-8", "replace"), part.get("Content-Location")
        elif kind.startswith("image/") and part.get("Content-Location"):
            files[part.get("Content-Location")] = (body, kind)
    if page is None:
        raise ValueError("bad-file")
    _page(page, doc, files, url if url and re.match(r"https?://", url) else None)


def _epub(path, doc):
    z = zipfile.ZipFile(path)
    rootfile = _xml(z.read("META-INF/container.xml")).find(".//{urn:oasis:names:tc:opendocument:xmlns:container}rootfile")
    opf_path = rootfile.get("full-path")
    opf, base = _xml(z.read(opf_path)), posixpath.dirname(opf_path)
    doc.meta.update({"title": (opf.findtext(f".//{DC}title") or "").strip(),
                     "authors": [c.text.strip() for c in opf.iter(DC + "creator") if c.text and c.text.strip()],
                     "created_at": (opf.findtext(f".//{DC}date") or "").strip()})
    items = {i.get("id"): i for i in opf.iter(OPF + "item")}
    for ref in opf.iter(OPF + "itemref"):
        item = items.get(ref.get("idref"))
        if item is None or ref.get("linear") == "no" or "nav" in (item.get("properties") or "").split():
            continue
        part = posixpath.normpath(posixpath.join(base, urllib.parse.unquote(item.get("href") or "")))
        if part not in z.namelist():
            continue
        here = posixpath.dirname(part)

        def resolve(src, here=here):
            target = posixpath.normpath(posixpath.join(here, urllib.parse.unquote(src.split("#")[0])))
            if target in z.namelist():
                return doc.picture(z.read(target), Path(target).suffix.lstrip(".").replace("jpg", "jpeg"))
            return src if re.match(r"https?://", src) else None

        Flow(doc, resolve).walk(_fragment(_decode(z.read(part))))
        doc.page()


def _textutil(path, doc):
    """A .doc, .rtf or .odt as macOS's textutil writes it in HTML: its text, its tables, its bold and italics (not its
    pictures or formulas). Its headings have no tags there, only larger type: a short paragraph set larger than the
    text's own size is a heading, the largest the top one."""
    page = subprocess.run(["textutil", "-convert", "html", "-stdout", str(path)], check=True, capture_output=True, timeout=120).stdout
    root = lhtml.fromstring(page)
    size = {m.group(1): float(m.group(2)) for m in re.finditer(r"p\.(p\d+)\s*\{[^}]*?font:[^};]*?([\d.]+)px", page.decode("utf-8", "replace"))}
    used = {}
    for p in root.iter("p"):
        used[p.get("class")] = used.get(p.get("class"), 0) + len(p.text_content())
    body = size.get(max(used, key=used.get)) if used else None
    bigger = sorted({v for v in size.values() if body and v > body + 0.5}, reverse=True)
    for p in root.iter("p"):
        v = size.get(p.get("class"))
        if v in bigger and 0 < len(p.text_content().split()) <= 20 and p.find(".//table") is None:
            p.tag = f"h{min(3, bigger.index(v) + 1)}"
    body_el = root.find(".//body")
    Flow(doc, lambda src: None).walk(body_el if body_el is not None else root)


def save(pid, file, name):
    """A file read by TextFold itself and stored as a document (pdfdoc.store_pdf, as the engine's are), the file kept
    beside it. ValueError("bad-file") when it cannot be read."""
    from . import pdfdoc, store
    with tempfile.TemporaryDirectory() as tmp:
        out = convert(file, Path(tmp) / "out")
        pdfdoc.store_pdf(pid, out, pdfdoc.read_output(out), name, "")
    kept = store.pdir(pid) / pdfdoc.kept_name(pid, file)
    if not kept.exists():
        kept.write_bytes(Path(file).read_bytes())
    return pid


def convert(file, out, ext=None):
    """A file read into out/ (structured_content.json, images/), as the engine would write it. ValueError("bad-file")
    when it cannot be read."""
    from . import engine
    ext = ext or engine.kind_of(str(file))
    out = Path(out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    doc = Doc(out, ext)
    try:
        if ext == "docx":
            _docx(file, doc)
        elif ext == "pptx":
            _pptx(file, doc)
        elif ext == "xlsx":
            _xlsx(file, doc)
        elif ext in ("csv", "tsv"):
            _csv(file, doc, ext)
        elif ext in ("html", "htm"):
            _page(Path(file).read_bytes(), doc)
        elif ext in ("mhtml", "mht"):
            _mhtml(file, doc)
        elif ext == "epub":
            _epub(file, doc)
        elif ext in TEXTUTIL and reads(ext):
            _textutil(file, doc)
        else:
            raise ValueError("bad-file")
    except ValueError:
        raise
    except Exception as e:   # a damaged file, an encrypted one, one of a kind it only seems to be
        raise ValueError("bad-file") from e
    doc.write()
    if not any(p["blocks"] for p in doc.pages):
        raise ValueError("bad-file")
    return out
