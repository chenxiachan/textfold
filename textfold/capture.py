"""A page sent from the reader's own browser (extension/): what they see, signed in or subscribed, read as they see it.

The extension serializes the tab when the reader clicks its button (extension/snapshot.js: shadow roots spelled out,
frames put in place, what the browser did not show marked) and hands it to this program by native messaging: the
browser starts it, writes one message to its standard input, reads the answer from its standard output, and lets only
the extensions named in its manifest (allowed_origins) reach it. No port is opened. The page's cookies never leave the
browser: what comes is the page as it was drawn.

With it come its article as reading views read it in the browser (extension/reader.js: Defuddle's and Readability's).
A snapshot is stored as a web page of the library (webpage.py: web-<host>-<hash>), its HTML as it came beside it
(snapshot.html): its article the reading view's (article.html beside it), or, when neither has the words to read,
the one TextFold reads in the snapshot as in any web page. A page already in the library is not written over. When
TextFold runs, the new page's levels are asked of it at once, as for a page added on its landing.

The desktop app registers the host each time it starts (desktop/main.js: install --app), with its own Python and code,
for the extension as the Chrome Web Store gives it (STORE); pip's TextFold does it once, by hand. A message carries
the version of its make (PROTOCOL): a newer one than this TextFold knows is refused with update-textfold, and a hello
says which TextFold answers, for the extension to tell the reader what to do when none does (extension/setup.html).

  python3 -m textfold capture install [--extension <id>] [--browser chrome ...]   the host's manifest, for the store's
                                                                                 extension and those named
  python3 -m textfold capture-host                                               what the browser starts
"""
import json
import os
import re
import shlex
import struct
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from . import local, store, webpage

