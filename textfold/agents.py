"""Coding agents already installed on this computer, ported from ThoughtDAG (runtime/agents/*.cjs).

The server scans for them at startup (and on demand): every mainstream agent CLI is looked for on PATH,
on the login shell's PATH, in the usual install directories and under the node version managers, as
ThoughtDAG's where.cjs does. Four can run a generation, the three ThoughtDAG drives and CodeBuddy:

  claude-code  claude -p, structured output by --json-schema        models: fable, opus, sonnet, haiku
  codex        codex exec, structured output by --output-schema      models: `codex app-server` model/list
  pi           pi -p --mode json, JSON asked for in the prompt       models: `pi --mode rpc` get_available_models
  codebuddy    codebuddy -p, structured output by --json-schema      models: the account's, as its --help lists them

CodeBuddy is Tencent's CLI, and WorkBuddy carries a copy of it (cli/bin/codebuddy in the app), which runs on the app's
own Electron and the WorkBuddy sign-in, as WorkBuddy runs it: no node needed, the reader's WorkBuddy models.

The others are reported when found, but not driven yet. A command the reader adds in the settings (a custom agent:
`mirasim claude`, which runs Claude Code under another name) is run by the driver of the one it speaks as. A model id is ThoughtDAG's:
`claude/claude-code/opus`, `codex/codex/<model>`, `pi/<provider>/<model>`; CodeBuddy's `codebuddy/codebuddy/<model>`.
Agents run here as one-shot text generators: no tools, no session, a scratch working directory. A question about a
text can be looked up on the web (tools=True: the switch beside a question; models.ask_spec): the agent's own web
tools, its own system prompt with ours after it, and nothing that changes a file (Claude Code and CodeBuddy: WebSearch
and WebFetch alone, no MCP server; Pi: its extensions' tools, none of its own; Codex: its web search, in the read-only
sandbox it always runs in, where it may read but writes nothing). Still no session, and the scratch directory to start in.
"""
import glob
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HOME = Path.home()
WIN = os.name == "nt"
MAC = sys.platform == "darwin"
WORKBUDDY_ID = "com.workbuddy.workbuddy"   # (its bundle id on a Mac)
WORKBUDDY_MAC = ["/Applications/WorkBuddy.app", "~/Applications/WorkBuddy.app"]


def _spotlight(bundle_id):
    """The apps of that bundle id wherever they are on this Mac (one dragged to Downloads, say), as Spotlight knows."""
    try:
        r = subprocess.run(["mdfind", f'kMDItemCFBundleIdentifier == "{bundle_id}"'], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=3, stdin=subprocess.DEVNULL)
        return [x for x in r.stdout.splitlines() if x.endswith(".app")]
    except Exception:
        return []


def _installed(name):
    """Where Windows' installed programs whose name starts with `name` are, as their installers recorded it (the
    reader may have chosen any folder, D:\\ as well): InstallLocation, else the folder of DisplayIcon or of the
    uninstaller."""
    try:
        import winreg
    except ImportError:
        return []
    out = []
    for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
                      (winreg.HKEY_LOCAL_MACHINE, r"Software\Microsoft\Windows\CurrentVersion\Uninstall"),
                      (winreg.HKEY_LOCAL_MACHINE, r"Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall")):
        try:
            root = winreg.OpenKey(hive, key)
        except OSError:
            continue
        for i in range(winreg.QueryInfoKey(root)[0]):
            try:
                app = winreg.OpenKey(root, winreg.EnumKey(root, i))
                values = {}
                for v in ("DisplayName", "InstallLocation", "DisplayIcon", "UninstallString"):
                    try:
                        values[v] = str(winreg.QueryValueEx(app, v)[0] or "")
                    except OSError:
                        values[v] = ""
            except OSError:
                continue
            if not values["DisplayName"].lower().startswith(name.lower()):
                continue
            place = values["InstallLocation"].strip().strip('"') or next(
                (d for d in (_folder_of(values["DisplayIcon"]), _folder_of(values["UninstallString"])) if d), "")
            if place:
                out.append(place)
    return out


def _folder_of(command):
    """The folder of the program a registry value names: '"D:\\Apps\\WorkBuddy\\WorkBuddy.exe",0' (an icon),
    '"D:\\Apps\\WorkBuddy\\Uninstall WorkBuddy.exe" /currentuser' (an uninstaller and its arguments)."""
    import ntpath
    s = (command or "").strip()
    if s.startswith('"'):
        s = s[1:].split('"', 1)[0]
    else:
        m = re.match(r"(?i)(.+?\.exe)\b", s)
        s = m.group(1) if m else s.split(",")[0]
    return ntpath.dirname(s.strip())


def _running_workbuddy():
    """The WorkBuddy apps running now (a Mac's .app, Windows' install folder), as the system lists their programs: open,
    WorkBuddy says itself where it is, whatever folder it was put in."""
    if WIN:
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
               "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '*WorkBuddy*' } | ForEach-Object { $_.ExecutablePath }"]
    else:
        cmd = ["ps", "-axo", "comm="]   # (each process's program, its whole path: spaces in it stay)
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=6, stdin=subprocess.DEVNULL)
    except Exception:
        return []
    return _apps_in(r.stdout)


