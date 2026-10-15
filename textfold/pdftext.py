"""PDFs that carry their own words (typeset, or exported from Word or LaTeX), read by TextFold itself when the PDF
engine is not installed. pdf-inspector (Firecrawl's, a Rust library of a few MB, with no models) tells whether the
pages have their text in them and writes each page as Markdown; its headings, paragraphs, lists and tables become
blocks in the shape the engine writes (structured_content.json), so that pdfdoc stores them as it stores the engine's.

A scan, or a PDF whose text cannot be read (a broken font encoding), still needs the engine: its pages are pictures,
read by a model. What this reading leaves out, the engine gives: the figures (their captions stay), formulas as LaTeX,
each paragraph's place on its page for the side-by-side view. A PDF read this way can be converted accurately later,
once the engine is installed (its tier is "text", not "accurate")."""
import html
import json
import re
import shutil
import tempfile
from pathlib import Path

from .pdfdoc import ABSTRACT, NUMBERED, REFS, depth, plausible

TIER = "text"
HEADING = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
LIST = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+\S")
# words set in bold or italics at the start of a line: a heading run into its paragraph ("**3. Methodology** The ..."),
# in pieces when the line broke inside it ("*2.1. Evolution of ... toward high-precision* *KGs* Bridge ...")
LEAD = re.compile(r"^((?:(\*{1,2})[^*\n]+?\2[\s\u2013\u2014-]*)+)(.*)$")
# a numbered heading ("3", "3.2.", "A.1", "IV."; not "A" as a title's first word): not joined to the one before
NUMBER = re.compile(r"^\s*(?:\d+(?:\.\d+)*\.?|[A-Z](?:\.\d+)+\.?|[A-Z]\.|[IVX]+\.)\s+\S")
PAGE_NO = re.compile(r"^\W*\d{1,4}\W*$")


def _plain(s):
    return re.sub(r"\s+", " ", re.sub(r"\*+", "", s)).strip()


def _join(lines):
    """A paragraph's lines as one, the words a line's end cut in two whole again ("communica- tion"; "OCR- free" keeps
    its hyphen)."""
    text = " ".join(x.strip() for x in lines if x.strip())
    return re.sub(r"(?<=[A-Za-z0-9])- (?=[a-z])", "-", re.sub(r"(?<=[a-z])- (?=[a-z])", "", text))


def _heading_of(s):
    """A line's leading bold or italic words when they are a heading (numbered, the abstract's, the references') and
    the rest: "**Abstract.** Engineering drawings ..." is the heading Abstract and its paragraph."""
    m = LEAD.match(s.strip())
    if not m:
        return None, s
    head = _plain(m.group(1))
    if ABSTRACT.match(head.rstrip(".:")) or REFS.match(head.rstrip(".:")):
        return head.rstrip(".:"), m.group(3)
    if NUMBERED.match(head):
        return head, m.group(3)
    return None, s


def blocks_of(md):
    """A page's Markdown as the engine's blocks: headings, paragraphs, lists, tables, code."""
    out, para = [], []

    def text(lines):
        if lines and _join(lines):
            out.append({"type": "text", "content": _join(lines)})

    def flush():
        lines = [x for x in para if x.strip()]
        para.clear()
        if not lines:
            return
        if all(x.lstrip().startswith("|") for x in lines):
            out.append({"type": "table", "content": "\n".join(x.strip() for x in lines)})
            return
        if LIST.match(lines[0]) and all(LIST.match(x) or x.startswith((" ", "\t")) for x in lines):
            out.append({"type": "list", "content": "\n".join(x.rstrip() for x in lines)})
            return
        run = []
        for line in lines:
            head, rest = _heading_of(line)
            if head is None:
                run.append(line)
                continue
            text(run)
            run = [rest] if rest.strip() else []
            out.append({"type": "paragraph_title", "content": head, "level": depth(head) + 1})
        text(run)

    lines = md.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("```"):
            flush()
            j = next((k for k in range(i + 1, len(lines)) if lines[k].startswith("```")), len(lines))
            code = "\n".join(lines[i + 1:j]).strip("\n")
            if code.strip():
                out.append({"type": "code", "content": code})
            i = j + 1
            continue
        m = HEADING.match(line)
        if m:
            flush()
            out.append({"type": "paragraph_title", "content": _plain(m.group(2)), "level": len(m.group(1))})
        elif not line.strip():
            flush()
        else:
            para.append(line)
        i += 1
    flush()
    return _joined_headings(out)


def _joined_headings(blocks):
    """A heading the page broke over two lines, set as two ("... from construction to refinement with", "LLMs and
    GNNs"): one. A numbered heading, the abstract's or the references' stay their own."""
    out = []
    for b in blocks:
        prev = out[-1] if out else None
        if (prev is not None and b["type"] == prev["type"] == "paragraph_title" and not NUMBER.match(b["content"])
                and (b.get("level") == prev.get("level") or prev["content"].endswith("-"))   # ("LARGE LAN-", "GUAGE MODELS")
                and not any(p.match(x["content"]) for p in (ABSTRACT, REFS) for x in (b, prev))):
            prev["content"] = re.sub(r"(?<=\w)-$", "", prev["content"]) + ("" if prev["content"].endswith("-") else " ") + b["content"]
            continue
        out.append(b)
    return out


