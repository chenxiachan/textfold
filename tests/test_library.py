"""library.py: the reader's folders in the library (inside one another), the names they give documents, and when
each document came in and was last opened, in a data folder of its own (store.DATA, set to a temporary one for each
test).

Run from the repo root:
    python3 -m unittest discover tests
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from textfold import library, store


class Library(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        patch = mock.patch.object(store, "DATA", Path(self.tmp.name))
        patch.start()
        self.addCleanup(patch.stop)
        self.addCleanup(self.tmp.cleanup)

    def paper(self, pid, at):
        d = store.pdir(pid)
        d.mkdir(parents=True)
        for name in ("meta.json", "source.md"):
            (d / name).write_text("{}", encoding="utf-8")
            os.utime(d / name, (at, at))
        return {"id": pid, "title": pid}

    def test_folders_made_named_and_filled(self):
        a = library.change({"op": "new", "name": "  Thesis   chapter 2 "})["folders"][0]
        self.assertEqual(a["name"], "Thesis chapter 2")
        b = library.change({"op": "new", "name": "Reading group"})["folders"][1]
        got = library.change({"op": "move", "papers": ["2512.24601", "wiki-en-13460"], "folder": a["id"]})
        self.assertEqual(got["of"], {"2512.24601": a["id"], "wiki-en-13460": a["id"]})
        got = library.change({"op": "move", "papers": ["2512.24601"], "folder": b["id"]})   # (one folder at a time)
        self.assertEqual(got["of"]["2512.24601"], b["id"])
        got = library.change({"op": "move", "papers": ["wiki-en-13460"], "folder": None})
        self.assertNotIn("wiki-en-13460", got["of"])
        self.assertEqual(library.change({"op": "rename", "id": b["id"], "name": "Group"})["folders"][1]["name"], "Group")
        self.assertEqual(json.loads(library.path().read_text(encoding="utf-8"))["folders"][1]["name"], "Group")

    def test_stale_view_does_not_erase_a_concurrently_added_folder(self):
        from textfold.sqlite_storage import SQLiteBackend
        context = store.use_backend(SQLiteBackend(store.DATA / "cloud-workspace"))
        context.__enter__(); self.addCleanup(context.__exit__, None, None, None)
        a, b = library.load(), library.load()
        a['folders'].append({'id': 'f-laptop', 'name': 'Laptop'})
        b['folders'].append({'id': 'f-phone', 'name': 'Phone'})
        library._save(a); library._save(b)
        self.assertEqual({f['id'] for f in library.folders()['folders']}, {'f-laptop', 'f-phone'})

    def test_a_new_folder_with_the_document_it_was_made_for(self):   # (the menu a document's right click opens)
        a = library.change({"op": "new", "name": "A"})["folders"][0]
        library.change({"op": "move", "papers": ["2512.24601"], "folder": a["id"]})
        got = library.change({"op": "new", "name": "B", "papers": ["2512.24601"]})
        self.assertEqual(got["of"], {"2512.24601": got["folders"][1]["id"]})
        with self.assertRaises(ValueError):
            library.change({"op": "new", "name": "C", "papers": ["../etc"]})
        self.assertEqual(len(library.folders()["folders"]), 2)

    def test_a_folder_deleted_leaves_its_documents(self):
        a = library.change({"op": "new", "name": "Old"})["folders"][0]
        library.change({"op": "move", "papers": ["2512.24601"], "folder": a["id"]})
        got = library.change({"op": "delete", "id": a["id"]})
        self.assertEqual(got, {"folders": [], "of": {}})

    def test_folders_inside_folders(self):
        top = library.change({"op": "new", "name": "Thesis"})["folders"][0]
        ch = library.change({"op": "new", "name": "Chapter 2", "parent": top["id"]})["folders"][1]
        sec = library.change({"op": "new", "name": "Related work", "parent": ch["id"]})["folders"][2]
        self.assertEqual((ch["parent"], sec["parent"]), (top["id"], ch["id"]))
        library.change({"op": "move", "papers": ["2512.24601"], "folder": sec["id"]})
        got = library.change({"op": "nest", "id": sec["id"], "parent": None})   # (to the top, its documents with it)
        self.assertNotIn("parent", got["folders"][2])
        self.assertEqual(got["of"], {"2512.24601": sec["id"]})
        got = library.change({"op": "nest", "id": sec["id"], "parent": top["id"]})
        self.assertEqual(got["folders"][2]["parent"], top["id"])
        on_disk = json.loads(library.path().read_text(encoding="utf-8"))
        self.assertEqual([f.get("parent") for f in on_disk["folders"]], [None, top["id"], top["id"]])

    def test_not_inside_itself(self):
        a = library.change({"op": "new", "name": "A"})["folders"][0]
        b = library.change({"op": "new", "name": "B", "parent": a["id"]})["folders"][1]
        c = library.change({"op": "new", "name": "C", "parent": b["id"]})["folders"][2]
        for to in (a["id"], b["id"], c["id"]):   # into itself, or into a folder inside it
            with self.assertRaises(ValueError):
                library.change({"op": "nest", "id": a["id"], "parent": to})
        for body in ({"op": "new", "name": "D", "parent": "f-none"}, {"op": "nest", "id": "f-none", "parent": None},
                     {"op": "nest", "id": b["id"], "parent": "f-none"}):
            with self.assertRaises(ValueError, msg=body):
                library.change(body)
        self.assertEqual([f.get("parent") for f in library.folders()["folders"]], [None, a["id"], b["id"]])

    def test_a_folder_deleted_moves_what_is_in_it_up(self):
        a = library.change({"op": "new", "name": "A"})["folders"][0]
        b = library.change({"op": "new", "name": "B", "parent": a["id"]})["folders"][1]
        c = library.change({"op": "new", "name": "C", "parent": b["id"]})["folders"][2]
        library.change({"op": "move", "papers": ["2512.24601"], "folder": b["id"]})
        library.change({"op": "move", "papers": ["wiki-en-13460"], "folder": c["id"]})
        got = library.change({"op": "delete", "id": b["id"]})
        self.assertEqual([(f["name"], f.get("parent")) for f in got["folders"]], [("A", None), ("C", a["id"])])
        self.assertEqual(got["of"], {"2512.24601": a["id"], "wiki-en-13460": c["id"]})
        got = library.change({"op": "delete", "id": a["id"]})   # (at the top: what was in it, at the top)
        self.assertEqual([(f["name"], f.get("parent")) for f in got["folders"]], [("C", None)])
        self.assertEqual(got["of"], {"wiki-en-13460": c["id"]})

    def test_a_library_edited_by_hand_has_no_loop(self):
        library.path().write_text(json.dumps({"folders": [
            {"id": "f-a", "name": "A", "parent": "f-b"}, {"id": "f-b", "name": "B", "parent": "f-a"},
            {"id": "f-c", "name": "C", "parent": "f-gone"}, {"id": "f-d", "name": "D", "parent": "f-c"}]}), encoding="utf-8")
        fs = {f["id"]: f.get("parent") for f in library.folders()["folders"]}
        self.assertEqual((fs["f-c"], fs["f-d"]), (None, "f-c"))   # a parent that is gone: at the top
        self.assertTrue(fs["f-a"] is None or fs["f-b"] is None)   # a loop: broken, every folder reachable from the top

    def test_a_document_named_in_the_library(self):
        listed = library.stamp([self.paper("pdf-0123456789abcdef", 1_790_000_000), self.paper("2512.24601", 1_790_000_000)])
        self.assertNotIn("name", listed[0])
        library.name("pdf-0123456789abcdef", "  Smith   2024 review ")
        listed = library.stamp([{"id": "pdf-0123456789abcdef", "title": "untitled"}, {"id": "2512.24601", "title": "x"}])
        self.assertEqual(listed[0]["name"], "Smith 2024 review")
        self.assertEqual(listed[0]["title"], "untitled")   # (its own title stays its own)
        self.assertNotIn("name", listed[1])
        self.assertEqual(library.name_of("pdf-0123456789abcdef"), "Smith 2024 review")
        library.name("pdf-0123456789abcdef", "")   # (cleared: its title again)
        self.assertIsNone(library.name_of("pdf-0123456789abcdef"))
        library.name("2512.24601", "Kept")
        library.forget("2512.24601")
        self.assertIsNone(library.name_of("2512.24601"))
        with self.assertRaises(ValueError):
            library.name("../etc", "x")

    def test_what_is_refused(self):
        a = library.change({"op": "new", "name": "A"})["folders"][0]
        for body in ({"op": "new", "name": "   "}, {"op": "rename", "id": "f-none", "name": "B"}, {"op": "delete", "id": "f-none"},
                     {"op": "move", "papers": ["2512.24601"], "folder": "f-none"}, {"op": "move", "papers": ["../etc"], "folder": a["id"]},
                     {"op": "move", "papers": "2512.24601", "folder": a["id"]}, {"op": "drop"}):
            with self.assertRaises(ValueError, msg=body):
                library.change(body)
        self.assertEqual(library.folders()["of"], {})

    def test_when_it_came_in_is_its_oldest_file_and_stays(self):
        listed = library.stamp([self.paper("2512.24601", 1_790_000_000), self.paper("wiki-en-13460", 1_791_000_000)])
        self.assertEqual([p["added"] for p in listed], [1_790_000_000, 1_791_000_000])
        self.assertEqual([p["opened"] for p in listed], [None, None])
        os.utime(store.pdir("2512.24601") / "meta.json", (1_700_000_000, 1_700_000_000))   # (its files changed since: the date stays)
        self.assertEqual(library.stamp([{"id": "2512.24601"}])[0]["added"], 1_790_000_000)

    def test_opened_and_forgotten(self):
        library.stamp([self.paper("2512.24601", 1_790_000_000)])
        library.opened("2512.24601")
        library.opened("../etc")   # (not a document's id: nothing)
        self.assertGreater(library.stamp([{"id": "2512.24601"}])[0]["opened"], 1_790_000_000)
        a = library.change({"op": "new", "name": "A"})["folders"][0]
        library.change({"op": "move", "papers": ["2512.24601"], "folder": a["id"]})
        library.forget("2512.24601")
        d = library.load()
        self.assertEqual((d["of"], d["names"], d["added"], d["opened"]), ({}, {}, {}, {}))

    def test_a_damaged_file_reads_as_none(self):
        library.path().write_text("{not json", encoding="utf-8")
        self.assertEqual(library.folders(), {"folders": [], "of": {}})


if __name__ == "__main__":
    unittest.main()
