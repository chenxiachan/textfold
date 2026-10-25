"""A GitHub repository's README, read as a document: its Markdown with the HTML it holds (a centred logo, badges, a
<details>), its pictures from the repository and its links to the repository's files. Fetched once through GitHub's
REST API (no sign-in: sixty requests an hour, two for each repository), and kept as a snapshot of the day it was read.
Its id: gh-<owner>--<repository> (an owner's name has no double hyphen)."""
import base64
import json
import posixpath
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from bs4 import BeautifulSoup

from . import local, markdown, store
from .fetch import UA

URL = re.compile(r"(?:https?://)?(?:www\.)?github\.com/([A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38})/([A-Za-z0-9._-]{1,100})", re.I)
ID = re.compile(r"gh-([a-z0-9-]+)--([a-z0-9._-]+)")


def ref_of(ref):
    """(owner, repository) for a link to a GitHub repository, or to anything in it; (None, None) for anything else."""
    m = URL.search(ref or "")
    if not m or m.group(1).lower() in ("orgs", "users", "topics", "search", "marketplace", "settings", "apps"):
        return None, None
    return m.group(1), re.sub(r"\.git$", "", m.group(2))


def pid_of(owner, repo):
    return f"gh-{owner.lower()}--{repo.lower()}"


def _api(path):
    req = urllib.request.Request(f"https://api.github.com{path}", headers={**UA, "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:   # no such repository, a private one, or one without a README
            raise ValueError("no-readme") from None
        if e.code in (403, 429) and e.headers.get("X-RateLimit-Remaining") == "0":
            raise RuntimeError("github-limit") from None
        raise


def _absolute(u, base, folder, picture):
    """A link or picture of the README, as an address: a relative one from the README's folder in the repository (or
    from its root, with a leading /); a picture on github.com, from its raw file."""
    if u.startswith("#") or u.startswith("//") or re.match(r"^[a-z][a-z0-9+.-]*:", u, re.I):
        if picture:
            u = re.sub(r"^https?://github\.com/([^/]+/[^/]+)/(?:blob|raw)/", r"https://raw.githubusercontent.com/\1/", u)
        return u
    path = u.lstrip("/") if u.startswith("/") else posixpath.join(folder, u)
    return urllib.parse.urljoin(base, path)


def _rebase(html, raw, blob, folder):
    soup = BeautifulSoup(html, "lxml")
    for img in soup.find_all("img", src=True):
        img["src"] = _absolute(img["src"], raw, folder, True)
    for a in soup.find_all("a", href=True):
        a["href"] = _absolute(a["href"], blob, folder, False)
    return (soup.body or soup).decode_contents()


def fetch(pid, progress=None):
    """Store a repository's README (once)."""
    if store.readable(pid):
        return pid
    owner, repo = ID.fullmatch(pid).groups()
    info = _api(f"/repos/{owner}/{repo}")   # its name as written, default branch, license, owner
    readme = _api(f"/repos/{owner}/{repo}/readme")
    text = base64.b64decode(readme["content"]).decode("utf-8", "replace")
    full, branch = info["full_name"], info["default_branch"]
    html, fm = markdown.to_html(text)
    title, html = markdown.take_title(html, fm, readme["name"])
    if re.fullmatch(r"readme", title, re.I):   # no heading of its own: the repository's name
        title = info["name"]
    html = _rebase(html, f"https://raw.githubusercontent.com/{full}/{branch}/", f"https://github.com/{full}/blob/{branch}/",
                   posixpath.dirname(readme["path"]))
    spdx = (info.get("license") or {}).get("spdx_id")
    meta = {"id": pid, "version": "", "title": title, "authors": [info["owner"]["login"]],
            "date": (info.get("pushed_at") or "")[:10], "snapshot": time.strftime("%Y-%m-%d"),
            "abs_url": info["html_url"], "html_url": "", "description": info.get("description") or "",
            "license": f"https://spdx.org/licenses/{spdx}.html" if spdx and spdx != "NOASSERTION" else "",
            "source": {"kind": "github", "name": full, "branch": branch}, "label": f"GitHub · {full}"}
    pre = local.prefetch(re.findall(r'<img[^>]*\ssrc="([^"]+)"', html))   # its pictures, at once
    return local.store_doc(pid, html, meta, "markdown", text, lambda folder: local.Images(folder, None, pre=pre).resolve, sizes=True)
