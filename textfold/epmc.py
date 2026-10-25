"""Papers from Europe PMC: PubMed Central's open-access articles, and the preprints whose full text it holds (bioRxiv,
medRxiv, Research Square and others), as JATS XML read by jats.py.

  PMC7778976 · europepmc.org/article/PMC/PMC7778976 · pmc.ncbi.nlm.nih.gov/articles/PMC7778976    an article
  PPR1212834 · europepmc.org/article/PPR/PPR1212834                                                 a preprint
  10.1093/nar/gkaa994 · doi.org/10.1093/nar/gkaa994                                                 found by its DOI

One request a paper (its XML) and one an image, through Europe PMC's public REST API, which asks for nothing more
than a polite User-Agent. Stored as papers/pmc7778976/ or papers/ppr1212834/, like any document (local.py).
"""
import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import jats, local, store
from .fetch import UA

API = "https://www.ebi.ac.uk/europepmc/webservices/rest"
PMC = re.compile(r"\b(PMC\d{4,9})\b", re.I)
PPR = re.compile(r"\b(PPR\d{4,9})\b", re.I)
DOI = re.compile(r"\b(10\.\d{4,9}/[^\s?#]+)")


def ref_of(ref):
    """The id a reference names, when Europe PMC is where it is read: ("pmc7778976" | "ppr1212834" | None, a DOI or None)."""
    ref = (ref or "").strip()
    m = PMC.search(ref) or PPR.search(ref)
    if m:
        return m.group(1).lower(), None
    m = DOI.search(urllib.parse.unquote(ref))
    if m and not re.match(r"10\.48550/", m.group(1), re.I):   # arXiv's own DOIs are read from arXiv
        return None, m.group(1).rstrip(".")
    return None, None


def _get(url, timeout=60):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read()


def by_doi(doi):
    """The Europe PMC id of a DOI's open full text; ValueError("no-fulltext") when it has none."""
    q = urllib.parse.urlencode({"query": f'DOI:"{doi}"', "format": "json", "resultType": "core", "pageSize": 5})
    hits = json.loads(_get(f"{API}/search?{q}", 30))["resultList"]["result"]
    for r in hits:
        for fid in (r.get("fullTextIdList") or {}).get("fullTextId", []):
            if r.get("isOpenAccess") == "Y" and (PMC.fullmatch(fid) or PPR.fullmatch(fid)):
                return fid.lower()
    raise ValueError("no-fulltext")


def image_resolver(pid, folder):
    """Absolute image URLs (PubMed Central's CDN) as they are; a preprint's figure (a bare file name) from Europe PMC's
    file service."""
    images = local.Images(folder, None)

    def resolve(src):
        if pid.startswith("ppr") and not re.match(r"https?://", src):
            name = src if re.search(r"\.(jpe?g|png|gif)$", src, re.I) else src + ".jpg"
            mime = "image/png" if name.lower().endswith(".png") else "image/gif" if name.lower().endswith(".gif") else "image/jpeg"
            src = "https://europepmc.org/api/fulltextRepo?" + urllib.parse.urlencode(
                {"pprId": pid.upper(), "type": "FILE", "fileName": name, "mimeType": mime})
        return images.resolve(src) if re.match(r"https?://", src) else None
    return resolve


def fetch(pid, eid=None, about=None):
    """Store a paper from Europe PMC (once). Its id: pmc… or ppr…. eid: the Europe PMC id to read when the paper is
    stored under an id of another source (a medRxiv preprint whose site turned the reader away); about: meta fields
    that source gives (its link, its name)."""
    if store.readable(pid):
        return pid
    eid = eid or pid
    try:
        xml = _get(f"{API}/{eid.upper()}/fullTextXML").decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code in (404, 500):   # Europe PMC answers a missing full text with a 500
            raise RuntimeError("no-fulltext")
        raise
    html, m = jats.to_html(xml)
    kind = "PMC" if eid.startswith("pmc") else "PPR"
    meta = {"id": pid, "version": "", "title": m["title"] or eid.upper(), "authors": m["authors"], "date": m["date"],
            "abs_url": f"https://europepmc.org/article/{kind}/{eid.upper()}", "html_url": "", "license": m["license"],
            "doi": m["doi"], "source": {"kind": "europepmc", "name": eid.upper()}, "label": eid.upper(), **(about or {})}
    return local.store_doc(pid, html, meta, "xml", xml, lambda folder: image_resolver(eid, folder))