def _apps_in(listing):
    """The WorkBuddy apps a list of program paths names, one a line: on a Mac the outermost .app of each ("/Applications/
    WorkBuddy.app/Contents/Frameworks/WorkBuddy Helper (GPU).app/…" is WorkBuddy.app) when it is WorkBuddy's, on
    Windows the folder of each program, or for a helper under the app's resources\\ (its CLI's sandbox, its git) the
    app's own folder."""
    import ntpath
    out = []
    for line in listing.splitlines():
        line = line.strip().strip('"')
        if "workbuddy" not in line.lower():
            continue
        if WIN:
            folder = ntpath.dirname(line)
            i = (folder + "\\").lower().find("\\resources\\")
            out.append(folder[:i] if i > 0 else folder)
        else:
            i = line.find(".app/")
            app = line[:i + 4] if i > 0 else ""
            if "workbuddy" in Path(app).name.lower():
                out.append(app)
    return list(dict.fromkeys(x for x in out if x))


def workbuddy_roots():
    """Where WorkBuddy may be: the one running, its usual places, then those Windows' installer recorded (the reader may
    choose any folder, D:\\ as well) or Spotlight knows (an app dragged to Downloads, say)."""
    if WIN:
        places = [os.path.join(os.environ.get("ProgramFiles") or "C:/Program Files", "WorkBuddy"),
                  os.path.join(os.environ.get("ProgramFiles(x86)") or "C:/Program Files (x86)", "WorkBuddy"),
                  os.path.join(os.environ.get("LOCALAPPDATA") or "~/AppData/Local", "Programs", "WorkBuddy")] + _installed("WorkBuddy")
    elif MAC:
        places = WORKBUDDY_MAC + _spotlight(WORKBUDDY_ID)
    else:
        places = []
    out = []
    for r in _running_workbuddy() + places:
        r = os.path.expanduser(r)
        if r not in out and os.path.isdir(r):
            out.append(r)
    return out


CLI_AT = ("Contents/Resources/app.asar.unpacked/cli/bin/codebuddy", "resources/app.asar.unpacked/cli/bin/codebuddy")
SKIP = {"node_modules", ".git", "locales", "Frameworks", "_CodeSignature", "vendor", "wasm"}


def find_cli(root):
    """CodeBuddy inside a WorkBuddy app: where WorkBuddy 5.3 keeps it (CLI_AT), else wherever a codebuddy sits in a bin
    folder of the app's resources, should a later version move it (node_modules and the like not looked through)."""
    root = Path(root)
    for rel in CLI_AT:
        if (root / rel).is_file():
            return [str(root / rel)]
    found = []
    for base in (root / "Contents" / "Resources", root / "resources"):
        if not base.is_dir():
            continue
        for d, dirs, files in os.walk(base):
            deep = len(Path(d).relative_to(base).parts)
            dirs[:] = [x for x in dirs if x not in SKIP and not x.endswith((".lproj", ".app", ".framework"))] if deep < 6 else []
            if Path(d).name == "bin":
                found += [os.path.join(d, n) for n in sorted(files) if n in ("codebuddy", "codebuddy.js", "codebuddy.exe")]
    return sorted(found, key=lambda p: ("cli" not in Path(p).parts, len(p)))


# runtime, label, binary, extra directories (or what finds them, at each scan), can it run a generation here
REGISTRY = [
    ("claude-code", "Claude Code", "claude", ["~/.claude/local"], True),
    ("codex", "Codex", "codex", ["~/.codex/bin"], True),
    ("pi", "Pi", "pi", ["~/.pi/bin"], True),
    ("codebuddy", "CodeBuddy", "codebuddy", [], True),   # (WorkBuddy's copy first: locate_codebuddy)
    ("gemini", "Gemini CLI", "gemini", [], False),
    ("qwen", "Qwen Code", "qwen", [], False),
    ("opencode", "opencode", "opencode", ["~/.opencode/bin"], False),
    ("cursor-agent", "Cursor Agent", "cursor-agent", [], False),
    ("kimi", "Kimi CLI", "kimi", [], False),
    ("amp", "Amp", "amp", [], False),
    ("goose", "Goose", "goose", [], False),
    ("crush", "Crush", "crush", [], False),
    ("droid", "Factory Droid", "droid", ["~/.factory/bin"], False),
    ("copilot", "GitHub Copilot CLI", "copilot", [], False),
    ("iflow", "iFlow CLI", "iflow", [], False),
    ("aider", "Aider", "aider", [], False),
]
PREFIX = {"claude-code": "claude/", "codex": "codex/", "pi": "pi/", "codebuddy": "codebuddy/"}
COMMON = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "~/.bun/bin", "~/.npm-global/bin", "~/.local/bin"]
if WIN:   # npm's global folder, Claude Code's own installer (~/.local/bin, above), Scoop, Node itself
    COMMON = [os.path.join(os.environ.get("APPDATA") or "~/AppData/Roaming", "npm"), "~/.local/bin", "~/.bun/bin", "~/scoop/shims",
              os.path.join(os.environ.get("ProgramFiles") or "C:/Program Files", "nodejs")]
# Windows: a program is claude.exe, or npm's shim claude.cmd (the bare `claude` beside it is a shell script)
EXTS = (".exe", ".cmd", ".bat") if WIN else ("",)


def _version_key(p):
    return [int(x) if x.isdigit() else 0 for x in re.findall(r"\d+", p)]


_login_path = None


