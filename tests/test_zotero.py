"""textfold/zotero.py, and the server's /api/zotero routes, against a fake Zotero local API.

Nothing here reaches the reader's own Zotero, settings or library: the settings and the data folder are temporary, the
fake answers on a free port of this computer, and the server run here looks for no model (TEXTFOLD_NO_MODELS).

Run from the repo root:
    python3 -m unittest discover tests
"""
import json
import os
import shutil
import site
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from textfold import models, store, zotero

ROOT = Path(__file__).resolve().parents[1]
PDF = b"%PDF-1.4\n1 0 obj << /Type /Catalog >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n"
SNAPSHOT = ("<!doctype html><html><head><title>Snapshot 2024</title></head><body><article><h1>Snapshot 2024</h1>"
            "<p>The first paragraph of a page Zotero saved, long enough to be read as the article it is.</p>"
            "<h2>Its method</h2><p>A second paragraph, under a heading of its own, so the page has a shape.</p></article></body></html>")


def item(key, title, **data):
    links, kind, kids = data.pop("links", {}), data.pop("itemType", "journalArticle"), data.pop("children", None)
    return {"key": key, "data": {"key": key, "itemType": kind, "title": title,
                                 "creators": [{"creatorType": "author", "lastName": "Lovelace", "firstName": "Ada"}], **data},
            "meta": {"parsedDate": "2024-05-01", **({"numChildren": kids} if kids else {})}, "links": links}


def key_of(n):
    """The n-th key of a made-up library, in Zotero's alphabet."""
    alphabet, out = "23456789ABCDEFGHIJKLMNPQRSTUVWXYZ", ""
    for _ in range(5):
        out, n = alphabet[n % 33] + out, n // 33
    return "GRP" + out


def attachment(key, ctype, mode, **data):
    return {"key": key, "data": {"key": key, "itemType": "attachment", "contentType": ctype, "linkMode": mode, **data}}


class Fake:
    """A Zotero library: its items, their attachments, the files Zotero says are theirs."""

    def __init__(self, files):
        self.off = False
        self.items = {
            "ARXVKEY2": item("ARXVKEY2", "Chain of thought", url="https://arxiv.org/abs/2201.11903", extra="Citation Key: wei2022"),
            "HTMLKEY2": item("HTMLKEY2", "A saved page", itemType="webpage",
                             links={"attachment": {"attachmentType": "text/html"}}),
            "PDFSKEY2": item("PDFSKEY2", "A paper with its PDF", DOI="10.1234/abcd",
                             links={"attachment": {"attachmentType": "application/pdf"}}),
            "PLNPKEY2": item("PLNPKEY2", "A PDF and nothing else", children=1),   # (no best attachment named: its children say)
            "FAKEPDF2": item("FAKEPDF2", "Not a PDF inside"),
            "DIGIKEY2": item("DIGIKEY2", "Only a DOI", DOI="10.5555/only"),
            "NTHGKEY2": item("NTHGKEY2", "Nothing to read"),
            "TRAVKEY2": item("TRAVKEY2", "A file name that climbs out"),
        }
        self.children = {
            "HTMLKEY2": [attachment("ATTHTML2", "text/html", "imported_url", filename="snap.html")],
            "PDFSKEY2": [attachment("ATTPDFS2", "application/pdf", "imported_file", filename="paper.pdf")],
            "PLNPKEY2": [attachment("ATTPLNP2", "application/pdf", "imported_file", filename="plain.pdf")],
            "FAKEPDF2": [attachment("ATTFAKE2", "application/pdf", "imported_file", filename="fake.pdf")],
            "TRAVKEY2": [attachment("ATTTRAV2", "application/pdf", "imported_file", filename="../../secret.pdf")],
        }
        self.group = {key_of(n): item(key_of(n), f"Group item {n}") for n in range(230)}
        self.group["LINKKEY2"] = item("LINKKEY2", "A linked file in a group")
        self.group_children = {"LINKKEY2": [attachment("ATTLINK2", "application/pdf", "linked_file", path=str(files["outside"]))]}
        self.collections = [{"key": "SUBCKEY2", "data": {"name": "methods", "parentCollection": "TPCLKEY2"}, "meta": {"numItems": 1}},
                            {"key": "TPCLKEY2", "data": {"name": "Reading", "parentCollection": False}, "meta": {"numItems": 2}}]
        self.in_collection = {"TPCLKEY2": ["ARXVKEY2", "HTMLKEY2"], "SUBCKEY2": ["PDFSKEY2"]}
        self.files = {"ATTHTML2": files["snapshot"], "ATTFAKE2": files["fake"]}   # what file/view/url answers (ATTPDFS2: not)

    def answer(self, path, params):
        if path == "users/0/groups":
            return [{"id": 42, "data": {"name": "Lab"}, "meta": {"numItems": len(self.group)}}]
        items, children = (self.items, self.children) if path.startswith("users/0") else (self.group, self.group_children)
        if path.endswith("/collections") and path.count("/") == 2:
            return self.collections if path.startswith("users/0") else []
        parts = path.split("/")
        if len(parts) == 6 and parts[2] == "collections" and parts[4:] == ["items", "top"]:
            return [items[k] for k in self.in_collection.get(parts[3], [])]
        if parts[2:] == ["items", "top"]:
            q = params.get("q", "").lower()
            return [i for i in items.values() if q in i["data"]["title"].lower()]
        if len(parts) == 5 and parts[2] == "items" and parts[4] == "children":
            return children.get(parts[3], [])
        if len(parts) == 4 and parts[2] == "items":
            return items.get(parts[3])
        return None


