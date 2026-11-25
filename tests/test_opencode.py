"""opencode: scanned like the other agents, its models as `opencode models` lists them (provider/model a line), and one
answer through `opencode run --format json` with an agent of our own (`.opencode/agent/textfold.md`: our system
prompt, every tool off), the last message's text parsed as JSON, the session deleted after; its free-limit error said
plainly. No model, no network.

Run from the repo root:
    python3 -m unittest discover tests
"""
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from textfold import agents, llm, models

CLI = '''\
import json, os, sys
args = sys.argv[1:]
log = os.environ.get("FAKE_OC_LOG")
agent_md = ""
p = os.path.join(os.getcwd(), ".opencode", "agent", "textfold.md")
if os.path.isfile(p):
    with open(p, encoding="utf-8") as fp:
        agent_md = fp.read()
stdin = sys.stdin.read() if args[:1] == ["run"] else ""
project = ""
q = os.path.join(os.getcwd(), "opencode.json")
if os.path.isfile(q):
    with open(q, encoding="utf-8") as fp:
        project = fp.read()
if log:
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"args": args, "stdin": stdin, "agent_md": agent_md,
                            "quiet": os.environ.get("OPENCODE_DISABLE_CLAUDE_CODE"),
                            "pwd_is_cwd": os.environ.get("PWD") == os.getcwd(), "project": project}) + "\\n")
def say(d):
    print(json.dumps(d), flush=True)
mode = os.environ.get("FAKE_OC_MODE", "")
if "--version" in args:
    print("opencode v2.4.1")
elif args[:2] == ["debug", "config"]:
    if os.environ.get("FAKE_OC_MCP"):   # (opencode's own form of the reader's config file)
        say([{"type": "document", "path": "/x/opencode.jsonc", "info": {"mcp": {"servers": {
                "notes": {"type": "local", "command": ["/x/notes-mcp"], "disabled": False}}}}},
             {"type": "document", "path": "/x/old.json", "info": {"mcp": {"web": {"type": "remote", "url": "https://x", "enabled": True}}}},
             {"type": "directory", "path": "/x"}])
    else:
        say([{"type": "directory", "path": "/x"}])
elif args[:3] == ["api", "GET", "/api/model"]:
    if os.environ.get("FAKE_OC_API"):   # (else as an opencode without the server's list: nothing that parses)
        v = lambda *ids: [{"id": i, "settings": {"reasoningEffort": i}} for i in ids]
        say({"location": {"directory": os.getcwd()}, "data": [
            {"id": "big-pickle", "providerID": "opencode", "name": "Big Pickle", "variants": [], "capabilities": {"input": ["text"]}},
            {"id": "deepseek-v4.1-flash-free", "providerID": "opencode", "name": "DeepSeek V4.1 Flash Free",
             "variants": v("low", "high", "max"), "capabilities": {"input": ["text", "image"]}},
            {"id": "nemotron-free", "providerID": "opencode", "name": "Nemotron Free", "variants": v("none", "minimal", "low", "high")},
            {"id": "gone", "providerID": "opencode", "name": "Gone", "enabled": False},
        ]})
elif args[:1] == ["models"]:
    print("opencode/big-pickle")
    print("opencode/mimo-v2.6-flash-free")
    print("deepseek/deepseek-flash")
    print("")
elif args[:2] == ["session", "delete"]:
    print("Session " + args[2] + " deleted")
elif args[:1] == ["run"]:
    sid = "ses_1"
    say({"type": "step_start", "sessionID": sid, "part": {"type": "step-start", "messageID": "msg_1"}})
    count = os.path.join(os.path.dirname(log), "count")
    n = int(open(count).read()) + 1 if os.path.exists(count) else 1
    open(count, "w").write(str(n))
    if mode == "free-limit" or (mode == "refuse-once" and n == 1):
        say({"type": "error", "sessionID": sid, "error": {"type": "provider.free-limit", "status": 429,
             "message": "Free usage limit reached.", "retryAfterMs": 7200000}})
        sys.exit(1)
    if mode == "bad-json" and n == 1:
        reply = "I think the takeaway is folded."
    else:
        reply = '{"takeaway": "Folded"}'
    say({"type": "text", "sessionID": sid, "part": {"type": "text", "messageID": "msg_1", "text": "thinking out loud"}})
    say({"type": "text", "sessionID": sid, "part": {"type": "text", "messageID": "msg_2", "text": reply}})
    say({"type": "step_finish", "sessionID": sid, "part": {"type": "step-finish", "reason": "stop", "cost": 0.002}})
'''