def search_dirs():
    """PATH, the login shell's PATH, common install dirs, version-manager dirs (ThoughtDAG where.cjs)."""
    global _login_path
    if _login_path is None and WIN:
        _login_path = ""   # no login shell to ask
    if _login_path is None:
        shell = os.environ.get("SHELL", "/bin/zsh")
        try:
            cmd = [shell, "-l", "-c", "string join : $PATH"] if shell.endswith("fish") else [shell, "-ilc", 'printf "%s" "$PATH"']
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=4, stdin=subprocess.DEVNULL)
            _login_path = (r.stdout.strip().splitlines() or [""])[-1]
        except Exception:
            _login_path = ""
    dirs = os.environ.get("PATH", "").split(os.pathsep) + _login_path.split(":") + COMMON
    managers = sorted(glob.glob(str(HOME / ".nvm/versions/node/*/bin")), key=_version_key, reverse=True)
    for pattern in ["~/.fnm/node-versions/*/installation/bin", "~/Library/Application Support/fnm/node-versions/*/installation/bin",
                    "~/.volta/bin", "~/n/bin", "~/.nodenv/shims", "~/.asdf/shims", "~/.local/share/mise/shims", "~/.proto/bin",
                    "~/Library/pnpm", "~/.local/share/pnpm", "~/.yarn/bin", "~/.config/yarn/global/node_modules/.bin",
                    "~/.deno/bin", "~/.cargo/bin", "~/bin"]:
        managers += sorted(glob.glob(os.path.expanduser(pattern)), key=_version_key, reverse=True)
    out, seen = [], set()
    for d in dirs + managers:
        d = os.path.expanduser(d)
        if d and d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def child_env():
    """Children get the widened PATH, so a node-based CLI finds node, and USER and LOGNAME even where the server was
    started without them (launchd, cron, env -i): Claude Code finds its sign-in in the keychain under USER, and
    without it says it is not logged in."""
    env = {**os.environ, "PATH": os.pathsep.join(search_dirs())}
    if not (env.get("USER") and env.get("LOGNAME")):
        try:
            import pwd   # not on Windows, where neither is needed
            name = pwd.getpwuid(os.getuid()).pw_name
        except (ImportError, KeyError):
            name = None
        for k in ("USER", "LOGNAME"):
            if name and not env.get(k):
                env[k] = name
    return env


def locate(binary, extra=()):
    for d in [os.path.expanduser(x) for x in extra] + search_dirs():
        for ext in EXTS:
            f = os.path.join(d, binary + ext)
            if os.path.isfile(f) and os.access(f, os.X_OK):
                return f
    return None


def workbuddy_app(path):
    """The WorkBuddy app a CodeBuddy found at `path` is part of, and the app's own program: (folder, program), or None
    for a codebuddy installed on its own. A Mac's: the .app it is in, the program its Info.plist names (Electron, for
    WorkBuddy 5.3); Windows': the folder of resources/ it is in whose program is WorkBuddy.exe or Electron.exe, else
    the one program of the first such folder that is not an uninstaller or an updater. (WorkBuddy 5.8 keeps a folder
    of resources/ inside its own, resources\\app.asar.unpacked\\resources: the walk goes on up past it.)"""
    alone = None
    for a in Path(path).parents:
        if not WIN and a.suffix == ".app":
            try:
                import plistlib
                with open(a / "Contents" / "Info.plist", "rb") as fp:
                    info = plistlib.load(fp)
            except Exception:
                info = {}
            exe = a / "Contents" / "MacOS" / (info.get("CFBundleExecutable") or "Electron")
            ours = info.get("CFBundleIdentifier") == WORKBUDDY_ID or "workbuddy" in a.name.lower()
            return (str(a), str(exe)) if ours and exe.is_file() else None
        if WIN and (a / "resources").is_dir():
            exe = next((x for x in (a / "WorkBuddy.exe", a / "Electron.exe") if x.is_file()), None)
            ours = (a / "WorkBuddy.exe").is_file() or "workbuddy" in str(a).lower()
            if ours and exe:
                return str(a), str(exe)
            others = sorted(x for x in a.glob("*.exe") if not re.match(r"(?i)un|update|crash|elevate", x.name))
            if ours and alone is None and len(others) == 1:
                alone = (str(a), str(others[0]))
    return alone


def workbuddy_lite(path):
    """Is the CodeBuddy at `path` the copy WorkBuddy 5.8 and later bring: its lite bundle beside its launcher
    (cli/dist/codebuddy-lite-wb.mjs), which the launcher runs only when CODEBUDDY_FORCE_LITE_WB_BUNDLE is 1, as
    WorkBuddy runs it (else it looks for the full bundle, which is gone, and stops on MODULE_NOT_FOUND)? Such a copy
    has no sign-in for another program: WorkBuddy keeps its token encrypted and hands the key to its own runs alone."""
    return bool(path) and workbuddy_app(path) is not None and (Path(path).parent.parent / "dist" / "codebuddy-lite-wb.mjs").is_file()


FUSE_WIRE = b"dL7pKGdnNz796PbbjQWNKmHXBZaB9tsX"   # Electron's fuses: this, a version byte, a count, a byte a fuse ('1' on)


def _runs_as_node(exe):
    """Will the app's Electron run a script as node (ELECTRON_RUN_AS_NODE)? Its RunAsNode fuse, the first, read from
    the binary that carries the fuses (a Mac's Electron Framework, Windows' program itself) without running anything:
    an app that turned it off would open its window instead. No fuses at all: an Electron that has none, which does."""
    import mmap
    exe = Path(exe)
    if WIN:
        files = [exe]
    else:
        frameworks = exe.parent.parent / "Frameworks" / "Electron Framework.framework"
        files = [x for x in (frameworks / "Versions" / "A" / "Electron Framework", frameworks / "Electron Framework") if x.is_file()] or [exe]
    for f in files:
        try:
            with open(f, "rb") as fp, mmap.mmap(fp.fileno(), 0, access=mmap.ACCESS_READ) as m:
                i = m.find(FUSE_WIRE)
                if i >= 0:
                    j = i + len(FUSE_WIRE)
                    return m[j + 1] > 0 and m[j + 2] == ord("1")
        except (OSError, ValueError):
            continue
    return True