def serve_fake(fake):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            params = {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}
            path = u.path[len("/api/"):]
            if fake.off:
                return self.reply(403, b"Local API is not enabled", "text/plain")
            if path.endswith("/file/view/url"):
                f = fake.files.get(path.split("/")[3])
                return self.reply(200, f.as_uri().encode(), "text/plain") if f else self.reply(404, b"Not found", "text/plain")
            body = fake.answer(path, params)
            if body is None:
                return self.reply(404, b"Not found", "text/plain")
            total = len(body) if isinstance(body, list) else 1
            if isinstance(body, list):
                start, limit = int(params.get("start", 0)), int(params.get("limit", 25))
                body = body[start:start + limit]
            self.reply(200, json.dumps(body).encode(), "application/json", total)

        def reply(self, code, data, ctype, total=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            if total is not None:
                self.send_header("Total-Results", str(total))
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Setup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="tf-zotero-"))
        zdata = cls.tmp / "zotero-data"
        (zdata / "storage" / "ATTPDFS2").mkdir(parents=True)
        (zdata / "storage" / "ATTPDFS2" / "paper.pdf").write_bytes(PDF)
        (zdata / "storage" / "ATTPLNP2").mkdir(parents=True)
        (zdata / "storage" / "ATTPLNP2" / "plain.pdf").write_bytes(PDF + b"%plain\n")
        (cls.tmp / "secret.pdf").write_bytes(PDF)   # what "../../secret.pdf" would reach from storage/<key>/
        files = {"snapshot": cls.tmp / "snap.html", "fake": cls.tmp / "fake.pdf", "outside": cls.tmp / "outside.pdf"}
        files["snapshot"].write_text(SNAPSHOT, encoding="utf-8")
        files["fake"].write_bytes(b"#!/bin/sh\necho not a pdf\n")
        files["outside"].write_bytes(PDF)
        cls.fake = Fake(files)
        cls.srv = serve_fake(cls.fake)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        cls.home = cls.tmp / "home"
        (cls.home / ".textfold").mkdir(parents=True)
        cls.settings = {"use_thoughtdag_env": False, "zotero": {"base": cls.base, "data_dir": str(zdata)}}
        (cls.home / ".textfold" / "settings.json").write_text(json.dumps(cls.settings), encoding="utf-8")
        cls.data = cls.tmp / "data"
        (cls.data / "papers").mkdir(parents=True)
        # this process's settings and library, the temporary ones
        cls.saved = (models.SETTINGS, models.OLD_HOMES, store.DATA)
        models.SETTINGS, models.OLD_HOMES, store.DATA = cls.home / ".textfold" / "settings.json", (), cls.data

    @classmethod
    def tearDownClass(cls):
        models.SETTINGS, models.OLD_HOMES, store.DATA = cls.saved
        cls.srv.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)


