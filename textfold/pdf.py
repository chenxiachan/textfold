"""A paper's original typeset pages, for the reader's side-by-side view: arXiv's PDF of the version read, the
publisher's PDF that PubMed Central keeps in its open data, a bioRxiv or medRxiv preprint's own, or (for a preprint read
through Europe PMC) the preprint server's. Fetched once, when the reader first asks, and kept beside the paper as
source.pdf; a PDF read through the PDF engine (pdfdoc.py) keeps the file itself there. Wikipedia articles and Markdown
files have none. None of these servers lets a page read the file itself (no CORS), and their article pages may not be
framed, so the app fetches it."""
import json
import re
import threading
import urllib.error
import urllib.request

from . import store
from .fetch import UA

LIMIT = 100 * 2**20       # no paper's PDF is larger; anything that is, is not one
_lock = threading.Lock()  # two requests for the same paper download it once


def candidates(pid, meta):
    """Where the PDF may be, in order."""
    if re.fullmatch(r"\d{4}\.\d{4,5}", pid):
        yield f"https://arxiv.org/pdf/{pid}{meta.get('version') or ''}"
    elif re.fullmatch(r"pmc\d+", pid):
        n = pid[3:]
        for v in (1, 2, 3):   # the open data's versions of an article
            yield f"https://pmc-oa-opendata.s3.amazonaws.com/PMC{n}.{v}/PMC{n}.{v}.pdf"
    elif re.fullmatch(r"(biorxiv|medrxiv)-[0-9.]+", pid):
        if meta.get("abs_url"):
            yield f"{meta['abs_url']}.full.pdf"
    elif re.fullmatch(r"ppr\d+", pid):   # Europe PMC's own PDF link is only good on its page: the preprint server's
        doi = meta.get("doi") or ""
        if re.match(r"10\.(1101|64898)/", doi):
            yield f"https://www.biorxiv.org/content/{doi}.full.pdf"
            yield f"https://www.medrxiv.org/content/{doi}.full.pdf"
        m = re.match(r"10\.21203/rs\.3\.(rs-\d+)/(v\d+)", doi)
        if m:
            yield f"https://www.researchsquare.com/article/{m.group(1)}/{m.group(2)}.pdf"


def has_source(pid):
    """Whether the paper is of a kind that can have one (the reader shows its button only then)."""
    return bool(re.fullmatch(r"\d{4}\.\d{4,5}|pmc\d+|ppr\d+|(biorxiv|medrxiv)-[0-9.]+|pdf-[0-9a-f]{16}", pid))


def path(pid):
    """The paper's PDF on disk, fetched now if it is not there yet; None if there is none to fetch."""
    d = store.pdir(pid)
    f = d / "source.pdf"
    if f.exists():
        return f
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    with _lock:
        if f.exists():
            return f
        for url in candidates(pid, meta):
            try:
                with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
                    data = r.read(LIMIT + 1)
            except (urllib.error.URLError, OSError):
                continue
            if data[:5] == b"%PDF-" and len(data) <= LIMIT:
                tmp = f.with_suffix(".part")
                tmp.write_bytes(data)
                tmp.replace(f)
                return f
    return None


def where(pid):
    """Where a reader can open the PDF themselves when the app cannot fetch it (medRxiv refuses programs): its first
    address, else the paper's page."""
    meta = json.loads((store.pdir(pid) / "meta.json").read_text(encoding="utf-8"))
    return next(candidates(pid, meta), None) or meta.get("abs_url") or None


def keep(pid, data):
    """The reader's own copy of the paper's PDF, dropped on the pane: kept as its source.pdf. False if it is not one."""
    if data[:5] != b"%PDF-" or len(data) > LIMIT:
        return False
    f = store.pdir(pid) / "source.pdf"
    tmp = f.with_suffix(".part")
    tmp.write_bytes(data)
    tmp.replace(f)
    return True
