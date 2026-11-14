"""A page sent from the reader's own browser (extension/, capture.py): native messaging's frames, a snapshot checked and
kept as a web page of the library (its HTML beside it, never over a page already there), the host's loop, and its
manifest, which lets only the extensions named reach it.

Run from the repo root:
    python3 -m unittest discover tests
"""
import base64
import io
import json
import os
import struct
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from textfold import capture, semantic, store, webpage

WORDS = ("The river office kept the gauges for a century, and its records show how the spring floods rose and fell "
         "with the snow on the hills, year after year, in the same slow rhythm the town still lives by. ")
PAGE = ("<!doctype html><html><head><title>Rivers</title></head><body><article><h1>Rivers</h1>"
        + "".join(f"<p>Paragraph {k}. {WORDS}</p>" for k in range(1, 5))
        + '<p>Read hourly<span class="mark">*</span><span data-tf-css-hidden="1" style="display:none">Since 2019.</span>.</p>'
        + "</article></body></html>")
SNAP = {"format": "textfold/snapshot", "version": 1, "url": "https://news.example.org/rivers#top", "title": "Rivers | Example",
        "html": PAGE, "taken": "2026-10-26T20:00:00Z", "how": "extension"}


def framed(obj):
    body = json.dumps(obj).encode()
    return struct.pack("=I", len(body)) + body


class Frames(unittest.TestCase):
    def test_a_message_there_and_back(self):
        out = io.BytesIO()
        capture.write_message(out, {"ok": True, "said": "日本語"})
        self.assertEqual(capture.read_message(io.BytesIO(out.getvalue())), {"ok": True, "said": "日本語"})
        self.assertIsNone(capture.read_message(io.BytesIO(b"")))   # (the browser closed the stream)

    def test_too_large_or_cut_off(self):
        with self.assertRaisesRegex(ValueError, "too-large"):
            capture.read_message(io.BytesIO(struct.pack("=I", capture.MAX + 1)))
        with self.assertRaisesRegex(ValueError, "cut-off"):
            capture.read_message(io.BytesIO(framed({"a": 1})[:-2]))


