"""Any web page: the article on it (the text a reader came for, without the site's menus, adverts and footers), read
with trafilatura as Markdown and stored through the Markdown path, with its pictures. Kept as a snapshot of the day it
was read. Text the page hides from its readers (display: none, aria-hidden, screen-reader classes) is removed first:
it is where words meant for a model rather than a person are put.

A page that needs its scripts to show its text, or a sign-in, gives too little to read (web-notext); a site that
refuses the request, web-blocked; a link to a PDF, web-pdf (the file is dropped on the landing instead).

Its id: web-<host>-<the first 12 hex digits of the address's SHA-256>; the address waits in url.txt until the fetch."""
import hashlib
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from . import local, markdown, store
from .fetch import UA

LIMIT = 8 * 2**20   # no article's page is larger
MIN_WORDS = 80      # fewer: a page that shows its text with scripts, or behind a sign-in
HIDDEN_STYLE = re.compile(r"display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:\.0+)?(?:px|pt|em|rem|%)?\s*(?:;|$|!)"
                          r"|opacity\s*:\s*0(?:\.0+)?\s*(?:;|$|!)", re.I)
HIDDEN_CLASS = re.compile(r"(?:^|\s)(?:sr-only|visually-hidden|screen-reader-text|screenreader-only|hidden|d-none|is-hidden)(?:\s|$)")


def pid_of(url):
    host = re.sub(r"^www\.", "", (urllib.parse.urlparse(url).hostname or "page").lower())
    host = re.sub(r"[^a-z0-9]+", "-", host).strip("-")[:40] or "page"
    return f"web-{host}-{hashlib.sha256(url.encode()).hexdigest()[:12]}"


def register(url):
    """A page's id (store.resolve), its address kept for the fetch."""
    url = url.strip()
    pid = pid_of(url)
    d = store.pdir(pid)
    if not (d / "meta.json").exists():
        d.mkdir(parents=True, exist_ok=True)
        (d / "url.txt").write_text(url, encoding="utf-8")
    return pid


def _get(url):
    req = urllib.request.Request(url, headers={**UA, "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            if ctype == "application/pdf" or r.url.lower().split("?")[0].endswith(".pdf"):
                raise ValueError("web-pdf")
            if ctype and "html" not in ctype and "xml" not in ctype:
                raise ValueError("web-notext")
            data = r.read(LIMIT + 1)
            return data[:LIMIT], r.url, r.headers.get_content_charset()
    except urllib.error.HTTPError as e:
        if e.code in (401, 402, 403, 407, 429, 451, 503):   # a sign-in, a paywall, a wall against programs
            raise ValueError("web-blocked") from None
        raise


def visible(page, url):
    """The page as its reader sees it: hidden elements removed, links and pictures as full addresses."""
    from lxml import html as lhtml
    root = lhtml.fromstring(page)
    root.make_links_absolute(url, resolve_base_href=True)
    gone = [el for el in root.iter() if isinstance(el.tag, str) and (
        el.get("hidden") is not None or el.get("aria-hidden") == "true" or HIDDEN_STYLE.search(el.get("style") or "")
        or HIDDEN_CLASS.search(el.get("class") or "") or el.tag in ("template", "noscript"))]
    for el in gone:
        if el.getparent() is not None:
            el.drop_tree()
    return root


def _clean(md, title):
    md = re.sub(r"!\[[^\]]*\]\(\s*\)", "", md)                      # pictures without an address (a layout's spacers)
    lines = [ln for ln in md.splitlines() if not re.fullmatch(r"\s*\|[\s|]*", ln)]   # a layout table's empty cells
    # the title again, as the article's first heading
    for i, ln in enumerate(lines):
        if ln.strip():
            m = re.match(r"^#+\s+(.*)$", ln)
            if m and re.sub(r"\W+", " ", m.group(1)).strip().lower() == re.sub(r"\W+", " ", title).strip().lower():
                del lines[i]
            break
    return "\n".join(lines).strip() + "\n"


def fetch(pid, progress=None):
    """Store a web page's article (once)."""
    import warnings
    # (macOS's own Python is built with LibreSSL; urllib3, which trafilatura brings, says so on import, harmlessly)
    warnings.filterwarnings("ignore", message="urllib3 v2 only supports OpenSSL")
    import trafilatura
    d = store.pdir(pid)
    if (d / "meta.json").exists() and (d / "source.html").exists():
        return pid
    url = (d / "url.txt").read_text(encoding="utf-8").strip()
    data, final, charset = _get(url)
    page = data.decode(charset or "utf-8", "replace") if charset else data
    tree = visible(page, final)
    md = trafilatura.extract(tree, url=final, output_format="markdown", include_images=True, include_links=True,
                             include_tables=True, include_formatting=True, favor_recall=True) or ""
    if len(md.split()) < MIN_WORDS:
        raise ValueError("web-notext")
    m = trafilatura.extract_metadata(tree, default_url=final)
    host = re.sub(r"^www\.", "", urllib.parse.urlparse(final).hostname or "")
    site = (m.sitename if m and m.sitename else "") or host
    title = (m.title if m and m.title else "") or host
    for name in {site, host}:   # "How to Do Great Work - Paul Graham": the site's name off the end
        title = re.sub(rf"\s*[-|–—·:]\s*{re.escape(name)}\s*$", "", title, flags=re.I) if name else title
    md = _clean(md, title)
    html, _ = markdown.to_html(md)
    authors = [a.strip() for a in re.split(r";|\band\b", (m.author if m and m.author else "")) if a.strip()]
    meta = {"id": pid, "version": "", "title": title.strip() or host, "authors": authors[:12], "date": (m.date if m and m.date else "") or "",
            "snapshot": time.strftime("%Y-%m-%d"), "abs_url": final, "html_url": "",
            "license": m.license if m and m.license and re.match(r"https?://", m.license) else "",
            "source": {"kind": "web", "site": site, "url": final}, "label": site}
    pre = local.prefetch(re.findall(r'<img[^>]*\ssrc="([^"]+)"', html))
    return local.store_doc(pid, html, meta, "source.md", md, lambda folder: local.Images(folder, None, pre=pre).resolve, sizes=True)