def _workbuddy_node():
    """The node WorkBuddy brings along (~/.workbuddy/binaries/node/versions/<v>/bin/node, node.exe on Windows), the
    newest, or None."""
    conf = Path(os.environ.get("WORKBUDDY_CONFIG_DIR") or HOME / ".workbuddy")
    found = glob.glob(str(conf / "binaries" / "node" / "versions" / "*" / "bin" / "node")) + \
        glob.glob(str(conf / "binaries" / "node" / "versions" / "*" / "node.exe"))
    found = [x for x in found if os.path.isfile(x)]
    return max(found, key=_version_key) if found else None


def workbuddy_runners(path):
    """What may run WorkBuddy's copy of CodeBuddy found at `path`, the safest first: the node WorkBuddy brings along,
    node on this computer, the app's own program as node (as WorkBuddy runs it) when its fuse lets it; [] for a
    codebuddy that runs itself (one installed on its own, a native one)."""
    if Path(path).name not in ("codebuddy", "codebuddy.js"):
        return []
    app = workbuddy_app(path)
    if not app:
        return []
    out = [_workbuddy_node(), shutil.which("node", path=os.pathsep.join(search_dirs()))]
    if _runs_as_node(app[1]):
        out.append(app[1])
    return list(dict.fromkeys(x for x in out if x))


_RUNNER = {}   # the runner that answered for each WorkBuddy copy (_runs)


def workbuddy_runner(path):
    """What runs WorkBuddy's copy of CodeBuddy found at `path`: the one that answered, else the safest there is."""
    if path in _RUNNER:
        return _RUNNER[path]
    runners = workbuddy_runners(path)
    return runners[0] if runners else None


# CodeBuddy as WorkBuddy runs it: no memory, no reminders, no suggestions, no repository scan (also ~400 tokens a call
# rather than ~15,000)
HELP_WAIT = 9.5   # (seconds: CodeBuddy's --help gives the account's models up within 10)
CODEBUDDY_QUIET = {k: "1" for k in ("CODEBUDDY_DISABLE_AUTO_MEMORY", "CODEBUDDY_DISABLE_SYSTEM_REMINDER_MD",
                                     "CODEBUDDY_CODE_DISABLE_SESSION_SUMMARY", "CODEBUDDY_PROMPT_SUGGESTION_DISABLED",
                                     "CODEBUDDY_GIT_REPO_SCAN_DISABLED", "CODEBUDDY_DISABLE_CRON", "CODEBUDDY_DISABLE_IDE")}


def env_for(path):
    """The environment an agent found at `path` runs in: child_env(), and for CodeBuddy its quiet switches; WorkBuddy's
    copy on the app's Electron as node (without it the app itself would open), with WorkBuddy's own settings, where
    its sign-in is (WorkBuddy 5.3's), and its lite bundle chosen where that is all it brings (workbuddy_lite)."""
    env = child_env()
    if path and Path(path).name.split(".")[0] == "codebuddy":
        env.update(CODEBUDDY_QUIET)
        if workbuddy_app(path):
            env["ELECTRON_RUN_AS_NODE"] = "1"   # (Electron's own switch; node does not mind it)
            conf = Path(os.environ.get("WORKBUDDY_CONFIG_DIR") or HOME / ".workbuddy")
            if conf.is_dir():
                env["CODEBUDDY_CONFIG_DIR"] = str(conf)
            if workbuddy_lite(path):
                env["CODEBUDDY_FORCE_LITE_WB_BUNDLE"] = "1"
    return env


_SHIM = re.compile(r'"%~?dp0%?\\([^"]+\.[cm]?js)"\s+%\*')


def argv(path):
    """The command that runs an agent found at `path`. On Windows npm's shim (claude.cmd) would hand its arguments to
    cmd.exe, which ends a multi-line system prompt at its first line break and reads its quotes and carets: the
    script the shim names is run by node instead. WorkBuddy's copy of CodeBuddy is run by node (WorkBuddy's own,
    else this computer's) or by the app's Electron as node (workbuddy_runner, env_for)."""
    runner = workbuddy_runner(path)
    if runner:
        return [runner, path]
    if not (WIN and path.lower().endswith(".cmd")):
        return [path]
    try:
        m = _SHIM.search(Path(path).read_text(encoding="utf-8", errors="replace"))
    except OSError:
        m = None
    script = m and Path(path).parent / m.group(1).replace("\\", os.sep)
    if not (script and script.is_file()):
        return [path]
    node = Path(path).parent / "node.exe"
    node = str(node) if node.is_file() else shutil.which("node", path=os.pathsep.join(search_dirs()))
    return [node, str(script)] if node else [path]


def _run(args, timeout, env=None, **kw):
    return subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                          timeout=timeout, env=env or child_env(), stdin=subprocess.DEVNULL, **kw)


def _help_levels(path, flag, pattern, cmd=None):
    """Effort or thinking levels as the CLI's own --help lists them. (cmd: the command that starts it, where that is
    not the program alone: a custom agent's)"""
    try:
        text = _run([*(cmd or argv(path)), "--help"], 8, env_for(path)).stdout
    except Exception:
        return []
    m = re.search(pattern, text, re.S)
    return [x.strip() for x in m.group(1).split(",") if x.strip()] if m else []


def codebuddy_candidates():
    """WorkBuddy's copies of CodeBuddy, wherever WorkBuddy is, then one installed on its own (npm's), signed in on its
    own; WorkBuddy 5.8's copies after that one, since another program cannot use their sign-in (workbuddy_lite)."""
    ours = [c for r in workbuddy_roots() for c in find_cli(r)]
    out = [c for c in ours if not workbuddy_lite(c)] + [locate("codebuddy")] + [c for c in ours if workbuddy_lite(c)]
    return list(dict.fromkeys(c for c in out if c))