class Receive(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        for p in (mock.patch.object(store, "DATA", self.data), mock.patch("textfold.local.prefetch", lambda srcs: {})):
            p.start()
            self.addCleanup(p.stop)

    def test_what_is_not_a_snapshot_of_a_web_page(self):
        for bad, said in (({"format": "other"}, "not-a-snapshot"), (dict(SNAP, url="file:///Users/x/a.html"), "not-a-web-page"),
                          (dict(SNAP, url="chrome://settings"), "not-a-web-page"), (dict(SNAP, html="  "), "no-page")):
            with self.assertRaisesRegex(ValueError, said):
                capture.receive(bad)

    def test_kept_as_a_web_page_with_its_snapshot(self):
        r = capture.receive(SNAP)
        pid = webpage.pid_of("https://news.example.org/rivers")   # (its fragment aside)
        self.assertEqual((r["ok"], r["paper"], r["new"], r["open"]), (True, pid, True, None))
        d = store.pdir(pid)
        self.assertEqual((d / "snapshot.html").read_text(encoding="utf-8"), PAGE)
        self.assertTrue((d / "source.md").exists())
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual((meta["source"]["via"], meta["snapshot_kind"], meta["snapshot_taken"]), ("browser", "browser", SNAP["taken"]))
        html = (d / "source.html").read_text(encoding="utf-8")
        self.assertIn("Paragraph 4.", html)
        self.assertIn('<sup class="ltx_note_mark">*</sup>', html)   # (hidden on its mark: a note)

    def test_a_page_already_there_is_not_written_over(self):
        pid = capture.receive(SNAP)["paper"]
        before = (store.pdir(pid) / "source.html").read_text(encoding="utf-8")
        r = capture.receive(dict(SNAP, html=PAGE.replace("Paragraph 1.", "Changed.")))
        self.assertEqual((r["paper"], r["new"]), (pid, False))
        self.assertEqual((store.pdir(pid) / "source.html").read_text(encoding="utf-8"), before)

    def test_a_page_that_names_itself_nowhere_takes_the_tab_s_title(self):
        page = PAGE.replace("<title>Rivers</title>", "").replace("<h1>Rivers</h1>", "")
        pid = capture.receive(dict(SNAP, url="https://bare.example.org/x", html=page, title="What the tab said"))["paper"]
        self.assertEqual(json.loads((store.pdir(pid) / "meta.json").read_text(encoding="utf-8"))["title"], "What the tab said")

    def test_the_reader_opens_where_textfold_runs(self):
        (self.data / "server.json").write_text(json.dumps({"format": "textfold/server", "url": "http://127.0.0.1:4100", "pid": os.getpid()}))
        r = capture.receive(SNAP)
        self.assertEqual(r["open"], f"http://127.0.0.1:4100/p/{r['paper']}")
        (self.data / "server.json").write_text(json.dumps({"url": "http://127.0.0.1:4100", "pid": 2 ** 22 + 12345}))   # (gone)
        self.assertIsNone(capture.receive(SNAP)["open"])

    def test_the_host_answers_each_message_until_the_stream_ends(self):
        out = io.BytesIO()
        code = capture.host(io.BytesIO(framed(SNAP) + framed({"format": "other"})), out)
        stream = io.BytesIO(out.getvalue())
        first, second = capture.read_message(stream), capture.read_message(stream)
        self.assertEqual((code, first["ok"], second), (0, True, {"ok": False, "error": "not-a-snapshot"}))


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")
# what Defuddle gives of a publisher's article page (extension/reader.js): the publisher's name and the title above
# the article, the site's buttons under it, a citation, a figure, a formula with its LaTeX, a footnote
VIEW = ('<h2>Published by: Example Press</h2><h2>Rivers</h2><div><a href="/pdf">View PDF</a> '
        '<p><a href="https://doi.org/10.1/x">10.1/x</a></p></div><h2>Abstract</h2>'
        f'<p>{WORDS}</p><h2>1. Introduction</h2><p>Paragraph 1. {WORDS} The gauges say so <a href="#b0010">[2]</a>.'
        '<sup id="fnref:1"><a href="#fn:1">1</a></sup></p>'
        '<figure><img src="fig1.png" alt=""><figcaption>Fig. 1. The gauges at the river office.</figcaption></figure>'
        f'<p>Paragraph 2. {WORDS}</p><math display="block" data-latex="x = 1"><mi>x</mi><mo>=</mo><mn>1</mn></math>'
        '<div id="footnotes"><ol><li id="fn:1"><p>Since 2019. <a href="#fnref:1" class="footnote-backref">↩</a></p></li></ol></div>')
READ = {"defuddle": {"html": VIEW, "title": "Rivers", "words": 160}, "readability": {"html": f"<p>{WORDS}</p>" * 4, "words": 140}}
# the publisher's page VIEW is Defuddle's reading of, as the browser showed it: the site's header and buttons around it
PRESS = ('<!doctype html><html><head><title>Rivers | Example Press</title></head><body><header><nav><a href="/">Journals</a> '
         '<a href="/books">Books</a></nav></header><main><article>' + VIEW.replace('<div id="footnotes">', '<aside id="footnotes">')
         .replace('</li></ol></div>', '</li></ol></aside>') + '</article></main><footer>About Example Press</footer></body></html>')


class ReadingView(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        for p in (mock.patch.object(store, "DATA", self.data),
                  mock.patch("textfold.local.prefetch", lambda srcs: {s: (PNG, "fig1.png") for s in srcs})):
            p.start()
            self.addCleanup(p.stop)

    @unittest.skipUnless(os.name == 'nt', 'Windows process liveness must not send a signal')
    def test_server_liveness_never_signals_the_running_windows_process(self):
        url = 'http://127.0.0.1:12345'
        (self.data / 'server.json').write_text(json.dumps({'url': url, 'pid': os.getpid()}), encoding='utf-8')
        with mock.patch.object(os, 'kill', side_effect=AssertionError('signal sent by a liveness probe')):
            self.assertEqual(capture._server_url(), url)

    def test_which_reading(self):
        self.assertEqual(webpage.view_of(READ)[0], "defuddle")
        many = dict(READ, readability={"html": "<p>x</p>", "words": 240})   # (Defuddle took too much away)
        self.assertEqual(webpage.view_of(many)[0], "readability")
        self.assertEqual(webpage.view_of({"defuddle": {"html": "<p>x</p>", "words": 30}}), (None, None))
        self.assertEqual(webpage.view_of({"errors": ["defuddle: no"]}), (None, None))
        self.assertEqual(webpage.view_of(None), (None, None))

    def test_the_article_from_its_title_on(self):
        html, info = webpage.read_view(READ["defuddle"], PAGE, "https://press.example.org/rivers")
        self.assertEqual(info["title"], "Rivers")
        for gone in ("Published by", "View PDF", "10.1/x", "<h2>Rivers</h2>"):
            self.assertNotIn(gone, html)
        self.assertIn('src="https://press.example.org/fig1.png"', html)
        doc = semantic.normalize(html, resolve_img=lambda src: "img/01-fig1.png")
        self.assertIn('alttext="x = 1"', doc)
        self.assertIn("Abstract", doc)
        self.assertIn("[2]", doc)
        self.assertIn("ltx_role_footnote", doc)   # (Defuddle's footnote, in its sentence)
        self.assertIn("The gauges at the river office.", doc)

    def test_a_reference_list_taken_for_footnotes_stays_a_list(self):
        view = {"html": f'<p>{WORDS} {WORDS} As the gauges show <a href="#b0010">[2]</a>.</p><h2>References</h2>'
                        '<div id="footnotes"><ol><li id="fn:1"><p>Gauge, A. (2020). Spring floods. '
                        '<a href="#fnref:1" class="footnote-backref">↩</a></p></li></ol></div>', "words": 90}
        doc = semantic.normalize(webpage.read_view(view, PAGE, "https://press.example.org/rivers")[0])
        self.assertIn("Spring floods.", doc)
        self.assertNotIn("↩", doc)
        self.assertNotIn("ltx_role_footnote", doc)

    def test_a_formula_that_is_nothing_but_its_latex(self):
        # (a formula MathJax drew, its source asked of MathJax: extension/background.js, reader.js)
        doc = semantic.normalize('<p>With <math display="inline" alttext="y_i"><semantics><annotation encoding="application/x-tex">'
                                 'y_i</annotation></semantics></math> observations.</p>')
        self.assertIn('class="pf-tex"', doc)
        self.assertRegex(doc, r"<msub><mi>y</mi><mi>i</mi></msub>")

    def test_formulas_readability_leaves_as_drawn(self):
        katex = ('<p>So <span class="katex"><span class="katex-mathml"><math><semantics><mi>y</mi><annotation '
                 'encoding="application/x-tex">y</annotation></semantics></math></span><span class="katex-html">y</span></span> holds. '
                 f'{WORDS}{WORDS}</p><p>And <mjx-container display="true"><mjx-math>z</mjx-math><mjx-assistive-mml><math>'
                 '<mi>z</mi></math></mjx-assistive-mml></mjx-container> too.</p>')
        html, _ = webpage.read_view({"html": katex, "words": 90}, PAGE, "https://press.example.org/rivers")
        self.assertNotIn("katex-html", html)
        self.assertNotIn("mjx-math", html)
        self.assertIn('<math display="block"><mi>z</mi></math>', html)

    def test_kept_from_the_reading_view(self):
        r = capture.receive(dict(SNAP, html=PRESS, read=READ))
        d = store.pdir(r["paper"])
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual((meta["reader"], meta["title"]), ("defuddle", "Rivers"))
        self.assertEqual((d / "article.html").read_text(encoding="utf-8"), VIEW)
        self.assertFalse((d / "source.md").exists())
        html = (d / "source.html").read_text(encoding="utf-8")
        self.assertIn("ltx_role_footnote", html)
        self.assertIn('src="img/01-fig1.png"', html)
        self.assertIn("ltx_title", html)

    def test_the_reading_that_differs_least_from_the_page_is_kept(self):
        """A reading view that left out part of the article (Defuddle, a Wikipedia article's sections): the other, or
        TextFold's own of the snapshot, is kept, whatever their words (webpage.view_of would take Defuddle's)."""
        half = {"html": f"<h1>Rivers</h1><p>Paragraph 1. {WORDS}</p><p>Paragraph 2. {WORDS}</p>", "title": "Rivers", "words": 90}
        whole = {"html": "".join(f"<p>Paragraph {k}. {WORDS}</p>" for k in range(1, 5)) + "<p>Read hourly.</p>", "words": 120}
        self.assertEqual(webpage.view_of({"defuddle": half, "readability": whole})[0], "defuddle")
        r = capture.receive(dict(SNAP, read={"defuddle": half, "readability": whole}))
        meta = json.loads((store.pdir(r["paper"]) / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["reader"], "readability")
        self.assertIn("Paragraph 4.", (store.pdir(r["paper"]) / "source.html").read_text(encoding="utf-8"))

    def test_neither_reading_has_the_words(self):
        r = capture.receive(dict(SNAP, read={"defuddle": {"html": "<p>Subscribe.</p>", "words": 1}}))
        meta = json.loads((store.pdir(r["paper"]) / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["reader"], "textfold")
        self.assertTrue((store.pdir(r["paper"]) / "source.md").exists())

    def test_its_levels_asked_of_the_textfold_that_runs(self):
        asked = []

        class Server(BaseHTTPRequestHandler):
            def do_POST(self):
                asked.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
                body = b'{"id": "job1"}'
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Server)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.shutdown)
        (self.data / "server.json").write_text(json.dumps({"url": f"http://127.0.0.1:{srv.server_address[1]}", "pid": os.getpid()}))
        with mock.patch("textfold.models.load_settings", lambda: {"lang": "zh"}):
            r = capture.receive(dict(SNAP, read=READ))
            self.assertEqual(r["job"], "job1")
            self.assertEqual(asked, [("/api/generate", {"paper": r["paper"], "lang": "zh"})])
            self.assertIsNone(capture.receive(dict(SNAP, read=READ))["job"])   # (a page there already: not again)


class Install(unittest.TestCase):
    EXT = "hgmpkjklkepgaeppbpaphfjpkicgicbb"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        for p in (mock.patch.object(store, "DATA", self.tmp / "data"), mock.patch.object(capture, "STORE", ())):   # (the store's, where a test names it)
            p.start()
            self.addCleanup(p.stop)

    def test_only_the_extensions_named(self):
        tmp, ext = self.tmp, self.EXT
        out = capture.install([ext], dirs={"chrome": tmp / "c", "edge": tmp / "e"}, home=tmp / "home")
        self.assertEqual(len(out), 2)
        m = json.loads(out[0].read_text())
        self.assertEqual((m["name"], m["type"], m["allowed_origins"]), (capture.HOST, "stdio", [f"chrome-extension://{ext}/"]))
        launcher = Path(m["path"]).read_text()
        self.assertIn("-m textfold capture-host", launcher)
        self.assertTrue(os.access(m["path"], os.X_OK) or os.name == "nt")
        self.assertEqual(len(capture.install([ext], ["edge"], dirs={"chrome": tmp / "c", "edge": tmp / "e"}, home=tmp / "home")), 1)
        for bad in ([], ["not-an-id"], ["HGMPKJKLKEPGAEPPBPAPHFJPKICGICBB"]):
            with self.assertRaisesRegex(ValueError, "32 letters"):
                capture.install(bad, dirs={"chrome": tmp / "none"})

    def test_the_store_s_extension_and_those_let_in_before(self):
        tmp, mine, store_id = self.tmp, self.EXT, "a" * 32
        dirs = {"chrome": tmp / "c"}
        capture.install([mine], dirs=dirs, home=tmp / "home")
        with mock.patch.object(capture, "STORE", (store_id,)):
            out = capture.install([], dirs=dirs, home=tmp / "home")   # (as the app does: none named)
        self.assertEqual(json.loads(out[0].read_text())["allowed_origins"], [f"chrome-extension://{e}/" for e in (store_id, mine)])

    def test_the_app_s_host_runs_as_the_app_does(self):
        out = capture.install([self.EXT], dirs={"chrome": self.tmp / "c"}, home=self.tmp / "home", app=True)
        launcher = Path(json.loads(out[0].read_text())["path"]).read_text()
        for said in ("TEXTFOLD_RUNTIME_ID=desktop", "PYTHONNOUSERSITE=1", "PYTHONUTF8=1", " -s -m textfold capture-host"):
            self.assertIn(said, launcher)
        self.assertIn(str(store.ROOT), launcher)   # (the app's code, where Python would not look)
        self.assertIn(str(self.tmp / "data"), launcher)

    def test_what_the_browser_starts_answers(self):
        """The app's launcher run as the browser runs it (Windows: by cmd.exe, as Chrome starts a batch file), a hello
        in and TextFold's answer out: the app's build runs this with the Python it ships (desktop/scripts/smoke.py)."""
        import subprocess
        from textfold import __version__
        reg = rf"Software\TextFoldTest{os.getpid()}"
        if os.name == "nt":
            self.addCleanup(self._unregister, reg)
        else:
            mock.patch.object(capture, "_name_in_registry", lambda key, value: None).start()
            self.addCleanup(mock.patch.stopall)
        out = capture.install([self.EXT], home=self.tmp / "home", keys={"chrome": reg + r"\Chrome"}, app=True) if os.name == "nt" else \
            capture.install([self.EXT], dirs={"chrome": self.tmp / "c"}, home=self.tmp / "home", app=True)
        path = json.loads(Path(out[0]).read_text())["path"]
        run = ["cmd.exe", "/d", "/c", path] if os.name == "nt" else [path]
        r = subprocess.run(run + [f"chrome-extension://{self.EXT}/"], input=framed({"format": "textfold/hello", "version": 1}),
                           capture_output=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stderr.decode(errors="replace")[-2000:])
        self.assertEqual(capture.read_message(io.BytesIO(r.stdout)),
                         {"ok": True, "textfold": __version__, "protocol": capture.PROTOCOL, "running": False})
        if os.name == "nt":
            import winreg
            self.assertEqual(winreg.QueryValue(winreg.HKEY_CURRENT_USER, reg + rf"\Chrome\{capture.HOST}"), str(out[0]))

    @staticmethod
    def _unregister(reg):
        import winreg
        for key in (rf"{reg}\Chrome\{capture.HOST}", rf"{reg}\Chrome", reg):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)
            except OSError:
                pass

    def test_on_windows_one_manifest_named_in_each_browser_s_key(self):
        named = []
        with mock.patch.object(capture, "_name_in_registry", lambda key, value: named.append((key, value))):
            out = capture.install([self.EXT], ["chrome", "edge"], home=self.tmp / "home", keys=capture.KEYS)
        f = self.tmp / "home" / f"{capture.HOST}.json"
        self.assertEqual(out[0], f)
        self.assertEqual(named, [(rf"Software\Google\Chrome\NativeMessagingHosts\{capture.HOST}", str(f)),
                                 (rf"Software\Microsoft\Edge\NativeMessagingHosts\{capture.HOST}", str(f))])

    def test_a_batch_file_names_the_reader_s_folders_by_their_variables(self):
        with mock.patch.dict(os.environ, {"LOCALAPPDATA": "C:\\Users\\张三\\AppData\\Local", "USERPROFILE": "C:\\Users\\张三"}):
            self.assertEqual(capture._batch("C:\\Users\\张三\\AppData\\Local\\Programs\\TextFold\\python.exe"),
                             "%LOCALAPPDATA%\\Programs\\TextFold\\python.exe")
            self.assertEqual(capture._batch("c:\\users\\张三\\.textfold"), "%USERPROFILE%\\.textfold")
            self.assertEqual(capture._batch("D:\\100%\\TextFold"), "D:\\100%%\\TextFold")


class Versions(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        p = mock.patch.object(store, "DATA", self.data)
        p.start()
        self.addCleanup(p.stop)

    def test_a_hello_says_which_textfold_answers(self):
        from textfold import __version__
        self.assertEqual(capture.receive({"format": "textfold/hello", "version": 1}),
                         {"ok": True, "textfold": __version__, "protocol": capture.PROTOCOL, "running": False})

    def test_a_newer_extension_s_message_is_refused(self):
        with self.assertRaisesRegex(ValueError, "update-textfold"):
            capture.receive(dict(SNAP, version=capture.PROTOCOL + 1))
        self.assertFalse((self.data / "papers").exists())


class StoreId(unittest.TestCase):
    def test_the_manifest_s_key_gives_the_store_s_id(self):
        """A copy loaded unpacked is the extension every install lets in: Chrome's id is the first 128 bits of the
        key's SHA-256, its hex digits written a to p."""
        import base64
        import hashlib
        m = json.loads((store.ROOT / "extension" / "manifest.json").read_text(encoding="utf-8"))
        digest = hashlib.sha256(base64.b64decode(m["key"])).hexdigest()[:32]
        self.assertEqual("".join(chr(ord("a") + int(c, 16)) for c in digest), capture.STORE[0])


class Pack(unittest.TestCase):
    def test_the_store_s_zip(self):
        import importlib.util
        import zipfile
        spec = importlib.util.spec_from_file_location("pack_extension", store.ROOT / "tools" / "pack_extension.py")
        pack = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(pack)
        with tempfile.TemporaryDirectory() as tmp:
            out, n = pack.pack(tmp)
            with zipfile.ZipFile(out) as z:
                names = z.namelist()
                m = json.loads(z.read("manifest.json"))
            self.assertEqual(out.name, f"textfold-extension-{m['version']}.zip")
            self.assertNotIn("key", m)
            self.assertNotIn("README.md", names)
            self.assertLessEqual(pack.named(m), set(names))
            self.assertTrue(any(x.startswith("_locales/zh_CN/") for x in names))
            self.assertEqual(out.read_bytes(), pack.pack(Path(tmp) / "again")[0].read_bytes())   # (the same files, the same zip)


if __name__ == "__main__":
    unittest.main()
