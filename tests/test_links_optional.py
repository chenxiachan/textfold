"""The argument links fail, the levels stay (#91: a long PDF's sections came back, its one call for the links over
every takeaway was cut off, and the job failed with nothing stored). The links are an extra: the levels are stored
without them, and a generation of the same text later reads those levels rather than asking again. A model that stands
in for one, a data folder of its own; no network.

Run from the repo root:
    python3 -m unittest discover tests
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from textfold import llm, local, pipeline, store

TEXT = """# Heat pump diary

What the house did in the cold week.

## Monday

The heat pump ran all night. Its COP stayed above three, even at minus five.

## Tuesday

A cold front arrived early. The buffer tank emptied by noon and the backup heater started.
"""
SPEC = {"id": "fake/model", "kind": "api", "protocol": "openai", "base": "http://127.0.0.1:9", "api_key": "", "model": "fake"}


def fake_model(prompts):
    """A section's levels as a model gives them; the links' call fails, as a reply cut off midway does."""
    def once(prompt, schema, spec, timeout, system):
        prompts.append(prompt)
        if "Its units in reading order" not in prompt:
            raise RuntimeError("the reply's JSON does not parse")
        n = prompt.count("\n[U")
        return {"section": {"takeaway": "The section in brief", "topic": "The section"},
                "units": [{"id": f"U{k}", "role": "context", "takeaway": f"Point {k}", "topic": "Point",
                           "reason": "", "evidence": [0]} for k in range(n)]}, 0
    return once


class LinksFail(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        patch = mock.patch.object(store, "DATA", Path(tmp.name))
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_levels_are_kept_without_the_links(self):
        pid = local.import_text(TEXT, "diary.md")
        asked, said = [], []
        with mock.patch.object(llm, "_once", fake_model(asked)), mock.patch.dict(os.environ):
            os.environ.pop("TEXTFOLD_NO_MODELS", None)   # (the build runs the tests with it: the model here is a stand-in)
            pipeline.generate(pid, "en", SPEC, log=said.append)
            sections = sum("Its units in reading order" in p for p in asked)
            self.assertGreater(len(asked), sections)   # the links were asked for, and failed
            self.assertTrue(any(line.startswith("  ! links:") for line in said))
            self.assertIsNotNone(store.read(pid, "en"))
            doc = store.assemble(pid)
            self.assertTrue(all(u["lad"].get("model") == "fake/model" for u in doc["units"].values()))
            self.assertFalse([e for e in doc["edges"] if e.get("tk") == "unit"])   # no argument links
            asked.clear()
            pipeline.generate(pid, "en", SPEC)   # opened again: its levels, no call
            self.assertEqual(asked, [])


if __name__ == "__main__":
    unittest.main()