def _runs(path):
    """Does the CodeBuddy at `path` answer --version? For WorkBuddy's copy each runner in turn (workbuddy_runners): the
    first that answers is the one it runs on."""
    def ok(args):
        try:
            r = _run(args, 10, env_for(path))
            return r.returncode == 0 and bool(re.search(r"\d+\.\d+", r.stdout))
        except Exception:
            return False
    if not workbuddy_app(path):
        return ok([*argv(path), "--version"])
    for runner in workbuddy_runners(path):
        if ok([runner, path, "--version"]):
            _RUNNER[path] = runner
            return True
    return False


def locate_codebuddy():
    """The first CodeBuddy that runs: WorkBuddy's own, else one installed on its own; None when none does."""
    return next((c for c in codebuddy_candidates() if _runs(c)), None)


# ── catalogs ──────────────────────────────────────────────────────────
def _claude_models(path, cmd=None):
    efforts = _help_levels(path, "--effort", r"--effort <level>.*?\(([^)]*)\)", cmd)
    efforts = (["off"] + efforts) if efforts else []   # off: no thinking at all (llm.py sets the thinking budget to nothing)
    # (the user's own effortLevel is not the default here: llm.py leaves the user settings out of a call)
    return [{"id": f"claude/claude-code/{a}", "name": f"Claude Code · {a.capitalize()}", "runtime": "claude-code",
             "model": a, "efforts": efforts, "defaultEffort": None}
            for a in ["fable", "opus", "sonnet", "haiku"]]


def _rpc(args, requests, timeout=30, cwd=None):
    """Write JSON lines, collect the replies whose id we asked for. When none comes back, an error that says why, in the
    program's own words (what it wrote to stderr) when it stopped: a scan then shows it, not "0 models" (#12). The pipes
    are UTF-8 whatever the system's codepage (a Chinese Windows would read them as cp936)."""
    err = tempfile.TemporaryFile("w+", encoding="utf-8", errors="replace")
    p = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=err, text=True, encoding="utf-8",
                         errors="replace", env=child_env(), cwd=cwd or str(HOME))
    lines = queue.Queue()   # read by a thread: select() cannot wait on a pipe on Windows
    threading.Thread(target=lambda: [*map(lines.put, p.stdout), lines.put(None)], daemon=True).start()
    got = {}
    try:
        for r in requests:
            p.stdin.write(json.dumps(r) + "\n")
        p.stdin.flush()
        want = {r["id"] for r in requests if "id" in r}
        t0 = time.time()
        while want - set(got) and time.time() - t0 < timeout:
            try:
                line = lines.get(timeout=0.5)
            except queue.Empty:
                continue
            if line is None:
                break
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("id") in want:
                got[d["id"]] = d
    finally:
        code = p.poll()   # None: still running when we stopped waiting
        p.kill()
    if not got and any("id" in r for r in requests):
        err.seek(0)
        said = " ".join(err.read().split())[-300:]
        name = Path(args[0]).name
        raise RuntimeError(f"{name} stopped (exit {code}){': ' + said if said else ''}" if code is not None else
                           f"{name} gave no reply in {timeout} s{': ' + said if said else ''}")
    return got


