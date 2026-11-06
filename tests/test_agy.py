"""Antigravity CLI (agy): scanned on PATH and in its usual install folders, its models as `agy models` lists them
(tab-separated id and display name), clear sign-in and proxy error messages, and one-shot structured output via an
isolated `.agents/agents/textfold.md` (`inheritMcp: false`, `inheritCustomizations: false`, `tools: [finish]`) and
`--output-format stream-json` with `transcript_full.jsonl` fallback. No model, no network.

Run from the repo root:
    python3 -m unittest discover tests
"""
import json
import os
import subprocess
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
log = os.environ.get("FAKE_AGY_LOG")
if log:
    agent_md = ""
    p = os.path.join(os.getcwd(), ".agents", "agents", "textfold.md")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as fp:
            agent_md = fp.read()
    stdin = sys.stdin.read() if "--output-format" in args else ""
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"args": args, "cwd": os.getcwd(), "stdin": stdin, "agent_md": agent_md}) + "\\n")
if "--version" in args:
    print("agy version 1.4.12")
elif "models" in args:
    if os.environ.get("FAKE_AGY_SIGNED_OUT"):
        sys.stderr.write("Error: You are not logged into Antigravity. Please sign in.\\n")
        sys.exit(1)
    if os.environ.get("FAKE_AGY_NET_ERR"):
        sys.stderr.write("Post https://daily-cloudcode-pa.googleapis.com: connectex: connection timed out\\n")
        sys.exit(1)
    print("gemini-3.8-flash-high\\tGemini 3.8 Flash (High)")
    print("gemini-3.8-flash-low\\tGemini 3.8 Flash (Low)")
    print("claude-sonnet-5-5-high\\tClaude Sonnet 5.5 (High)")
elif "--output-format" in args:
    if os.environ.get("FAKE_AGY_SIGNED_OUT"):
        sys.stderr.write("Error: You are not logged into Antigravity.\\n")
        sys.exit(1)
    if os.environ.get("FAKE_AGY_ERROR"):
        print(json.dumps({"event": "result", "result": {"status": "ERROR", "error": "quota exhausted"}}), flush=True)
        sys.exit(1)
    model = args[args.index("--model") + 1] if "--model" in args else ""
    if os.environ.get("FAKE_AGY_CUTOFF_HIGH") and model.endswith("-high"):
        print(json.dumps({"event": "init", "conversation_id": "cid-cutoff"}), flush=True)
        print(json.dumps({"event": "step_update", "step_update": {"step_type": "error", "text": "exceeded the output token limit"}}), flush=True)
        print(json.dumps({"event": "step_update", "step_update": {"step_type": "error", "text": "exceeded the output token limit"}}), flush=True)
        sys.exit(1)
    if os.environ.get("FAKE_AGY_TRANSCRIPT_ONLY"):
        print(json.dumps({"event": "init", "conversation_id": "cid-disk"}), flush=True)
        sys.exit(0)
    print(json.dumps({"event": "init", "conversation_id": "cid-1"}), flush=True)
    print(json.dumps({"event": "step_update", "step_update": {
        "conversation_id": "cid-1", "step_index": 2, "state": "ACTIVE",
        "step_type": "tool", "tool_name": "finish",
        "tool_info": {"name": "finish", "parameters": {"takeaway": "Folded"}}}}), flush=True)
    print(json.dumps({"event": "step_update", "step_update": {
        "conversation_id": "cid-1", "step_index": 2, "state": "DONE", "step_type": "finish"}}), flush=True)
    print(json.dumps({"event": "result", "result": {
        "conversation_id": "cid-1", "status": "SUCCESS",
        "structured_output": {"takeaway": "Folded"}}}), flush=True)