SCHEMA = {"type": "object", "properties": {"takeaway": {"type": "string"}}, "required": ["takeaway"]}


class Opencode(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.cli = self.bin / ("opencode.exe" if os.name == "nt" else "opencode")
        self.script = self.tmp / "fake_opencode.py"
        self.script.write_text(textwrap.dedent(CLI), encoding="utf-8")
        self.cli.write_text(f"#!{sys.executable}\n" + textwrap.dedent(CLI), encoding="utf-8")
        self.cli.chmod(0o755)
        self.log = self.tmp / "calls.jsonl"
        orig_argv = agents.argv

        def fake_argv(path):
            return [sys.executable, str(self.script)] if Path(path) == self.cli else orig_argv(path)

        state = dict(agents.STATE)
        self.addCleanup(agents.STATE.update, state)
        for p in (mock.patch.object(agents, "argv", side_effect=fake_argv),
                  mock.patch.object(agents, "path_of", return_value=str(self.cli)),
                  mock.patch.dict(os.environ, {"FAKE_OC_LOG": str(self.log)})):
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(agents, "OPENCODE_REFUSED_WAIT", (0, 0))
        p.start()
        self.addCleanup(p.stop)
        for k in ("FAKE_OC_MODE", "FAKE_OC_API", "FAKE_OC_MCP", "TEXTFOLD_NO_MODELS"):
            os.environ.pop(k, None)
        agents.OPENCODE_VARIANTS.clear()   # (what the server listed, kept between calls: asked afresh in each test)
        agents._opencode_asked.clear()
        agents._opencode_mcp.clear()
        agents._opencode_mcp_asked.clear()

    def calls(self):
        if agents._opencode_cleaner is not None:   # (sessions are deleted beside the calls: those asked for, done)
            agents._opencode_cleaner.submit(lambda: None).result()
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def test_models_listed_and_scanned(self):
        got = agents._opencode_models(str(self.cli))
        self.assertEqual([m["id"] for m in got],
                         ["opencode/opencode/big-pickle", "opencode/opencode/mimo-v2.6-flash-free", "opencode/deepseek/deepseek-flash"])
        with mock.patch.object(agents, "REGISTRY", [("opencode", "opencode", "opencode", [str(self.bin)], True)]):
            row = agents.scan()["agents"][0]
        self.assertEqual((row["runtime"], row["version"], row["error"], len(row["models"])), ("opencode", "opencode v2.4.1", None, 3))

    def test_models_from_server_with_variants(self):
        os.environ["FAKE_OC_API"] = "1"
        got = agents._opencode_models(str(self.cli))
        self.assertEqual([m["id"] for m in got], ["opencode/opencode/big-pickle", "opencode/opencode/deepseek-v4.1-flash-free",
                                                  "opencode/opencode/nemotron-free"])   # (not the one switched off)
        ds = got[1]
        self.assertEqual((ds["name"], ds["model"], ds["efforts"], ds["vision"]),
                         ("opencode · DeepSeek V4.1 Flash Free (opencode)", "opencode/deepseek-v4.1-flash-free", ["low", "high", "max"], True))
        self.assertFalse(any(c["args"][:1] == ["models"] for c in self.calls()))

    def test_effort_asks_for_the_nearest_variant(self):
        os.environ["FAKE_OC_API"] = "1"
        cases = [("opencode/nemotron-free", "off", "opencode/nemotron-free#none"),
                 ("opencode/deepseek-v4.1-flash-free", "off", "opencode/deepseek-v4.1-flash-free#low"),
                 ("opencode/deepseek-v4.1-flash-free", "medium", "opencode/deepseek-v4.1-flash-free#low"),
                 ("opencode/deepseek-v4.1-flash-free", "max", "opencode/deepseek-v4.1-flash-free#max"),
                 ("opencode/deepseek-v4.1-flash-free", None, "opencode/deepseek-v4.1-flash-free"),
                 ("opencode/big-pickle", "off", "opencode/big-pickle")]   # (no variants: its own default)
        for model, effort, want in cases:
            agents.run_opencode(model, "Fold.", SCHEMA, "Sys", effort, 30)
        runs = [c["args"] for c in self.calls() if c["args"][:1] == ["run"]]
        self.assertEqual([a[a.index("-m") + 1] for a in runs], [w for _, _, w in cases])
        # the server asked once for the variants, not once a call
        self.assertEqual(sum(c["args"][:1] == ["api"] for c in self.calls()), 1)

    def test_no_server_list_no_variant(self):
        agents.run_opencode("opencode/nemotron-free", "Fold.", SCHEMA, "Sys", "off", 30)
        run = next(c["args"] for c in self.calls() if c["args"][:1] == ["run"])
        self.assertEqual(run[run.index("-m") + 1], "opencode/nemotron-free")

    def test_mcp_servers_switched_off(self):
        os.environ["FAKE_OC_MCP"] = "1"
        for _ in range(2):
            agents.run_opencode("opencode/big-pickle", "Fold.", SCHEMA, "Sys", None, 30)
        runs = [c for c in self.calls() if c["args"][:1] == ["run"]]
        self.assertEqual(json.loads(runs[0]["project"]), {"mcp": {
            "notes": {"type": "local", "command": ["/x/notes-mcp"], "enabled": False},
            "web": {"type": "remote", "url": "https://x", "enabled": False}}})
        self.assertEqual(runs[1]["project"], runs[0]["project"])
        self.assertEqual(sum(c["args"][:2] == ["debug", "config"] for c in self.calls()), 1)   # (asked once)

    def test_no_mcp_no_project_file(self):
        agents.run_opencode("opencode/big-pickle", "Fold.", SCHEMA, "Sys", None, 30)
        self.assertEqual(next(c for c in self.calls() if c["args"][:1] == ["run"])["project"], "")

    def test_resolve_keeps_provider_and_model(self):
        spec = models.resolve("opencode/opencode/big-pickle", {"model": None})
        self.assertEqual((spec["kind"], spec["runtime"], spec["model"]), ("agent", "opencode", "opencode/big-pickle"))
        self.assertEqual(agents.runtime_of("opencode/deepseek/deepseek-flash"), "opencode")

    def test_one_answer(self):
        out, cost = agents.run_opencode("opencode/big-pickle", "Fold this.", SCHEMA, "The system.", "high", 30)
        self.assertEqual((out, cost), ({"takeaway": "Folded"}, 0.002))
        run, delete = [c for c in self.calls() if c["args"][:1] in (["run"], ["session"])]
        self.assertEqual(run["args"][:8], ["run", "--format", "json", "--agent", "textfold", "-m", "opencode/big-pickle", "--archived"])
        self.assertNotIn("--auto", run["args"])   # (it would approve what asks)
        # opencode's own provider: its tools kept in the request, every one on ask (a non-interactive run rejects it)
        self.assertIn('permission:\n  "*": ask\n---', run["agent_md"])
        self.assertNotIn("\ntools:", run["agent_md"].split("---")[1])
        self.assertIn("The system.", run["agent_md"])
        self.assertIn("Call no tools", run["agent_md"])
        self.assertNotIn("webfetch", run["agent_md"])
        self.assertIn("Fold this.", run["stdin"])
        self.assertIn('"required": ["takeaway"]', run["stdin"])
        self.assertEqual(run["quiet"], "1")   # (not Claude Code's CLAUDE.md and skills)
        self.assertTrue(run["pwd_is_cwd"])    # (opencode finds its project, and our agent, by PWD)
        self.assertEqual(delete["args"], ["session", "delete", "ses_1"])

    def test_own_provider_gets_no_tools(self):
        out, _ = agents.run_opencode("deepseek/deepseek-flash", "Fold.", SCHEMA, "Sys", None, 30)
        self.assertEqual(out, {"takeaway": "Folded"})
        md = next(c for c in self.calls() if c["args"][:1] == ["run"])["agent_md"]
        self.assertIn('tools:\n  "*": false\n---', md)
        self.assertNotIn("permission:", md)
        self.assertNotIn("Call no tools", md)

    def test_web_tools_when_asked(self):
        agents.run_opencode("deepseek/deepseek-flash", "Look up.", SCHEMA, "Sys", None, 30, tools=True)
        md = next(c for c in self.calls() if c["args"][:1] == ["run"])["agent_md"]
        self.assertIn('tools:\n  "*": false\n  webfetch: true\n  websearch: true\n---', md)
        agents.run_opencode("opencode/big-pickle", "Look up.", SCHEMA, "Sys", None, 30, tools=True)
        md = [c for c in self.calls() if c["args"][:1] == ["run"]][1]["agent_md"]
        # (the allows after the ask: opencode's rules go by the last that matches)
        self.assertIn('permission:\n  "*": ask\n  webfetch: allow\n  websearch: allow\n---', md)

    def test_refused_asked_again_then_said_plainly(self):
        os.environ["FAKE_OC_MODE"] = "free-limit"
        with self.assertRaisesRegex(RuntimeError, r"free models refused the call 3 times \(their usage limit, or too many calls at once\)"):
            agents.run_opencode("opencode/big-pickle", "Fold.", SCHEMA, "Sys", None, 30)
        calls = self.calls()
        self.assertEqual(sum(c["args"][:1] == ["run"] for c in calls), 3)
        self.assertEqual(sum(c["args"][:2] == ["session", "delete"] for c in calls), 3)   # (each try's session)

    def test_refused_once_then_answered(self):
        os.environ["FAKE_OC_MODE"] = "refuse-once"
        out, _ = agents.run_opencode("opencode/big-pickle", "Fold.", SCHEMA, "Sys", None, 30)
        self.assertEqual(out, {"takeaway": "Folded"})
        self.assertEqual(sum(c["args"][:1] == ["run"] for c in self.calls()), 2)

    def test_jobs_per_agent(self):
        self.assertEqual(agents.jobs_for({"kind": "agent", "runtime": "opencode"}), 8)
        self.assertEqual(agents.jobs_for({"kind": "agent", "runtime": "custom", "protocol": "opencode"}), 8)
        self.assertEqual(agents.jobs_for({"kind": "agent", "runtime": "claude-code"}), 4)
        self.assertEqual(agents.jobs_for({"kind": "api", "protocol": "openai"}), 4)

    def test_unparsable_reply_asked_again(self):
        os.environ["FAKE_OC_MODE"] = "bad-json"
        out, _ = agents.run_opencode("opencode/big-pickle", "Fold.", SCHEMA, "Sys", None, 30)
        self.assertEqual(out, {"takeaway": "Folded"})
        self.assertEqual(sum(c["args"][:1] == ["run"] for c in self.calls()), 2)

    def test_llm_dispatch(self):
        spec = {"id": "opencode/opencode/big-pickle", "kind": "agent", "runtime": "opencode", "model": "opencode/big-pickle"}
        out, _ = llm._once("Fold.", SCHEMA, spec, 30, "Sys")
        self.assertEqual(out, {"takeaway": "Folded"})
        spec = {"id": "custom/mine/opencode/big-pickle", "kind": "agent", "runtime": "custom", "protocol": "opencode",
                "command": str(self.cli), "agent": "Mine", "model": "opencode/big-pickle"}
        out, _ = llm._once("Fold.", SCHEMA, spec, 30, "Sys")
        self.assertEqual(out, {"takeaway": "Folded"})


if __name__ == "__main__":
    unittest.main()
