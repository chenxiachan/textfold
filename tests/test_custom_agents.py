"""Custom agents (#15): a command the reader adds in the settings, such as `mirasim claude`, run by the driver of the
agent it speaks as (Claude Code, Codex or Pi) with that command in front. Its command is split and its program found as
an agent's is; the agent it speaks as is told by its --version or a word of its command; its models are asked for as
that agent's are, and kept in the settings with any written in by hand; its model ids have a prefix of their own, so
the agents' own ids and cached replies stay as they were. A fake wrapper (a Python script, one subcommand per agent)
stands in; no model, no network.

Run from the repo root:
    python3 -m unittest discover tests
"""
import hashlib
import json
import os
import site
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from textfold import agents, llm, models

ROOT = Path(__file__).resolve().parent.parent

# `wrap claude …` speaks as Claude Code, `wrap codex …` as Codex, `wrap pi …` as Pi; FAKE_LOG records each call
WRAP = '''\
import json, os, sys
sub, args = (sys.argv[1:2] or [""])[0], sys.argv[2:]
log = os.environ.get("FAKE_LOG")
def record(stdin=""):
    if log:
        with open(log, "a", encoding="utf-8") as f:
            f.write(json.dumps({"sub": sub, "args": args, "cwd": os.getcwd(), "stdin": stdin}) + "\\n")
if os.environ.get("FAKE_BROKEN_LIST") and ("--help" in args or "app-server" in args or "rpc" in args):
    sys.exit("no list today")
if sub == "claude":
    if "--version" in args:
        print("2.1.304 (Claude Code)"); print("wrap · routed", file=sys.stderr)
    elif "--help" in args:
        print("  --effort <level>    Effort level for the session (low, medium, high)")
    elif "-p" in args:
        record(sys.stdin.read())
        print(json.dumps({"is_error": False, "result": "", "structured_output": {"takeaway": "Folded"}, "total_cost_usd": 0.01}))
elif sub == "codex":
    if "--version" in args:
        print("codex-cli 0.50.0")
    elif "app-server" in args:
        for line in sys.stdin:
            m = json.loads(line)
            if m.get("method") == "model/list":
                print(json.dumps({"id": m["id"], "result": {"data": [
                    {"model": "gpt-5.5", "displayName": "GPT-5.5", "isDefault": True,
                     "supportedReasoningEfforts": [{"reasoningEffort": "low"}, {"reasoningEffort": "high"}]},
                    {"model": "old", "hidden": True}]}}), flush=True)
                break
            elif "id" in m:
                print(json.dumps({"id": m["id"], "result": {}}), flush=True)
    elif "exec" in args:
        stdin = sys.stdin.read()
        record(stdin)
        out = args[args.index("-o") + 1]
        open(out, "w").write(json.dumps({"takeaway": "Folded by codex"}))
elif sub == "pi":
    if "--version" in args:
        print("0.9.1")
    elif "--help" in args:
        print("  --thinking <level>   Set thinking level: off, low, high")
    elif "rpc" in args:
        for line in sys.stdin:
            m = json.loads(line)
            print(json.dumps({"id": m["id"], "data": {"models": [
                {"provider": "moonshot", "id": "kimi-k2", "name": "Kimi K2", "reasoning": True}]}}), flush=True)
            break
    elif "-p" in args:
        record(sys.stdin.read())
        print(json.dumps({"type": "agent_end", "messages": [{"role": "assistant", "content": [
            {"type": "text", "text": '{"takeaway": "Folded by pi"}'}], "usage": {"cost": {"total": 0}}}]}))
else:
    sys.exit("usage: wrap claude|codex|pi")
'''
SCHEMA = {"type": "object", "properties": {"takeaway": {"type": "string"}}, "required": ["takeaway"]}


def script(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)
    return path


def custom(key, protocol, models_=("sonnet",), command=None, name=None):
    return {"key": key, "name": name or key.capitalize(), "command": command or f"wrap {protocol.split('-')[0]}",
            "protocol": protocol, "models": [{"id": m, "name": m, "efforts": []} for m in models_]}