def _lead_key(b):
    """What a page's first block begins with, as a running head would (its page number aside), or None."""
    if b.get("type") != "text":
        return None
    m = LEAD.match(b["content"])
    s = m.group(1) if m else b["content"]
    return re.sub(r"\d+", "#", _plain(s)) if len(s.split()) <= 20 else None


def _running_heads(pages):
    """The running head over most pages ("X. Li et al. Advanced Engineering Informatics 76 (2026) 105034"), out:
    pdf-inspector leaves some, run into the page's first paragraph. A page number alone is marked as one."""
    keys = [_lead_key(p["blocks"][0]) if p["blocks"] else None for p in pages]
    counts = {k: keys.count(k) for k in keys if k}
    for p, k in zip(pages, keys):
        if k and counts[k] >= max(3, len(pages) // 2):
            b = p["blocks"][0]
            m = LEAD.match(b["content"])
            rest = m.group(3).strip() if m and re.sub(r"\d+", "#", _plain(m.group(1))) == k else ""
            if rest:
                b["content"] = rest
            else:
                p["blocks"].pop(0)
        for b in p["blocks"]:
            if b["type"] == "text" and PAGE_NO.match(b["content"]):
                b["type"] = "page_number"


def _words(s):
    return set(re.findall(r"\w+", s.lower()))


def _title(pages, known=""):
    """The paper's title: among the headings on its first page before its first section, the one the file's own
    title (known) names, else none (pdfdoc takes the file's); without one, the largest, the longest of those. The
    others there (the journal's name, the authors') are front matter, read as text."""
    if not pages:
        return
    heads = []
    for b in pages[0]["blocks"]:
        if b["type"] == "paragraph_title":
            if NUMBER.match(b["content"]) or ABSTRACT.match(b["content"]) or REFS.match(b["content"]):
                break
            heads.append(b)
    if heads:
        kw = _words(known) if plausible(known) else set()
        named = [b for b in heads if kw and len(_words(b["content"]) & kw) >= 0.6 * len(_words(b["content"]) | kw)]
        title = named[0] if named else None if kw else min(heads, key=lambda b: (b.get("level") or 1, -len(b["content"].split())))
        for b in heads:
            b["type"] = "doc_title" if b is title else "text"


def structure(pages, title=""):
    """The engine's structured content from the pages' Markdown, [(page index, markdown)], and the title the file
    gives itself (often none, or its code name)."""
    out = [{"page_idx": n, "blocks": blocks_of(md)} for n, md in pages]
    _running_heads(out)
    _title(out, title)
    return {"pages": out, "metadata": {"file_suffix": "pdf", "producer": {"name": "pdf-inspector"},
                                       "document": {"title": title} if title else {}}}


def read(file):
    """A PDF's pages as ([(page index, markdown)], its own title) when they have their words in them; None when the
    engine is needed (a scan, pictures of pages, a broken font encoding). ValueError("bad-file") when it is no PDF
    pdf-inspector can open."""
    import pdf_inspector
    try:
        kind = pdf_inspector.detect_pdf(str(file))
        if kind.pdf_type != "text_based" or kind.pages_needing_ocr or kind.has_encoding_issues:
            return None
        result = pdf_inspector.extract_pages_markdown(str(file))
    except Exception as e:   # a damaged file, an encrypted one
        raise ValueError("bad-file") from e
    if result.pages_needing_ocr or not any(p.markdown.strip() for p in result.pages):
        return None
    return [(p.page, p.markdown) for p in result.pages], html.unescape(kind.title or "").strip()


def convert(file, out):
    """A PDF read into out/ (structured_content.json), as the engine would write it; None when it needs the engine."""
    got = read(file)
    if got is None:
        return None
    sc = structure(*got)
    if not any(p["blocks"] for p in sc["pages"]):
        return None
    out = Path(out)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    (out / "structured_content.json").write_text(json.dumps(sc, ensure_ascii=False))
    return out


def save(pid, file, name):
    """A PDF read by TextFold itself and stored as a document (pdfdoc.store_pdf, as the engine's are), the file kept
    beside it as source.pdf; False when it needs the engine (or pdf-inspector cannot open it: the engine may)."""
    from . import pdfdoc, store
    with tempfile.TemporaryDirectory() as tmp:
        try:
            out = convert(file, Path(tmp) / "out")
        except ValueError:
            return False
        if out is None:
            return False
        pdfdoc.store_pdf(pid, out, pdfdoc.read_output(out), name, TIER)
    kept = store.pdir(pid) / "source.pdf"
    if not kept.exists():
        kept.write_bytes(Path(file).read_bytes())
    return True