def _codex_models(path, cmd=None):
    got = _rpc([*(cmd or argv(path)), "app-server"], [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"clientInfo": {"name": "textfold", "title": "TextFold", "version": "0.4"}, "capabilities": None}},
        {"jsonrpc": "2.0", "method": "initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "model/list", "params": {}}])
    rows = (got.get(2) or {}).get("result", {}).get("data", [])
    home = Path(os.environ.get("CODEX_HOME", HOME / ".codex"))
    m = re.search(r'^model_reasoning_effort\s*=\s*"([^"]+)"', (home / "config.toml").read_text(encoding="utf-8", errors="replace"), re.M) if (home / "config.toml").exists() else None
    out = []
    for r in rows:
        if r.get("hidden"):
            continue
        mid = r.get("model") or r.get("id")
        efforts = [e.get("reasoningEffort") for e in r.get("supportedReasoningEfforts", []) if e.get("reasoningEffort")]
        default = m.group(1) if m and m.group(1) in efforts else r.get("defaultReasoningEffort")
        out.append({"id": f"codex/codex/{mid}", "name": f"Codex · {r.get('displayName') or mid}", "runtime": "codex", "model": mid,
                    "efforts": efforts, "defaultEffort": default, "isDefault": bool(r.get("isDefault"))})
    return out


def _pi_models(path, cmd=None):
    levels = _help_levels(path, "--thinking", r"--thinking <level>\s+Set thinking level:\s*([^\n]*)", cmd)
    # without the extensions, skills and context files a generation runs without (run_pi): an extension Pi cannot load
    # would stop it here, and a model only an extension adds could not be used anyway
    got = _rpc([*(cmd or argv(path)), "--mode", "rpc", "--no-session", "--no-extensions", "--no-skills", "--no-prompt-templates",
                "--no-context-files"], [{"id": "1", "type": "get_available_models"}])
    data = (got.get("1") or {}).get("data")
    rows = data.get("models", []) if isinstance(data, dict) else (data or [])
    return [{"id": f"pi/{r['provider']}/{r['id']}", "name": f"Pi · {r.get('name') or r['id']}", "runtime": "pi",
             "model": f"{r['provider']}/{r['id']}", "efforts": levels if r.get("reasoning") else [], "defaultEffort": None,
             "vision": "image" in (r.get("input") or [])} for r in rows if r.get("provider") and r.get("id")]


def _codebuddy_models(path):
    """The models the account may use, as --help lists them once signed in ("Currently supported: (auto, glm-5.1, …)"),
    the efforts too. For --help it asks the account for them within 10 s, and lists its own few when that runs out
    (cli/dist/codebuddy.js, updateModelOptionDescription): a --help that took that long is asked once more."""
    for attempt in (1, 2):
        t0 = time.time()
        text = _run([*argv(path), "--help"], 30, env_for(path)).stdout
        if time.time() - t0 < HELP_WAIT:
            break
    m = re.search(r"Currently supported:\s*\(([^)]*)\)", text)
    names = [x.strip() for x in m.group(1).split(",") if x.strip()] if m else ["auto"]
    e = re.search(r"--effort <level>.*?\(([^)]*)\)", text, re.S)
    efforts = [x.strip() for x in e.group(1).split(",") if x.strip()] if e else []
    who = "WorkBuddy" if workbuddy_app(path) else "CodeBuddy"
    return [{"id": f"codebuddy/codebuddy/{n}", "name": f"{who} · {'Auto' if n == 'auto' else n}", "runtime": "codebuddy",
             "model": n, "efforts": efforts, "defaultEffort": None} for n in names]


CATALOG = {"claude-code": _claude_models, "codex": _codex_models, "pi": _pi_models, "codebuddy": _codebuddy_models}

# ── the scan ──────────────────────────────────────────────────────────
STATE = {"scanned_at": None, "scanning": False, "agents": []}
_lock = threading.Lock()


def wait_scanned(timeout):
    """Wait up to timeout seconds for a scan under way to end; whether none is under way now."""
    end = time.time() + timeout
    while STATE["scanning"] and time.time() < end:
        time.sleep(0.05)
    return not STATE["scanning"]


def scan(log=None):
    """Find every registered agent, ask the runnable ones for their models. Safe to call from any thread."""
    global _login_path
    with _lock:
        STATE["scanning"] = True
        _login_path = None  # a new install may have changed the login PATH
        found, seen = [], {}
        for runtime, label, binary, extra, runnable in REGISTRY:
            # TEXTFOLD_NO_MODELS=1: none looked for (a test server, a CI run: nothing calls a model by accident)
            path = None if os.environ.get("TEXTFOLD_NO_MODELS") else (
                locate_codebuddy() if runtime == "codebuddy" else locate(binary, extra))
            row = {"runtime": runtime, "label": label, "binary": binary, "path": path, "runnable": runnable,
                   "version": None, "models": [], "error": None}
            if path:
                real = os.path.realpath(path)
                if real in seen:  # one program under two names counts once
                    row.update(path=None, error=f"same program as {seen[real]}")
                else:
                    seen[real] = label
                    if runtime == "codebuddy" and workbuddy_app(path):
                        row["label"] = "WorkBuddy"   # (its copy of CodeBuddy, on the WorkBuddy sign-in)
                    try:
                        out = _run([*argv(path), "--version"], 8, env_for(path))
                        row["version"] = ((out.stdout or out.stderr).strip().splitlines() or [""])[0][:80]
                    except Exception as e:
                        row["error"] = str(e)[:200]
                    if runnable:
                        try:
                            row["models"] = CATALOG[runtime](path)
                        except Exception as e:
                            row["error"] = f"model list: {e}"[:200]
            found.append(row)
        STATE.update(agents=found, scanned_at=time.time(), scanning=False)
    if log:
        have = [f"{a['label']} {a['version'] or ''}".strip() + (f" ({len(a['models'])} models)" if a["models"] else "")
                for a in found if a["path"]]
        log("Agents found: " + (", ".join(have) or "none"))
    return STATE


def models():
    return [m for a in STATE["agents"] if a["path"] and a["runnable"] for m in a["models"]]


def runtime_of(model_id):
    return next((rt for rt, p in PREFIX.items() if model_id.startswith(p)), None)


def path_of(runtime):
    a = next((a for a in STATE["agents"] if a["runtime"] == runtime and a["path"]), None)
    if a:
        return a["path"]
    if runtime == "codebuddy":
        return locate_codebuddy()
    binary, extra = next((r[2], r[3]) for r in REGISTRY if r[0] == runtime)
    return locate(binary, extra)


# ── agents the reader adds in the settings (#15) ─────────────────────
# A program that starts one of the agents above under another command (`mirasim claude` runs Claude Code, routed
# through Mirasim) is no agent of its own: the reader names its command and which one it speaks as, and it is run by
# that one's driver with that command in front. It is kept in the settings (models.py), its models as they were when it
# was saved, and its model ids have a prefix of their own, so the ids and cached replies of the agents above stay as
# they are.
CUSTOM = "custom/"
PROTOCOLS = {"claude-code": "Claude Code", "codex": "Codex", "pi": "Pi"}


def command_of(command):
    """A custom agent's command line as (the arguments that start it, the program's path); None when its program is not
    found. Its first word is looked for as an agent's program is (locate: PATH, the login shell's, the usual places) and
    started as one is (argv: npm's .cmd on Windows by node); the words after it follow, as written."""
    try:
        words = shlex.split(command or "", posix=not WIN)
    except ValueError:   # a quote left open
        return None
    if WIN:   # (not posix: a quoted word keeps its quotes)
        words = [w[1:-1] if len(w) > 1 and w[0] == w[-1] == '"' else w for w in words]
    if not words:
        return None
    first = os.path.expanduser(words[0])
    if os.path.dirname(first):   # a path, not a name
        path = first if os.path.isfile(first) else None
    else:
        path = locate(first)
    return ([*argv(path), *words[1:]], path) if path else None


def guess_protocol(command, version=""):
    """Which agent a command speaks as, where it says so: in its --version (Claude Code's ends "(Claude Code)", Codex's
    starts "codex-cli"), else in a word of the command (`mirasim claude`); None when neither does."""
    v = (version or "").lower()
    if "claude code" in v:
        return "claude-code"
    if "codex" in v:
        return "codex"
    try:
        words = shlex.split(command or "", posix=not WIN)
    except ValueError:
        words = (command or "").split()
    names = {"claude": "claude-code", "codex": "codex", "pi": "pi"}
    for w in reversed(words):   # (the last first: `mirasim claude` speaks as claude, whatever mirasim is)
        name = Path(w.strip('"')).name.split(".")[0].lower()
        if name in names:
            return names[name]
    return None


def probe_custom(command, protocol=None):
    """What a custom agent's command gives: its program, its version, the agent it speaks as (the one asked for, else
    guess_protocol) and that agent's models as it lists them, asked the way that agent is asked. A list it cannot give
    is an error beside the rest: the reader then writes the models in."""
    found = command_of(command)
    if not found:
        raise RuntimeError(f"{(command or '').split()[0] if (command or '').split() else 'The command'} was not found on this computer")
    cmd, path = found
    version, error = None, None
    try:
        out = _run([*cmd, "--version"], 15, env_for(path))
        text = (out.stdout or out.stderr).strip()
        version = (text.splitlines() or [""])[0][:80] or None
    except Exception as e:
        error = str(e)[:200]
    protocol = protocol if protocol in PROTOCOLS else guess_protocol(command, version)
    models = []
    if protocol:
        try:
            # (a name as the list gives it, without the agent's own name in front: "Claude Code · Sonnet" is "Sonnet")
            models = [{"id": m["model"], "name": m["name"].split(" · ", 1)[-1], "efforts": m.get("efforts") or []}
                      for m in CATALOG[protocol](path, cmd)]
        except Exception as e:
            error = f"model list: {e}"[:200]
    return {"path": path, "version": version, "protocol": protocol, "models": models, "error": error}


def custom_models(rows):
    """The models of the custom agents in the settings, as agents.models() gives the others'."""
    if os.environ.get("TEXTFOLD_NO_MODELS"):   # (as scan: none, in a test server or a CI run)
        return []
    return [{"id": f"{CUSTOM}{a['key']}/{m['id']}", "name": f"{a['name']} · {m.get('name') or m['id']}", "runtime": CUSTOM + a["key"],
             "model": m["id"], "efforts": m.get("efforts") or [], "defaultEffort": None}
            for a in rows or [] if a.get("protocol") in PROTOCOLS for m in a.get("models") or [] if m.get("id")]


# ── one-shot runs ─────────────────────────────────────────────────────
def _strict(schema):
    """OpenAI-style structured output wants every object closed and every property required."""
    if isinstance(schema, dict):
        s = {k: _strict(v) for k, v in schema.items()}
        if s.get("type") == "object" and "properties" in s:
            s["additionalProperties"] = False
            s["required"] = list(s["properties"])
        return s
    if isinstance(schema, list):
        return [_strict(x) for x in schema]
    return schema


def parse_json(text, schema=None):
    """The reply's JSON object, asked for in the prompt: the first object that parses (and has the schema's required
    keys), wherever it starts. A model that is not reasoning sometimes adds a line after the object, or repeats it."""
    text, dec = text or "", json.JSONDecoder()
    need = set((schema or {}).get("required", []))
    first = None
    for m in re.finditer(r"\{", text):
        try:
            obj, _ = dec.raw_decode(text, m.start())
        except ValueError:
            continue
        if isinstance(obj, dict) and need <= set(obj):
            return obj
        first = first if first is not None else obj
    if first is not None and not need:
        return first
    raise RuntimeError("no JSON in reply" if "{" not in text else "the reply's JSON does not parse")


def run_codex(model, prompt, schema, system, effort, timeout, tools=False, cmd=None):
    """(cmd: the command that starts it, a custom agent's; else the Codex found here)"""
    if cmd is None:
        path = path_of("codex")
        if not path:
            raise RuntimeError("Codex was not found on this computer")
        cmd = argv(path)
    with tempfile.TemporaryDirectory() as tmp:  # a scratch cwd: no repository, nothing to edit
        sf, of = Path(tmp) / "schema.json", Path(tmp) / "out.txt"
        sf.write_text(json.dumps(_strict(schema)), encoding="utf-8")
        # (tools: its web search too; the sandbox stays read-only)
        args = [*cmd, *(["--search"] if tools else []), "exec", "--ephemeral", "--skip-git-repo-check", "-s", "read-only",
                "-m", model, "--output-schema", str(sf), "-o", str(of)]
        # off is "none" (gpt-5.5 takes it, and refuses "minimal"); a model that refuses "none" runs at "low"
        for level in (["none", "low"] if effort == "off" else [effort]):
            r = subprocess.run(args + (["-c", f"model_reasoning_effort={level}"] if level else []) + ["-"], input=f"{system}\n\n{prompt}",
                               capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, env=child_env(), cwd=tmp)
            if not r.returncode and of.exists():
                break
        if r.returncode or not of.exists():
            raise RuntimeError(f"codex exec exited {r.returncode}: {(r.stderr or r.stdout)[-800:]}")
        return parse_json(of.read_text(encoding="utf-8")), None


def run_pi(model, prompt, schema, system, effort, timeout, tools=False, cmd=None):
    """(cmd: the command that starts it, a custom agent's; else the Pi found here)"""
    if cmd is None:
        path = path_of("pi")
        if not path:
            raise RuntimeError("Pi was not found on this computer")
        cmd = argv(path)
    if tools:   # its extensions' tools (the web, where one gives it), none of its own: it reads, writes and runs nothing
        args = [*cmd, "-p", "--mode", "json", "--no-builtin-tools", "--no-session", "--model", model, "--append-system-prompt", system]
    else:
        args = [*cmd, "-p", "--mode", "json", "--no-tools", "--no-session", "--no-context-files", "--no-extensions", "--no-skills",
                "--no-prompt-templates", "--model", model, "--system-prompt", system]
    if effort:
        args += ["--thinking", effort]
    body = f"{prompt}\n\nReply with one JSON object that matches this JSON Schema, and nothing else:\n{json.dumps(schema)}"
    for attempt in (1, 2):   # a reply whose JSON does not parse is asked for once more (it is a sample: the next one parses)
        try:
            return _pi_once(args, body, schema, timeout)
        except ValueError:
            if attempt == 2:
                raise


# (WorkBuddy 5.8 and later signed in, its copy of CodeBuddy all the same not: workbuddy_lite)
LITE_SIGNED_OUT = ("WorkBuddy 5.8 and later keep their sign-in to themselves, so TextFold cannot use it. Install CodeBuddy on its own "
                   "(npm install -g @tencent-ai/codebuddy-code), run codebuddy once and use /login, then restart TextFold: it uses "
                   "that one. Or choose another model in Settings. (WorkBuddy 5.8 起不把登录交给其他程序，TextFold 用不了。请单独安装 "
                   "CodeBuddy：npm install -g @tencent-ai/codebuddy-code，运行一次 codebuddy 并用 /login 登录，再重启 TextFold；"
                   "或在设置里换一个模型。)")


def run_codebuddy(model, prompt, schema, system, effort, timeout, tools=False):
    """claude -p's way, with CodeBuddy's differences: no --setting-sources (without the user's settings it has no
    sign-in and waits), the one tool its structured output needs (with none it has no StructuredOutput to answer
    with), and a list of messages for an output whose last is the result."""
    path = path_of("codebuddy")
    if not path:
        raise RuntimeError("CodeBuddy (WorkBuddy) was not found on this computer")
    if tools:   # the web (and its structured output) alone, allowed without asking; its own system prompt with ours after it
        web = "WebSearch,WebFetch,StructuredOutput"
        args = [*argv(path), "-p", "--model", model, "--tools", web, "--allowedTools", web, "--strict-mcp-config", "--no-session-persistence",
                "--output-format", "json", "--json-schema", json.dumps(schema), "--append-system-prompt", system]
    else:
        args = [*argv(path), "-p", "--model", model, "--tools", "StructuredOutput", "--strict-mcp-config", "--no-session-persistence",
                "--output-format", "json", "--json-schema", json.dumps(schema), "--system-prompt", system]
    if effort:
        args += ["--effort", "minimal" if effort == "off" else effort]   # (no level off: the least)
    env = env_for(path)
    signed_out = lambda r: "Authentication required" in r.stdout + r.stderr   # (it says so at once, exit 0, and does not wait)
    with tempfile.TemporaryDirectory() as tmp:   # a scratch cwd: no project, nothing to read
        r = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env=env, cwd=tmp)
        if signed_out(r) and env.pop("CODEBUDDY_CONFIG_DIR", None):   # not in WorkBuddy's settings: CodeBuddy's own sign-in
            r = subprocess.run(args, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=timeout, env=env, cwd=tmp)
    said = (r.stderr or r.stdout)[-800:]
    if signed_out(r) and workbuddy_lite(path):
        raise RuntimeError(LITE_SIGNED_OUT)
    if signed_out(r):
        raise RuntimeError("WorkBuddy is not signed in: open WorkBuddy and sign in (WorkBuddy 没有登录：打开 WorkBuddy 登录)"
                           if workbuddy_app(path) else "CodeBuddy is not signed in: run codebuddy once and use /login")
    try:
        got = json.loads(r.stdout)
    except ValueError:
        raise RuntimeError(f"codebuddy -p exited {r.returncode}: {said}")
    rows = got if isinstance(got, list) else [got]
    res = next((x for x in reversed(rows) if isinstance(x, dict) and x.get("type") == "result"), None)
    if res is None:
        raise RuntimeError(f"codebuddy -p exited {r.returncode} with no result: {said}")
    if res.get("is_error"):
        raise RuntimeError(f"codebuddy -p error: {str(res.get('result'))[:800]}")
    out = res.get("structured_output")
    if out is None:
        out = parse_json(res.get("result") or "", schema)
    return out, res.get("total_cost_usd")


def _pi_once(args, body, schema, timeout):
    with tempfile.TemporaryDirectory() as tmp:
        r = subprocess.run(args, input=body, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, env=child_env(), cwd=tmp)
    text, cost = None, None
    for line in r.stdout.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "agent_end":
            last = next((m for m in reversed(d.get("messages", [])) if m.get("role") == "assistant"), None)
            if last:
                text = "".join(c.get("text", "") for c in last.get("content", []) if c.get("type") == "text")
                cost = ((last.get("usage") or {}).get("cost") or {}).get("total")
                if last.get("stopReason") == "error" or last.get("errorMessage"):
                    raise RuntimeError(f"pi: {last.get('errorMessage') or 'error'}")
    if text is None:
        raise RuntimeError(f"pi exited {r.returncode}: {(r.stderr or r.stdout)[-800:]}")
    try:
        return parse_json(text, schema), cost
    except RuntimeError as e:
        raise ValueError(str(e))

