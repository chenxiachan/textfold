"""What's new: what a release says to the reader the first time TextFold opens after an update, in the desktop app and
a pip install alike (app.js, the page's dialog).

whatsnew.json, beside this file, is written with each release that has something to say: [{version, en, zh}], each
language's words a list of [title, text], plain text, and after a change a link may follow, to act on it at once:
[title, text, {url, label}] (the page's button: the store listing of the browser's button, say). The page shows the reader's language, Traditional Chinese the
Chinese, every other the English. A release with no entry is a quiet one: the page says only that it is installed.
The release the reader was last told of is kept in the library's folder (whatsnew.json there, {seen}); a first
install is told nothing, and a library from before this file (documents in it, no release told of) is told of the
release it now runs.
"""
import json
import re
from pathlib import Path

from . import __version__, store

NEWS = Path(__file__).with_name("whatsnew.json")


def _v(version):
    return tuple(int(x) for x in re.findall(r"\d+", str(version)))


def entries():
    """Every release's words, newest first."""
    try:
        got = json.loads(NEWS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return sorted((e for e in got if isinstance(e, dict) and e.get("version")), key=lambda e: _v(e["version"]), reverse=True)


def _seen():
    try:
        return json.loads((store.DATA / "whatsnew.json").read_text(encoding="utf-8")).get("seen")
    except (OSError, ValueError, AttributeError):
        return None


def told(version=__version__):
    """The reader told of a release (the page showed its words, or said it is installed)."""
    store.DATA.mkdir(parents=True, exist_ok=True)
    tmp = store.DATA / "whatsnew.json.tmp"
    tmp.write_text(json.dumps({"seen": str(version)}), encoding="utf-8")
    tmp.replace(store.DATA / "whatsnew.json")


def state(now=__version__):
    """What the page says as it opens: {version, updated: whether this release is new to the reader, news: the words of
    the releases since the one they were last told of (newest first; none: a quiet update), last: the newest release's
    words up to this one (Settings' What's new)}."""
    mine = [e for e in entries() if _v(e["version"]) <= _v(now)]
    seen = _seen()
    if seen is None:
        papers = store.DATA / "papers"
        if not (papers.is_dir() and any(papers.iterdir())):   # a first install: nothing to say, now or at this release
            told(now)
            return {"version": now, "updated": False, "news": [], "last": mine[0] if mine else None}
        news = [e for e in mine if _v(e["version"]) == _v(now)]   # (a library from before: this release's words)
    else:
        news = [e for e in mine if _v(e["version"]) > _v(seen)]
    return {"version": now, "updated": seen is None or _v(now) > _v(seen), "news": news, "last": mine[0] if mine else None}
