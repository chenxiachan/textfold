"""Agents on Windows: found as claude.exe or npm's claude.cmd, a shim run by node itself (cmd.exe would cut a
multi-line system prompt), and a JSON-lines conversation read without select(). Runs anywhere; the last test only on
Windows."""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from textfold import agents, server

SHIM = textwrap.dedent(r"""
    @ECHO off
    GOTO start
    :find_dp0
    SET dp0=%~dp0
    EXIT /b
    :start
    SETLOCAL
    CALL :find_dp0

    IF EXIST "%dp0%\node.exe" (
      SET "_prog=%dp0%\node.exe"
    ) ELSE (
      SET "_prog=node"
      SET PATHEXT=%PATHEXT:;.JS;=;%
    )

    endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\node_modules\@anthropic-ai\claude-code\cli.js" %*
    """)


def touch(p, text="", mode=0o755):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    p.chmod(mode)
    return p


class WindowsAgents(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(self.dir, ignore_errors=True))

    def test_the_shim_is_found_not_the_shell_script_beside_it(self):
        touch(self.dir / "claude", "#!/bin/sh\n")
        touch(self.dir / "claude.cmd", SHIM)
        with mock.patch.object(agents, "EXTS", (".exe", ".cmd", ".bat")), mock.patch.object(agents, "search_dirs", lambda: [str(self.dir)]):
            self.assertEqual(agents.locate("claude"), str(self.dir / "claude.cmd"))

    def test_a_program_comes_before_a_shim(self):
        touch(self.dir / "claude.cmd", SHIM)
        touch(self.dir / "claude.exe")
        with mock.patch.object(agents, "EXTS", (".exe", ".cmd", ".bat")), mock.patch.object(agents, "search_dirs", lambda: [str(self.dir)]):
            self.assertEqual(agents.locate("claude"), str(self.dir / "claude.exe"))

    def test_npms_shim_is_run_by_node(self):
        shim = touch(self.dir / "claude.cmd", SHIM)
        node = touch(self.dir / "node.exe")
        cli = touch(self.dir / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js", "")
        with mock.patch.object(agents, "WIN", True):
            self.assertEqual(agents.argv(str(shim)), [str(node), str(cli)])

    def test_node_from_the_path_when_none_is_beside_the_shim(self):
        shim = touch(self.dir / "pi.cmd", SHIM.replace(r"@anthropic-ai\claude-code\cli.js", r"@earendil\pi\dist\cli.mjs"))
        cli = touch(self.dir / "node_modules" / "@earendil" / "pi" / "dist" / "cli.mjs", "")
        bin_ = self.dir / "elsewhere"
        node = touch(bin_ / ("node.exe" if os.name == "nt" else "node"))
        with mock.patch.object(agents, "WIN", True), mock.patch.object(agents, "search_dirs", lambda: [str(bin_)]):
            got = agents.argv(str(shim))
        self.assertEqual([os.path.normcase(x) for x in got], [os.path.normcase(str(node)), os.path.normcase(str(cli))])   # (Windows: node.EXE)

    def test_a_shim_that_names_no_script_runs_as_it_is(self):
        shim = touch(self.dir / "odd.cmd", "@echo off\r\nodd-real.exe %*\r\n")
        with mock.patch.object(agents, "WIN", True):
            self.assertEqual(agents.argv(str(shim)), [str(shim)])

    def test_elsewhere_the_path_is_the_command(self):
        with mock.patch.object(agents, "WIN", False):
            self.assertEqual(agents.argv("/usr/local/bin/claude"), ["/usr/local/bin/claude"])


class JsonLines(unittest.TestCase):
    def script(self, body):
        d = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        return touch(d / "rpc.py", textwrap.dedent(body))

    def test_the_replies_asked_for_are_collected(self):
        s = self.script("""
            import json, sys
            print("a banner line", flush=True)
            for line in sys.stdin:
                r = json.loads(line)
                print(json.dumps({"id": "other"}), flush=True)
                print(json.dumps({"id": r["id"], "result": r["method"]}), flush=True)
            """)
        got = agents._rpc([sys.executable, str(s)], [{"id": "1", "method": "model/list"}, {"id": "2", "method": "x"}], timeout=20)
        self.assertEqual({k: v["result"] for k, v in got.items()}, {"1": "model/list", "2": "x"})

    def test_a_program_that_ends_early_does_not_hold_the_server(self):
        s = self.script("import sys; sys.stdin.readline(); print('bye')")
        got = agents._rpc([sys.executable, str(s)], [{"id": "1"}], timeout=20)
        self.assertEqual(got, {})


@unittest.skipUnless(os.name == "nt", "Windows only")
class QuietChildren(unittest.TestCase):
    def test_a_child_starts_without_a_window(self):
        init = subprocess.Popen.__init__
        self.addCleanup(setattr, subprocess.Popen, "__init__", init)
        server._quiet_children()
        p = subprocess.Popen([sys.executable, "-c", "print(1)"], stdout=subprocess.PIPE)
        self.assertEqual(p.communicate()[0].strip(), b"1")


if __name__ == "__main__":
    unittest.main()
