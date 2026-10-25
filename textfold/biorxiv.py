"""Preprints from bioRxiv and medRxiv, read from their own JATS XML.

Europe PMC holds the full text of only some of them (about 20,000 of bioRxiv's 350,000), so a preprint it does not
hold is read from the server itself: its API (api.biorxiv.org) gives each version's XML. The newest version's XML
can be the abstract alone while its full text is being prepared; then the newest one with a body is read.
Their XML draws tables and display formulas as pictures, which the page shows as such.

  10.1101/2024.01.15.575678 · 10.64898/2026.09.24.754278 (openRxiv's prefix since 2026)
  https://www.biorxiv.org/content/10.64898/2026.09.24.754278v1 · the same on medrxiv.org

Stored as papers/biorxiv-<suffix>/ or papers/medrxiv-<suffix>/. A request for the XML and one for each image, with a
User-Agent of its own (the servers turn away the generic ones), and seven seconds apart, as their robots.txt asks
(Crawl-delay: 7); faster, their firewall shuts the door for a while. A paper with many pictures takes a minute or two.
"""
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from . import epmc, jats, local, store
from .fetch import UA

API = "https://api.biorxiv.org/details/{server}/{doi}"
SERVERS = ("biorxiv", "medrxiv")
PREFIXES = ("10.1101", "10.64898")
DOI = re.compile(r"\b(10\.(?:1101|64898)/[\w.]+?)(?:v\d+)?(?:\.full(?:-text|\.pdf)?|\.abstract|\.supplementary-material)?(?=$|[/?#\s])")
DELAY = 7          # seconds between requests to www.biorxiv.org and www.medrxiv.org (their robots.txt)
_pace = {"last": 0.0, "lock": threading.Lock()}
LICENSES = {"cc_by": "by/4.0", "cc_by_nc": "by-nc/4.0", "cc_by_nd": "by-nd/4.0", "cc_by_nc_nd": "by-nc-nd/4.0"}


def ref_of(ref):
    """(server or None, DOI) for a bioRxiv or medRxiv link or DOI; (None, None) for anything else."""
    ref = urllib.parse.unquote((ref or "").strip())
    m = DOI.search(ref)
    if not m:
        return None, None
    host = re.search(r"(biorxiv|medrxiv)\.org", ref, re.I)
    return (host.group(1).lower() if host else None), m.group(1)


def pid_of(server, doi):
    return f"{server}-{doi.split('/', 1)[1]}"


def _get(url, timeout=60):
    if re.match(r"https?://(www\.)?(biorxiv|medrxiv)\.org/", url):
        with _pace["lock"]:
            time.sleep(max(0.0, _pace["last"] + DELAY - time.time()))
            _pace["last"] = time.time()
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read(), r.geturl()


def versions(server, doi):
    data, _ = _get(API.format(server=server, doi=doi), 30)
    return sorted(json.loads(data).get("collection") or [], key=lambda r: int(r.get("version") or 0))


def find(doi, server=None):
    """The server that holds a DOI, and its versions; ValueError("no-fulltext") when neither does."""
    for s in ([server] if server else SERVERS):
        vs = versions(s, doi)
        if vs:
            return s, vs
    raise ValueError("no-fulltext")


def image_resolver(content, folder, progress=None, total=0):
    """A figure's or table's "F1.large.jpg" beside the XML (…/content/biorxiv/early/<date>/<suffix>/); any other
    picture ("embed/graphic-6.gif") where the site keeps them (…/sites/default/files/highwire/biorxiv/early/…)."""
    images = local.Images(folder, None, get=_get)

    def resolve(src):
        if progress:
            progress("fetch", len(images.seen), total)
        if src.startswith("embed/"):
            src = re.sub(r"/content/(biorxiv|medrxiv)/", r"/sites/default/files/highwire/\1/", content) + "/" + src
        elif not re.match(r"https?://", src):
            src = content + "/" + src
        return images.resolve(src)
    return resolve


def _from_europepmc(pid, server, rec):
    """The preprint as Europe PMC holds it (its full text converted from the PDF), stored under this preprint's id."""
    name = "bioRxiv" if server == "biorxiv" else "medRxiv"
    refused = RuntimeError(f"{name} refused this computer for now (its firewall), and Europe PMC holds no full text of this "
                           "preprint. Try again later.")
    try:
        eid = epmc.by_doi(rec["doi"])
        return epmc.fetch(pid, eid, {"abs_url": f"https://www.{server}.org/content/{rec['doi']}v{rec.get('version', '')}",
                                     "label": f"{name} {rec['doi']} · Europe PMC {eid.upper()}", "doi": rec["doi"]})
    except (ValueError, RuntimeError):   # no full text there either (some it lists are PDFs only, answered with a 500)
        raise refused


def _has_body(xml):
    """The XML of a version whose full text is not ready yet holds the abstract and an empty body."""
    m = re.search(r"<body\b[^>]*>(.*?)</body>", xml, re.S)
    return bool(m) and (len(re.findall(r"<sec\b", m.group(1))) > 0 or len(re.findall(r"<p\b", m.group(1))) >= 3)


def fetch(pid, progress=None):
    """Store a preprint from bioRxiv or medRxiv (once). Its id: biorxiv-… or medrxiv-…. progress(stage, done, total)
    follows the images."""
    if store.readable(pid):
        return pid
    server, suffix = pid.split("-", 1)
    found = None
    for prefix in PREFIXES:
        vs = versions(server, f"{prefix}/{suffix}")
        if vs:
            found = vs
            break
    if not found:
        raise RuntimeError("no-fulltext")
    for rec in reversed(found):   # the newest version whose XML has a body
        if not rec.get("jatsxml"):
            continue
        try:
            xml, final = _get(rec["jatsxml"])
        except urllib.error.HTTPError as e:
            if e.code == 403:   # the site's firewall turned this computer away: Europe PMC's copy, if it holds one
                return _from_europepmc(pid, server, found[-1])
            continue
        xml = xml.decode("utf-8", "replace")
        if _has_body(xml):
            break
    else:
        raise RuntimeError("no-fulltext")
    html, m = jats.to_html(xml)
    lic = LICENSES.get(rec.get("license", ""))
    name = "bioRxiv" if server == "biorxiv" else "medRxiv"
    meta = {"id": pid, "version": f"v{rec.get('version', '')}", "title": m["title"] or rec.get("title", ""),
            "authors": m["authors"] or [a.strip() for a in (rec.get("authors") or "").split(";") if a.strip()],
            "date": rec.get("date", ""), "doi": rec["doi"],
            "abs_url": f"https://www.{server}.org/content/{rec['doi']}v{rec.get('version', '')}", "html_url": "",
            "license": m["license"] or (f"https://creativecommons.org/licenses/{lic}/" if lic else ""),
            "source": {"kind": server, "name": rec["doi"]}, "label": f"{name} {rec['doi']}v{rec.get('version', '')}"}
    content = re.sub(r"\.source\.xml$", "", final)
    total = len(set(re.findall(r'<img src="([^"]+)"', html)))
    return local.store_doc(pid, html, meta, "xml", xml, lambda folder: image_resolver(content, folder, progress, total))