class LibraryTest(Setup):
    def test_status_lists_the_libraries(self):
        st = zotero.status()
        self.assertTrue(st["ok"])
        self.assertEqual(st["library"], "users/0")
        self.assertEqual([(x["id"], x["name"]) for x in st["libraries"]], [("users/0", ""), ("groups/42", "Lab")])

    def test_the_chosen_library_is_kept_and_nothing_else_changes(self):
        zotero.choose("groups/42")
        try:
            self.assertEqual(zotero.settings()["library"], "groups/42")
            self.assertEqual(zotero.settings()["base"], self.base)   # the address stays the file's
            for bad in ("../x", "groups/abc", "users/1", ""):
                with self.assertRaises(ValueError):
                    zotero.choose(bad)
        finally:
            zotero.choose("users/0")

    def test_collections_as_a_tree(self):
        cols = zotero.collections("users/0")
        self.assertEqual([(c["name"], c["parent"]) for c in cols], [("methods", "TPCLKEY2"), ("Reading", "")])

    def test_items_of_a_collection_and_a_search(self):
        got = {i["key"]: i for i in zotero.items("users/0", "TPCLKEY2")}
        self.assertEqual(set(got), {"ARXVKEY2", "HTMLKEY2"})
        self.assertEqual((got["ARXVKEY2"]["arxiv"], got["ARXVKEY2"]["citekey"], got["ARXVKEY2"]["year"]), ("2201.11903", "wei2022", "2024"))
        self.assertEqual(got["HTMLKEY2"]["file"], "HTML")
        self.assertEqual([i["key"] for i in zotero.items("users/0", q="only a doi")], ["DIGIKEY2"])
        with self.assertRaises(ValueError):
            zotero.items("users/0", "not-a-key")

    def test_a_long_library_is_read_page_by_page(self):
        self.assertEqual(len(zotero.items("groups/42")), 231)

    def test_zotero_off_and_closed_say_so(self):
        self.fake.off = True
        try:
            self.assertEqual(zotero.status()["error"], "api-off")
            with self.assertRaises(zotero.Off):
                zotero.read("users/0", "HTMLKEY2")
        finally:
            self.fake.off = False
        s = json.loads(models.SETTINGS.read_text(encoding="utf-8"))
        s["zotero"]["base"] = f"http://127.0.0.1:{free_port()}"
        models.SETTINGS.write_text(json.dumps(s), encoding="utf-8")
        try:
            self.assertEqual(zotero.status()["error"], "not-running")
        finally:
            models.SETTINGS.write_text(json.dumps(self.settings), encoding="utf-8")


class ReadTest(Setup):
    def test_an_arxiv_item_is_read_from_arxiv(self):
        self.assertEqual(zotero.read("users/0", "ARXVKEY2"), {"ref": "2201.11903", "title": "Chain of thought"})

    def test_a_saved_page_is_its_file(self):
        got = zotero.read("users/0", "HTMLKEY2")
        self.assertEqual((got["name"], got["data"].decode(), got["refs"]), ("snap.html", SNAPSHOT, []))

    def test_a_pdf_found_in_storage_carries_its_doi(self):
        got = zotero.read("users/0", "PDFSKEY2")   # (Zotero does not say where: found in its data folder)
        self.assertEqual((got["name"], got["data"], got["refs"]), ("paper.pdf", PDF, ["10.1234/abcd"]))

    def test_a_doi_alone_is_read_online(self):
        self.assertEqual(zotero.read("users/0", "DIGIKEY2")["ref"], "10.5555/only")

    def test_what_is_not_a_readable_file_is_not_read(self):
        for key in ("NTHGKEY2", "FAKEPDF2", "TRAVKEY2"):   # nothing; a "PDF" that is not one; a name out of storage/
            with self.assertRaises(ValueError, msg=key) as e:
                zotero.read("users/0", key)
            self.assertEqual(str(e.exception), "no-file")
        with self.assertRaises(ValueError):   # a linked file is the reader's own library's only
            zotero.read("groups/42", "LINKKEY2")

    def test_bad_references_are_refused(self):
        for lib, key in (("users/0", "../../x"), ("users/0", "lowercase"), ("../x", "HTMLKEY2")):
            with self.assertRaises(ValueError):
                zotero.read(lib, key)

    def test_a_document_from_an_items_file_takes_the_items_record(self):
        d = self.data / "papers" / "pdf-fedcba9876543210"
        d.mkdir()
        (d / "meta.json").write_text(json.dumps({"id": d.name, "title": "WHAT THE PDF SAYS", "authors": [], "date": "",
                                                 "doi": "", "abs_url": "", "source": {"kind": "pdf", "name": "paper.pdf"}, "label": "PDF · paper.pdf"}), encoding="utf-8")
        self.assertFalse(zotero.apply(d.name))   # not read from Zotero: as it was
        got = zotero.read("users/0", "PDFSKEY2")
        zotero.link("users/0", "PDFSKEY2", d.name, got["title"], {**got["record"], "citekey": "lovelace2024"})
        self.assertTrue(zotero.apply(d.name))
        m = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        self.assertEqual((m["title"], m["authors"], m["date"], m["doi"], m["abs_url"], m["label"]),
                         ("A paper with its PDF", ["Lovelace, Ada"], "2024-05-01", "10.1234/abcd", "https://doi.org/10.1234/abcd", "Zotero · lovelace2024"))
        self.assertEqual(m["zotero"], {"library": "users/0", "item": "PDFSKEY2", "citekey": "lovelace2024"})
        self.assertFalse(zotero.apply("2201.11903"))   # a paper read online keeps its own record

    def test_an_item_read_is_marked_while_its_document_is_here(self):
        (self.data / "papers" / "doc-0123456789abcdef").mkdir()
        (self.data / "papers" / "doc-0123456789abcdef" / "meta.json").write_text("{}", encoding="utf-8")
        zotero.link("users/0", "HTMLKEY2", "doc-0123456789abcdef", "A saved page")
        zotero.link("users/0", "ARXVKEY2", "2201.11903", "Chain of thought")   # not here (never generated)
        got = {i["key"]: i["paper"] for i in zotero.items("users/0", "TPCLKEY2")}
        self.assertEqual(got, {"HTMLKEY2": "doc-0123456789abcdef", "ARXVKEY2": ""})


