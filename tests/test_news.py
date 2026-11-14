"""What's new (news.py): the words of the releases since the one the reader was last told of, the first time TextFold
opens after an update; a quiet release (no words) says only that it is installed; a first install is told nothing. In
a data folder of its own, with releases written for the test; nothing is fetched.

Run from the repo root:
    python3 -m unittest discover tests
"""
import json
import re
import sys
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from textfold import news, server, store   # noqa: E402

RELEASES = [{"version": "0.10.43", "en": [["Edit text", "Put it right."]], "zh": [["修改原文", "改正它。"]]},
            {"version": "0.10.45", "en": [["Later", "More."]], "zh": [["之后", "更多。"]]},
            {"version": "0.10.47", "en": [["Not yet", "Next time."]], "zh": [["还没有", "下次。"]]}]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        f = Path(self.tmp.name) / "whatsnew.json"
        f.write_text(json.dumps(RELEASES), encoding="utf-8")
        for p in (mock.patch.object(store, "DATA", Path(self.tmp.name) / "data"), mock.patch.object(news, "NEWS", f)):
            p.start()
            self.addCleanup(p.stop)

    def library(self, seen=None, papers=True):
        if papers:
            (store.DATA / "papers" / "md-diary-12345678").mkdir(parents=True)
        if seen:
            news.told(seen)


class State(Base):
    def test_a_first_install_is_told_nothing(self):
        got = news.state("0.10.45")
        self.assertEqual((got["updated"], got["news"]), (False, []))
        self.assertEqual(got["last"]["version"], "0.10.45")   # (Settings' What's new)
        self.assertEqual(news._seen(), "0.10.45")   # and not at this release later either

    def test_after_an_update_the_releases_since_newest_first(self):
        self.library(seen="0.10.42")
        got = news.state("0.10.46")
        self.assertTrue(got["updated"])
        self.assertEqual([e["version"] for e in got["news"]], ["0.10.45", "0.10.43"])   # (not one still to come)
        news.told("0.10.46")
        self.assertEqual((news.state("0.10.46")["updated"], news.state("0.10.46")["news"]), (False, []))

    def test_a_quiet_release_says_only_that_it_is_installed(self):
        self.library(seen="0.10.45")
        got = news.state("0.10.46")
        self.assertEqual((got["updated"], got["news"], got["last"]["version"]), (True, [], "0.10.45"))

    def test_a_library_from_before_is_told_of_the_release_it_runs(self):
        self.library()
        got = news.state("0.10.45")
        self.assertEqual((got["updated"], [e["version"] for e in got["news"]]), (True, ["0.10.45"]))
        self.assertIsNone(news._seen())   # (told once the page has shown it)

    def test_an_older_release_than_the_last_told_of_says_nothing(self):
        self.library(seen="0.10.45")
        self.assertEqual((news.state("0.10.43")["updated"], news.state("0.10.43")["news"]), (False, []))


class Package(unittest.TestCase):
    def test_each_release_has_its_words_in_english_and_chinese(self):
        got = json.loads(news.NEWS.read_text(encoding="utf-8"))
        self.assertEqual(len({e["version"] for e in got}), len(got))
        for e in got:
            self.assertRegex(e["version"], r"^\d+\.\d+\.\d+$")
            for lang in ("en", "zh"):   # [title, text], and a link to follow it with: {url (https), label}
                self.assertTrue(e[lang] and all(len(x) in (2, 3) and all(isinstance(w, str) and w.strip() for w in x[:2]) for x in e[lang]), (e["version"], lang))
                for x in e[lang]:
                    if len(x) == 3:
                        self.assertTrue(x[2]["url"].startswith("https://") and x[2]["label"].strip(), (e["version"], lang))
                        self.assertEqual([len(y) for y in e["en"]], [len(y) for y in e["zh"]], e["version"])
            self.assertEqual(len(e["en"]), len(e["zh"]), e["version"])

    def test_it_goes_into_the_package(self):
        toml = (Path(news.__file__).parent.parent / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('"whatsnew.json"', re.search(r"\[tool\.setuptools\.package-data\]\ntextfold = (.*)", toml).group(1))


class Server(Base):
    def test_the_page_asks_and_says_it_told(self):
        self.library(seen="0.10.42")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(object()))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        url = f"http://127.0.0.1:{httpd.server_port}/api/whatsnew"
        with mock.patch.object(news, "__version__", "0.10.43"), mock.patch.object(news.state, "__defaults__", ("0.10.43",)):
            with urllib.request.urlopen(url, timeout=30) as r:
                got = json.loads(r.read())
            self.assertEqual((got["version"], got["updated"], [e["version"] for e in got["news"]]), ("0.10.43", True, ["0.10.43"]))
            req = urllib.request.Request(url, data=json.dumps({"seen": "0.10.43"}).encode(), headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                self.assertEqual(json.loads(r.read()), {"ok": True})
            with urllib.request.urlopen(url, timeout=30) as r:
                self.assertFalse(json.loads(r.read())["updated"])


if __name__ == "__main__":
    unittest.main()