'''


class Agy(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.home = self.tmp / "home"
        self.home.mkdir(parents=True)
        self.bin = self.tmp / "bin"
        self.bin.mkdir(parents=True)
        self.cli = self.bin / ("agy.exe" if os.name == "nt" else "agy")
        self.script = self.tmp / "fake_agy.py"
        self.script.write_text(textwrap.dedent(CLI), encoding="utf-8")
        self.cli.write_text(f"#!{sys.executable}\n" + textwrap.dedent(CLI), encoding="utf-8")
        self.cli.chmod(0o755)
        self.log = self.tmp / "calls.jsonl"
        orig_argv = agents.argv

        def fake_argv(path):
            if Path(path) == self.cli:
                return [sys.executable, str(self.script)]
            return orig_argv(path)

        for p in (mock.patch.object(agents, "HOME", self.home),
                  mock.patch.object(agents, "argv", side_effect=fake_argv),
                  mock.patch.dict(os.environ, {"FAKE_AGY_LOG": str(self.log)})):
            p.start()
            self.addCleanup(p.stop)
        for k in ("FAKE_AGY_SIGNED_OUT", "FAKE_AGY_NET_ERR", "FAKE_AGY_ERROR", "FAKE_AGY_CUTOFF_HIGH", "FAKE_AGY_TRANSCRIPT_ONLY"):
            os.environ.pop(k, None)

    def calls(self):
        return [json.loads(x) for x in self.log.read_text(encoding="utf-8").splitlines()] if self.log.exists() else []

    def test_models_list_and_errors(self):
        got = agents._agy_models(str(self.cli))
        self.assertEqual([m["id"] for m in got], [
            "agy/gemini-3.8-flash-high",
            "agy/gemini-3.8-flash-low",
            "agy/claude-sonnet-5-5-high",
        ])
        self.assertEqual(got[0]["name"], "Antigravity · Gemini 3.8 Flash (High)")
        self.assertEqual(got[0]["efforts"], [])
        with mock.patch.dict(os.environ, {"FAKE_AGY_SIGNED_OUT": "1"}):
            with self.assertRaisesRegex(RuntimeError, "Antigravity CLI is not signed in"):
                agents._agy_models(str(self.cli))
        with mock.patch.dict(os.environ, {"FAKE_AGY_NET_ERR": "1"}):
            with self.assertRaisesRegex(RuntimeError, "could not reach Google"):
                agents._agy_models(str(self.cli))

    def test_scan_and_resolve(self):
        with mock.patch.object(agents, "REGISTRY", [("agy", "Antigravity", "agy", [str(self.bin)], True)]), \
                mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TEXTFOLD_NO_MODELS", None)
            row = agents.scan()["agents"][0]
        self.assertEqual((row["runtime"], row["label"], row["version"], row["error"]),
                         ("agy", "Antigravity", "agy version 1.4.12", None))
        self.assertEqual(len(row["models"]), 3)
        spec = models.resolve("agy/gemini-3.8-flash-high", {"model": None})
        self.assertEqual((spec["kind"], spec["runtime"], spec["model"]), ("agent", "agy", "gemini-3.8-flash-high"))

    def test_isolated_agent_and_stream_json_generation(self):
        schema = {"type": "object", "properties": {"takeaway": {"type": "string"}}, "required": ["takeaway"]}
        with mock.patch.object(agents, "path_of", return_value=str(self.cli)):
            out, cost = agents.run_agy("gemini-3.8-flash-high", "Fold this.", schema, "The system. answer with JSON only.", "off", 30)
        self.assertEqual((out, cost), ({"takeaway": "Folded"}, None))
        call = self.calls()[-1]
        args = call["args"]
        self.assertIn("--agent", args)
        self.assertEqual(args[args.index("--agent") + 1], "textfold")
        self.assertEqual(args[args.index("--output-format") + 1], "stream-json")
        self.assertNotIn("--effort", args)
        self.assertIn("inheritMcp: false", call["agent_md"])
        self.assertIn("inheritCustomizations: false", call["agent_md"])
        self.assertIn("  - finish\n", call["agent_md"])
        self.assertNotIn("search_web", call["agent_md"])
        self.assertIn("answer by calling the finish tool with the JSON object only", call["agent_md"])
        self.assertIn("Fold this.", call["stdin"])

    def test_web_tools_enabled_when_asked(self):
        schema = {"type": "object", "properties": {"takeaway": {"type": "string"}}, "required": ["takeaway"]}
        with mock.patch.object(agents, "path_of", return_value=str(self.cli)):
            agents.run_agy("gemini-3.8-flash-low", "Look up.", schema, "System.", "low", 30, tools=True)
        md = self.calls()[-1]["agent_md"]
        self.assertIn("  - finish\n", md)
        self.assertIn("  - search_web\n", md)
        self.assertIn("  - read_url_content\n", md)

    def test_transcript_fallback_and_cutoff_stepdown(self):
        schema = {"type": "object", "properties": {"takeaway": {"type": "string"}}, "required": ["takeaway"]}
        tf = self.home / ".gemini" / "antigravity-cli" / "brain" / "cid-disk" / ".system_generated" / "logs" / "transcript_full.jsonl"
        tf.parent.mkdir(parents=True, exist_ok=True)
        tf.write_text(json.dumps({"step_index": 1, "tool_calls": [{"name": "finish", "args": {"takeaway": "From disk"}}]}) + "\n", encoding="utf-8")
        with mock.patch.object(agents, "path_of", return_value=str(self.cli)), \
                mock.patch.dict(os.environ, {"FAKE_AGY_TRANSCRIPT_ONLY": "1"}):
            out, _ = agents.run_agy("gemini-3.8-flash-high", "Fold.", schema, "Sys", "off", 30)
        self.assertEqual(out, {"takeaway": "From disk"})

        with mock.patch.object(agents, "path_of", return_value=str(self.cli)), \
                mock.patch.dict(os.environ, {"FAKE_AGY_CUTOFF_HIGH": "1"}):
            out, _ = agents.run_agy("gemini-3.8-flash-high", "Fold.", schema, "Sys", "off", 30)
        self.assertEqual(out, {"takeaway": "Folded"})
        models_tried = [c["args"][c["args"].index("--model") + 1] for c in self.calls()[-2:]]
        self.assertEqual(models_tried, ["gemini-3.8-flash-high", "gemini-3.8-flash-low"])

    def test_no_duplicate_retry_on_fixed_effort_model_failure(self):
        schema = {"type": "object", "properties": {"takeaway": {"type": "string"}}, "required": ["takeaway"]}
        spec = {"id": "agy/gemini-3.8-flash-high", "kind": "agent", "runtime": "agy",
                "model": "gemini-3.8-flash-high", "effort": "off"}
        with mock.patch.object(agents, "path_of", return_value=str(self.cli)), \
                mock.patch.dict(os.environ, {"FAKE_AGY_ERROR": "1"}):
            with self.assertRaisesRegex(RuntimeError, "quota exhausted"):
                llm.call("Fold.", schema, spec, self.tmp / "cache")
        self.assertEqual(len(self.calls()), 1)


if __name__ == "__main__":
    unittest.main()