@unittest.skipIf(os.name == "nt", "the fake wrapper is a POSIX script")
class Fake(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bin = self.tmp / "bin"
        self.wrap = script(self.bin / "wrap", WRAP)
        self.log = self.tmp / "log.jsonl"
        for p in (mock.patch.object(agents, "search_dirs", return_value=[str(self.bin)]),
                  mock.patch.dict(os.environ, {"FAKE_LOG": str(self.log)})):
            p.start()
            self.addCleanup(p.stop)
        os.environ.pop("TEXTFOLD_NO_MODELS", None)

    def calls(self):
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []


class Command(Fake):
    def test_a_command_with_its_words(self):
        cmd, path = agents.command_of("wrap claude --flag")
        self.assertEqual((cmd, path), ([str(self.wrap), "claude", "--flag"], str(self.wrap)))

    def test_a_full_path_and_a_home(self):
        self.assertEqual(agents.command_of(f'"{self.wrap}" claude')[0], [str(self.wrap), "claude"])
        with mock.patch.dict(os.environ, {"HOME": str(self.tmp)}):
            self.assertEqual(agents.command_of("~/bin/wrap pi")[0], [str(self.wrap), "pi"])

    def test_not_found(self):
        self.assertIsNone(agents.command_of("nowhere claude"))
        self.assertIsNone(agents.command_of(str(self.tmp / "gone") + " claude"))
        self.assertIsNone(agents.command_of(""))
        self.assertIsNone(agents.command_of('wrap "claude'))   # (a quote left open)

    def test_windows_shim_is_run_by_node(self):
        """On Windows a command's program goes through argv(): npm's .cmd shim is run by node (test_agents_windows)."""
        with mock.patch.object(agents, "locate", return_value=r"C:\npm\mirasim.cmd"), \
                mock.patch.object(agents, "argv", return_value=["node.exe", r"C:\npm\mirasim.js"]) as argv:
            self.assertEqual(agents.command_of("mirasim claude")[0], ["node.exe", r"C:\npm\mirasim.js", "claude"])
            argv.assert_called_once_with(r"C:\npm\mirasim.cmd")

    def test_which_agent_it_speaks_as(self):
        self.assertEqual(agents.guess_protocol("anything", "2.1.304 (Claude Code)"), "claude-code")
        self.assertEqual(agents.guess_protocol("anything", "codex-cli 0.50.0"), "codex")
        self.assertEqual(agents.guess_protocol("mirasim claude", "mirasim 0.0.584"), "claude-code")
        self.assertEqual(agents.guess_protocol("/opt/x/bin/pi.cmd --mode x", ""), "pi")
        self.assertEqual(agents.guess_protocol("router codex", None), "codex")
        self.assertIsNone(agents.guess_protocol("mystery run", "1.0"))


class Probe(Fake):
    def test_claude_code(self):
        r = agents.probe_custom("wrap claude")
        self.assertEqual((r["protocol"], r["version"], r["error"]), ("claude-code", "2.1.304 (Claude Code)", None))
        self.assertEqual([m["id"] for m in r["models"]], ["fable", "opus", "sonnet", "haiku"])
        self.assertEqual(r["models"][2], {"id": "sonnet", "name": "Sonnet", "efforts": ["off", "low", "medium", "high"]})

    def test_codex(self):
        r = agents.probe_custom("wrap codex")
        self.assertEqual((r["protocol"], r["models"]), ("codex", [{"id": "gpt-5.5", "name": "GPT-5.5", "efforts": ["low", "high"]}]))

    def test_pi(self):
        r = agents.probe_custom("wrap pi")
        self.assertEqual((r["protocol"], r["models"]), ("pi", [{"id": "moonshot/kimi-k2", "name": "Kimi K2", "efforts": ["off", "low", "high"]}]))

    def test_the_one_asked_for_wins(self):
        self.assertEqual(agents.probe_custom("wrap claude", "pi")["protocol"], "pi")

    def test_a_list_it_cannot_give(self):
        with mock.patch.dict(os.environ, {"FAKE_BROKEN_LIST": "1"}):
            r = agents.probe_custom("wrap codex")
        self.assertEqual((r["protocol"], r["models"]), ("codex", []))
        self.assertIn("no list today", r["error"])

    def test_not_found(self):
        with self.assertRaisesRegex(RuntimeError, "nowhere was not found"):
            agents.probe_custom("nowhere claude")


class Catalog(Fake):
    def settings(self, *rows):
        return {"model": None, "ask_model": None, "ask_effort": None, "efforts": {}, "providers": [], "custom_agents": list(rows)}

    def test_ids_of_their_own(self):
        s = self.settings(custom("mirasim", "claude-code", ["sonnet", "kimi-k2"], name="Mirasim"), custom("pirouter", "pi", ["moonshot/kimi-k2"]))
        with mock.patch.object(agents, "models", return_value=[]), mock.patch.object(models, "provider_list", return_value=[]):
            cat, _ = models.catalog(s)
        self.assertEqual([(m["id"], m["name"], m["group"]) for m in cat], [
            ("custom/mirasim/sonnet", "Mirasim · sonnet", "custom/mirasim"),
            ("custom/mirasim/kimi-k2", "Mirasim · kimi-k2", "custom/mirasim"),
            ("custom/pirouter/moonshot/kimi-k2", "Pirouter · moonshot/kimi-k2", "custom/pirouter")])

    def test_resolve(self):
        s = self.settings(custom("mirasim", "claude-code", command="mirasim claude"), custom("pirouter", "pi", ["moonshot/kimi-k2"]))
        spec = models.resolve("custom/mirasim/sonnet", s)
        self.assertEqual(spec, {"id": "custom/mirasim/sonnet", "kind": "agent", "runtime": "custom", "protocol": "claude-code",
                                "command": "mirasim claude", "agent": "Mirasim", "model": "sonnet", "effort": None})
        self.assertEqual(models.resolve("custom/pirouter/moonshot/kimi-k2", s)["model"], "moonshot/kimi-k2")   # (Pi's provider/model)
        with self.assertRaisesRegex(RuntimeError, "custom agent is gone"):
            models.resolve("custom/gone/sonnet", s)

    def test_the_agents_own_ids_unchanged(self):
        s = self.settings(custom("claude", "claude-code"))   # (a key that is an agent's name changes nothing of the agent's)
        spec = models.resolve("claude/claude-code/sonnet", s)
        self.assertEqual((spec["runtime"], spec["model"]), ("claude-code", "sonnet"))
        self.assertEqual(models.resolve("sonnet", s)["id"], "claude/claude-code/sonnet")

    def test_none_without_models(self):
        with mock.patch.dict(os.environ, {"TEXTFOLD_NO_MODELS": "1"}):
            self.assertEqual(agents.custom_models([custom("mirasim", "claude-code")]), [])
        self.assertEqual(agents.custom_models([{**custom("x", "claude-code"), "protocol": "gemini"}]), [])


class Generate(Fake):
    def spec(self, protocol, model, command):
        return {"id": f"custom/w/{model}", "kind": "agent", "runtime": "custom", "protocol": protocol, "command": command,
                "agent": "W", "model": model, "effort": None}

    def test_claude_code(self):
        with tempfile.TemporaryDirectory() as cache:
            rec, cached = llm.call("Fold this.", SCHEMA, self.spec("claude-code", "sonnet", "wrap claude"), Path(cache))
        self.assertEqual((rec["out"], rec["cost"], cached), ({"takeaway": "Folded"}, 0.01, False))
        (c,) = self.calls()
        self.assertEqual((c["sub"], c["args"][:3], c["stdin"]), ("claude", ["-p", "--model", "sonnet"], "Fold this."))
        self.assertIn("--json-schema", c["args"])
        self.assertNotEqual(Path(c["cwd"]).resolve(), Path.cwd().resolve())   # (a scratch folder, as Claude Code's own)

    def test_codex(self):
        with tempfile.TemporaryDirectory() as cache:
            rec, _ = llm.call("Fold this.", SCHEMA, self.spec("codex", "gpt-5.5", "wrap codex"), Path(cache))
        self.assertEqual(rec["out"], {"takeaway": "Folded by codex"})
        (c,) = self.calls()
        self.assertEqual((c["sub"], c["args"][0], c["args"][c["args"].index("-m") + 1]), ("codex", "exec", "gpt-5.5"))
        self.assertTrue(c["stdin"].endswith("Fold this."))

    def test_pi(self):
        with tempfile.TemporaryDirectory() as cache:
            rec, _ = llm.call("Fold this.", SCHEMA, self.spec("pi", "moonshot/kimi-k2", "wrap pi"), Path(cache))
        self.assertEqual(rec["out"], {"takeaway": "Folded by pi"})
        (c,) = self.calls()
        self.assertEqual((c["sub"], c["args"][c["args"].index("--model") + 1]), ("pi", "moonshot/kimi-k2"))
        self.assertIn("--no-tools", c["args"])

    def test_its_command_gone(self):
        with tempfile.TemporaryDirectory() as cache, self.assertRaisesRegex(RuntimeError, "W: nowhere was not found"):
            llm.call("Fold this.", SCHEMA, self.spec("claude-code", "sonnet", "nowhere claude"), Path(cache))

    def test_a_cache_of_its_own(self):
        """Claude Code's replies are kept under a key of its bare model name (as they always were); a custom agent's
        under its whole id, so the same model through another command is asked, not read from Claude Code's."""
        with tempfile.TemporaryDirectory() as cache:
            builtin = {"id": "claude/claude-code/sonnet", "kind": "agent", "runtime": "claude-code", "model": "sonnet", "effort": None}
            key = hashlib.sha256(json.dumps([llm.SYSTEM, "Fold this.", SCHEMA, "sonnet"]).encode()).hexdigest()[:24]
            (Path(cache) / f"{key}.json").write_text(json.dumps({"out": {"takeaway": "Claude Code's"}, "cost": 0, "models": ["claude/claude-code/sonnet"]}))
            rec, cached = llm.call("Fold this.", SCHEMA, builtin, Path(cache))
            self.assertEqual((rec["out"]["takeaway"], cached), ("Claude Code's", True))
            rec, cached = llm.call("Fold this.", SCHEMA, self.spec("claude-code", "sonnet", "wrap claude"), Path(cache))
            self.assertEqual((rec["out"]["takeaway"], cached), ("Folded", False))
            self.assertEqual(len(self.calls()), 1)
            self.assertEqual(llm.call("Fold this.", SCHEMA, self.spec("claude-code", "sonnet", "wrap claude"), Path(cache))[1], True)


@unittest.skipIf(os.name == "nt", "the fake wrapper is a POSIX script")
class Routes(unittest.TestCase):
    """Save, edit, remove and probe, on a server of its own with this test's settings (HOME is the test's)."""

    @classmethod
    def setUpClass(cls):
        import socket
        cls.home = Path(tempfile.mkdtemp())
        cls.wrap = script(cls.home / "bin" / "wrap", WRAP)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.port = s.getsockname()[1]
        env = {**os.environ, "HOME": str(cls.home), "USERPROFILE": str(cls.home), "TEXTFOLD_DATA": str(cls.home / "data"),
               "TEXTFOLD_NO_MODELS": "1", "PYTHONUSERBASE": site.getuserbase()}
        cls.proc = subprocess.Popen([sys.executable, "-m", "textfold", "serve", "--port", str(cls.port)], cwd=ROOT, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(150):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/api/papers", timeout=1)
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)

    def call(self, path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def settings(self):
        return json.loads((self.home / ".textfold" / "settings.json").read_text(encoding="utf-8"))

    def test_save_edit_remove(self):
        code, d = self.call("/api/agents/save", {"name": "Mirasim", "command": " mirasim claude ", "protocol": "claude-code",
                                                 "models": [{"id": "sonnet", "name": "Sonnet", "efforts": ["low"]}, "kimi-k2", "kimi-k2", ""]})
        self.assertEqual(code, 200, d)
        (a,) = [x for x in d["customAgents"] if x["name"] == "Mirasim"]
        self.assertEqual(a, {"key": "mirasim", "name": "Mirasim", "command": "mirasim claude", "protocol": "claude-code", "models": ["sonnet", "kimi-k2"]})
        self.assertEqual(d["protocols"], agents.PROTOCOLS)
        saved = next(x for x in self.settings()["custom_agents"] if x["key"] == "mirasim")
        self.assertEqual(saved["models"], [{"id": "sonnet", "name": "Sonnet", "efforts": ["low"]}, {"id": "kimi-k2", "name": "kimi-k2", "efforts": []}])
        # another of the same name: a key of its own; an edit keeps its key
        d = self.call("/api/agents/save", {"name": "Mirasim", "command": "mirasim codex", "protocol": "codex", "models": ["gpt-5.5"]})[1]
        self.assertEqual(sorted(x["key"] for x in d["customAgents"]), ["mirasim", "mirasim-2"])
        d = self.call("/api/agents/save", {"key": "mirasim-2", "name": "Mirasim Codex", "command": "mirasim codex", "protocol": "codex", "models": ["gpt-5.5"]})[1]
        self.assertEqual(sorted((x["key"], x["name"]) for x in d["customAgents"]), [("mirasim", "Mirasim"), ("mirasim-2", "Mirasim Codex")])
        for key in ("mirasim", "mirasim-2"):
            d = self.call("/api/agents/remove", {"key": key})[1]
        self.assertEqual(d["customAgents"], [])

    def test_refused(self):
        self.assertEqual(self.call("/api/agents/save", {"name": "X", "command": "", "protocol": "claude-code", "models": ["a"]})[0], 400)
        self.assertEqual(self.call("/api/agents/save", {"name": "X", "command": "x", "protocol": "gemini", "models": ["a"]})[0], 400)

    def test_probe(self):
        code, r = self.call("/api/agents/probe", {"command": f"{self.wrap} claude"})
        self.assertEqual((code, r["protocol"], len(r["models"])), (200, "claude-code", 4), r)
        code, r = self.call("/api/agents/probe", {"command": "nowhere claude"})
        self.assertEqual(code, 400)
        self.assertIn("not found", r["error"])


if __name__ == "__main__":
    unittest.main()