class ServerTest(Setup):
    """The routes, on a server of its own with this test's settings and data folder (and no engine: HOME is theirs)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.port = free_port()
        # (the packages a user installed stay found: where they are is HOME's, unless PYTHONUSERBASE says; on Windows the
        # home is USERPROFILE's, and the engine is under LOCALAPPDATA)
        env = {**os.environ, "HOME": str(cls.home), "USERPROFILE": str(cls.home), "LOCALAPPDATA": str(cls.home / "AppData" / "Local"),
               "TEXTFOLD_DATA": str(cls.data), "TEXTFOLD_NO_MODELS": "1", "PYTHONUSERBASE": site.getuserbase()}
        cls.proc = subprocess.Popen([sys.executable, "-m", "textfold", "serve", "--port", str(cls.port)], cwd=ROOT, env=env,
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{cls.port}/api/papers", timeout=1)
                break
            except OSError:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(timeout=10)
        super().tearDownClass()

    def call(self, path, body=None, origin=None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json", **({"Origin": origin} if origin else {})})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_listing(self):
        code, st = self.call("/api/zotero")
        self.assertEqual((code, st["ok"], st["library"]), (200, True, "users/0"))
        code, items = self.call("/api/zotero/items?lib=users/0&collection=TPCLKEY2")
        self.assertEqual((code, sorted(i["key"] for i in items)), (200, ["ARXVKEY2", "HTMLKEY2"]))
        self.assertEqual(self.call("/api/zotero/items?lib=../etc")[0], 400)

    def test_a_saved_page_opens_at_once_and_is_marked(self):
        code, d = self.call("/api/zotero/read", {"item": "users/0/HTMLKEY2", "lang": "en"})
        self.assertEqual(code, 200, d)
        pid = d["paper"]
        self.assertTrue(pid.startswith("doc-"), d)
        self.assertTrue((self.data / "papers" / pid / "source.html").exists())
        self.assertEqual(urllib.request.urlopen(f"http://127.0.0.1:{self.port}/p/{pid}", timeout=30).status, 200)
        self.assertEqual(json.loads((self.data / "zotero.json").read_text(encoding="utf-8"))["users/0/HTMLKEY2"]["paper"], pid)
        m = json.loads((self.data / "papers" / pid / "meta.json").read_text(encoding="utf-8"))   # Zotero's title, not the page's own
        self.assertEqual((m["title"], m["authors"], m["label"]), ("A saved page", ["Lovelace, Ada"], "Zotero · snap.html"))
        listed = {p["id"]: p for p in self.call("/api/papers")[1]}
        self.assertEqual((listed[pid]["title"], listed[pid].get("zotero")), ("A saved page", True))

    def test_a_pdf_waits_for_the_engine_as_a_dropped_one_does(self):
        code, d = self.call("/api/zotero/read", {"item": "users/0/PLNPKEY2", "lang": "en"})   # (no DOI: nothing looked up online)
        self.assertEqual((code, d.get("needs"), d["name"]), (200, "engine", "plain.pdf"), d)
        self.assertTrue(d["upload"].startswith("pdf-"))

    def test_an_arxiv_item_comes_back_as_its_reference(self):
        self.assertEqual(self.call("/api/zotero/read", {"item": "users/0/ARXVKEY2", "lang": "en"}), (200, {"ref": "2201.11903", "title": "Chain of thought"}))

    def test_refusals(self):
        self.assertEqual(self.call("/api/zotero/read", {"item": "users/0/NTHGKEY2"}), (400, {"error": "no-file"}))
        self.assertEqual(self.call("/api/zotero/read", {"item": "../../x/ABCDEFGH"}), (400, {"error": "bad-library"}))
        self.assertEqual(self.call("/api/zotero/library", {"library": "users/0"}, origin="https://example.com")[0], 403)
        code, st = self.call("/api/zotero/library", {"library": "groups/42", "base": "http://example.com", "data_dir": "/"})
        self.assertEqual((code, st["library"]), (200, "groups/42"))
        z = json.loads((self.home / ".textfold" / "settings.json").read_text(encoding="utf-8"))["zotero"]
        self.assertEqual((z["base"], z["data_dir"]), (self.base, self.settings["zotero"]["data_dir"]))   # only the library changed
        self.call("/api/zotero/library", {"library": "users/0"})


if __name__ == "__main__":
    unittest.main()