HOST = "app.textfold.capture"
FORMAT = "textfold/snapshot"
HELLO = "textfold/hello"
PROTOCOL = 1   # the make of the messages this TextFold reads (each message's "version")
MAX = 64 * 2**20   # what a browser sends a native host at most
# The extension as the stores give it, which every install lets in: its id is fixed by the key in
# extension/manifest.json, so that a copy loaded unpacked has it too (the Chrome Web Store's; Edge's store gives its own)
STORE = ("lkfemlafmabkdjedldmjebdpccdaeglf",)
EXTENSION_ID = re.compile(r"[a-p]{32}")
# where each browser looks for a host's manifest, for this user: a folder on macOS and Linux; on Windows, a key of the
# registry (HKEY_CURRENT_USER) that names the manifest's file
DIRS = {
    "darwin": {"chrome": "Library/Application Support/Google/Chrome/NativeMessagingHosts",
               "chromium": "Library/Application Support/Chromium/NativeMessagingHosts",
               "edge": "Library/Application Support/Microsoft Edge/NativeMessagingHosts",
               "brave": "Library/Application Support/BraveSoftware/Brave-Browser/NativeMessagingHosts"},
    "linux": {"chrome": ".config/google-chrome/NativeMessagingHosts", "chromium": ".config/chromium/NativeMessagingHosts",
              "edge": ".config/microsoft-edge/NativeMessagingHosts", "brave": ".config/BraveSoftware/Brave-Browser/NativeMessagingHosts"},
}
KEYS = {"chrome": r"Software\Google\Chrome\NativeMessagingHosts", "chromium": r"Software\Chromium\NativeMessagingHosts",
        "edge": r"Software\Microsoft\Edge\NativeMessagingHosts", "brave": r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts"}


# ── native messaging: each message its length (4 bytes, this computer's order), then its JSON ───────
def read_message(stream):
    head = stream.read(4)
    if not head:
        return None
    if len(head) < 4:
        raise ValueError("cut-off")
    n = struct.unpack("=I", head)[0]
    if n > MAX:
        raise ValueError("too-large")
    body = stream.read(n)
    if len(body) < n:
        raise ValueError("cut-off")
    return json.loads(body.decode("utf-8"))


def write_message(stream, obj):
    body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    stream.write(struct.pack("=I", len(body)) + body)
    stream.flush()


# ── a snapshot, kept ─────────────────────────────────────────────────
def _server_url():
    """The reader of the TextFold running now (server.json), if one is."""
    try:
        s = json.loads((store.DATA / "server.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    pid = s.get("pid") if isinstance(s, dict) else None
    if not isinstance(pid, int) or not isinstance(s.get("url"), str):
        return None
    from .storage_settings import _process_alive
    if not _process_alive(pid):
        return None   # (a server ended outright leaves its file behind)
    return s["url"]


def _generate(there, pid):
    """The paper's levels asked of the TextFold that runs, in the reader's language as its page last told it (as a
    page added on its landing is): the job's id, or None."""
    from . import langs, models
    lang = models.load_settings().get("lang")
    body = json.dumps({"paper": pid, "lang": lang if lang in langs.LANGS else None}).encode("utf-8")
    req = urllib.request.Request(f"{there}/api/generate", data=body, headers={"Content-Type": "application/json"})
    try:   # (straight to this computer, whatever proxy the system names)
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10) as r:
            return json.loads(r.read() or b"{}").get("id")
    except (OSError, ValueError):
        return None


def hello():
    """Which TextFold answers the extension: its version, the make of message it reads, and whether it runs."""
    from . import __version__
    return {"ok": True, "textfold": __version__, "protocol": PROTOCOL, "running": bool(_server_url())}


def reading_of(read, html, url):
    """The reading of a page sent from the browser: of the reading views' (Defuddle's, Readability's, those with the
    words to read) and TextFold's own of the snapshot, the one that differs least from the page (adapt.choose: blocks of
    the article it lacks, formulas not in their places, pictures, words the page does not show), the views first when
    they are as far from it. (how, its HTML, what the page says of itself, the view's own HTML or None, TextFold's own
    Markdown or None). web-notext: none has the words to read."""
    from . import adapt, semantic
    from .parse import parse
    got = {}
    for name, view in webpage.views_of(read).items():
        try:
            got[name] = (*webpage.read_view(view, html, url), view["html"], None)
        except ValueError:   # (web-notext: too few words once the site's bits are out)
            pass
    try:
        md, own, info = webpage.read(html, url)
        got["textfold"] = (own, info, None, md)
    except ValueError:
        pass
    if not got:
        raise ValueError("web-notext")
    how = next(iter(got))
    if len(got) > 1:
        try:
            docs = {k: parse(semantic.normalize(h, resolve_img=lambda src: src, sizes=True),
                             {"id": "web-choose", "abs_url": url, "title": i.get("title"), "authors": i.get("authors") or [], "date": i.get("date")})
                    for k, (h, i, _, _) in got.items()}
            how, _ = adapt.choose(html, url, docs)
        except Exception:   # (the check could not be made: the views by their words, as before)
            how = webpage.view_of(read)[0] or "textfold"
            how = how if how in got else next(iter(got))
    return (how, *got[how])


def receive(msg):
    """A snapshot the extension sent: checked, stored, its levels asked for when TextFold runs, and where to open it.
    Its article is the reading that differs least from the snapshot (reading_of): one a reading view read in the
    browser (msg["read"]), or the one TextFold reads in the snapshot itself. {"ok", "paper", "new", "open", "job"}. A hello is answered with hello()."""
    if isinstance(msg, dict) and msg.get("format") == HELLO:
        return hello()
    if not isinstance(msg, dict) or msg.get("format") != FORMAT:
        raise ValueError("not-a-snapshot")
    if isinstance(msg.get("version"), int) and msg["version"] > PROTOCOL:
        raise ValueError("update-textfold")   # (an extension newer than this TextFold: the reader is told to update it)
    url, html = msg.get("url"), msg.get("html")
    if not isinstance(url, str) or not re.match(r"https?://", url, re.I):
        raise ValueError("not-a-web-page")   # (a file:, a chrome:, an extension's page: not one TextFold reads)
    if not isinstance(html, str) or not html.strip():
        raise ValueError("no-page")
    url = url.split("#")[0]
    pid = webpage.pid_of(url)
    d = store.pdir(pid)
    new = not (d / "meta.json").exists()
    if new:
        how, read, info, view, md = reading_of(msg.get("read") if isinstance(msg.get("read"), dict) else None, html, url)
        host = re.sub(r"^www\.", "", urllib.parse.urlparse(url).hostname or "")
        if info["title"] == host:   # (the page names itself nowhere TextFold reads: the tab's title)
            info["title"] = (msg.get("title") or "").strip()[:300] or host
        d.mkdir(parents=True, exist_ok=True)
        (d / "url.txt").write_text(url, encoding="utf-8")
        meta = webpage.meta_of(pid, url, info)
        meta["source"] = {**meta["source"], "via": "browser"}
        meta["snapshot_kind"] = "browser"
        meta["snapshot_taken"] = msg.get("taken") if isinstance(msg.get("taken"), str) else time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        meta["reader"] = how
        pre = local.prefetch(re.findall(r'<img[^>]*\ssrc="([^"]+)"', read))
        local.store_doc(pid, read, meta, "snapshot.html", html, lambda folder: local.Images(folder, None, pre=pre).resolve, sizes=True)
        if view:
            (d / "article.html").write_text(view, encoding="utf-8")   # (as the reading view gave it)
        else:
            (d / "source.md").write_text(md, encoding="utf-8")
    there = _server_url()
    job = _generate(there, pid) if there and new else None
    return {"ok": True, "paper": pid, "new": new, "open": f"{there}/p/{pid}" if there else None, "job": job}


def host(stdin=None, stdout=None):
    """What the browser starts: each message answered, until the browser closes the stream."""
    if stdin is None and sys.platform == "win32":   # (bytes as they come: no line ends turned into others)
        import msvcrt
        for s in (sys.stdin, sys.stdout):
            msvcrt.setmode(s.fileno(), os.O_BINARY)
    stdin = stdin or sys.stdin.buffer
    stdout = stdout or sys.stdout.buffer
    while True:
        try:
            msg = read_message(stdin)
        except ValueError as e:
            write_message(stdout, {"ok": False, "error": str(e)})
            return 1
        if msg is None:
            return 0
        try:
            reply = receive(msg)
        except ValueError as e:
            reply = {"ok": False, "error": str(e)}
        except Exception as e:   # (anything else: said, not a crash the browser would only call "disconnected")
            reply = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
        write_message(stdout, reply)


# ── installing the host ──────────────────────────────────────────────
def _batch(value):
    """A path as a batch file holds it. cmd.exe reads one in the console's code page (OEM), not in UTF-8, so the
    reader's own folders, which hold their name (C:\\Users\\<name>\\AppData\\Local), are named by the variables the
    browser passes on to the host."""
    for var in ("LOCALAPPDATA", "APPDATA", "USERPROFILE"):
        there = (os.environ.get(var) or "").rstrip("\\")
        if there and value.lower().startswith(there.lower() + "\\"):
            return f"%{var}%" + value[len(there):].replace("%", "%%")
    return value.replace("%", "%%")


def launcher(home=None, app=False):
    """The program a manifest names (a path, no arguments): this Python and this TextFold, with the library it uses
    now. The desktop app's (app) runs as the app runs its server: its own packages alone (-s), in UTF-8, under the
    app's name for its runtime (TEXTFOLD_RUNTIME_ID), which says where the library was moved to (storage_settings)."""
    home = Path(home or store.HOME)
    home.mkdir(parents=True, exist_ok=True)
    env = {"TEXTFOLD_DATA": str(store.DATA)}
    path = [str(store.ROOT)] if app or store.SOURCE else []   # (a clone's or the app's code; pip's is where Python looks)
    env["PYTHONPATH"] = os.pathsep.join(path + [p for p in (os.environ.get("PYTHONPATH") or "").split(os.pathsep) if p and p not in path])
    if not env["PYTHONPATH"]:
        del env["PYTHONPATH"]
    for k in ("HOME", "TEXTFOLD_RUNTIME_ID"):
        if os.environ.get(k) and not (k == "HOME" and sys.platform == "win32"):
            env[k] = os.environ[k]
    flags = ""
    if app:
        env.update(TEXTFOLD_RUNTIME_ID="desktop", PYTHONNOUSERSITE="1", PYTHONUTF8="1", PYTHONDONTWRITEBYTECODE="1")
        flags = " -s"
    if sys.platform == "win32":
        f = home / "capture-host.bat"
        lines = ["@echo off", "rem TextFold's native messaging host (textfold/capture.py), started by the browser"]
        lines += [f'set "{k}={_batch(v)}"' for k, v in env.items()]
        lines += [f'"{_batch(sys.executable)}"{flags} -m textfold capture-host %*', ""]
        try:   # (a folder the reader chose: in the console's code page, as cmd.exe reads it)
            f.write_bytes("\r\n".join(lines).encode("oem"))
        except UnicodeEncodeError:
            raise ValueError(f"TextFold's folders cannot be named in a batch file here: {sys.executable}") from None
        return f
    f = home / "capture-host"
    keep = "".join(f"{k}={shlex.quote(v)} " for k, v in env.items())
    f.write_text(f"#!/bin/sh\n# TextFold's native messaging host (textfold/capture.py), started by the browser\n"
                 f"exec env {keep}{shlex.quote(sys.executable)}{flags} -m textfold capture-host \"$@\"\n", encoding="utf-8")
    f.chmod(0o755)
    return f


def manifest(path, extensions):
    return {"name": HOST, "description": "TextFold: pages sent from the browser", "path": str(path), "type": "stdio",
            "allowed_origins": [f"chrome-extension://{e}/" for e in extensions]}


def _let_in(files):
    """The extensions a manifest written before lets in (one loaded unpacked, named by hand): still let in."""
    out = []
    for f in files:
        try:
            m = json.loads(Path(f).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for o in m.get("allowed_origins", []) if isinstance(m, dict) else []:
            hit = re.fullmatch(r"chrome-extension://([a-p]{32})/", o) if isinstance(o, str) else None
            if hit:
                out.append(hit.group(1))
    return out


def _name_in_registry(key, value):
    import winreg
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key) as k:
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, value)


def install(extensions=(), browsers=None, home=None, dirs=None, app=False, keys=None):
    """The host's manifest where each browser looks for it, for this user, letting in the store's extension (STORE),
    those named, and those a manifest written before let in: [where it was written]. On Windows (or with keys) the
    manifest is one file beside the launcher, named in each browser's key of the registry."""
    if not all(isinstance(e, str) and EXTENSION_ID.fullmatch(e) for e in extensions):
        raise ValueError("an extension's id is 32 letters from a to p")
    windows = keys is not None or (dirs is None and sys.platform == "win32")
    if windows:
        places = {b: k for b, k in (keys or KEYS).items() if not browsers or b in browsers}
        files = [Path(home or store.HOME) / f"{HOST}.json"]
    else:
        places = {b: Path(p) for b, p in (dirs or {b: Path.home() / p for b, p in DIRS.get(sys.platform, {}).items()}).items()
                  if not browsers or b in browsers}
        if not places and not dirs and sys.platform not in DIRS:
            raise ValueError(f"not yet on {sys.platform}")
        files = [p / f"{HOST}.json" for p in places.values()]
    ids = list(dict.fromkeys([*STORE, *extensions, *_let_in(files)]))
    if not ids:
        raise ValueError("an extension's id is 32 letters from a to p: --extension <id> (chrome://extensions)")
    path, out = launcher(home, app), []
    for f in files:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(manifest(path, ids), indent=2), encoding="utf-8")
        out.append(f)
    if windows:
        for key in places.values():
            _name_in_registry(f"{key}\\{HOST}", str(files[0]))
            out.append(f"HKEY_CURRENT_USER\\{key}\\{HOST}")
    return out
