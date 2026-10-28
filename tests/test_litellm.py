"""Models named through LiteLLM (LITELLM_MODELS): listed only when named, resolved to the litellm protocol, and called
through its SDK with the request llm_openai.py sends. A stand-in for the SDK is used; nothing here calls a model.

Run from the repo root:
    python3 -m unittest discover tests
"""
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from textfold import llm, llm_litellm, models, providers

SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
SPEC = {"id": "bedrock/anthropic.claude-sonnet-4-6", "kind": "api", "protocol": "litellm", "base": "", "api_key": "",
        "provider": "LiteLLM", "model": "bedrock/anthropic.claude-sonnet-4-6", "effort": None}


def reply(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def fake_litellm(completion, cost=0.0042):
    m = types.ModuleType("litellm")

    class APIError(Exception):
        def __init__(self, message=""):
            super().__init__(message)
            self.message = message

    errors = {name: type(name, (APIError,), {}) for name in
              ("BadRequestError", "AuthenticationError", "NotFoundError", "RateLimitError", "APIConnectionError")}
    m.exceptions = SimpleNamespace(APIError=APIError, **errors)
    m.completion = mock.Mock(side_effect=completion) if callable(completion) else mock.Mock(return_value=completion)
    m.completion_cost = mock.Mock(return_value=cost)
    return m


class Listed(unittest.TestCase):
    def env(self, **values):
        return mock.patch.object(providers, "environment", return_value=values)

    def test_not_listed_until_models_are_named(self):
        with self.env():
            self.assertNotIn("litellm", [p["key"] for p in providers.env_providers(False, [])])

    def test_named_models_are_listed_with_the_litellm_protocol(self):
        with self.env(LITELLM_MODELS="anthropic/claude-sonnet-4-6, vertex_ai/gemini-2.5-flash"):
            p = next(p for p in providers.env_providers(False, []) if p["key"] == "litellm")
        self.assertEqual(p["protocol"], "litellm")
        self.assertEqual([m["id"] for m in p["models"]], ["anthropic/claude-sonnet-4-6", "vertex_ai/gemini-2.5-flash"])
        self.assertEqual(p["base"], "")       # each provider's own endpoint
        self.assertEqual(p["api_key"], "")    # each provider's own credentials

    def test_an_endpoint_can_be_named(self):
        with self.env(LITELLM_MODELS="ollama/llama3", LITELLM_API_BASE="http://localhost:11434/"):
            p = next(p for p in providers.env_providers(False, []) if p["key"] == "litellm")
        self.assertEqual(p["base"], "http://localhost:11434")

    def test_resolves_to_the_litellm_protocol(self):
        settings = {"model": None, "providers": [], "use_thoughtdag_env": False}
        with self.env(LITELLM_MODELS="vertex_ai/gemini-2.5-flash"), \
             mock.patch.object(providers, "ollama_models", return_value=[]), \
             mock.patch.object(models.bridges, "bridge_provider", return_value=None):
            spec = models.resolve("vertex_ai/gemini-2.5-flash", settings)
        self.assertEqual((spec["kind"], spec["protocol"], spec["model"]), ("api", "litellm", "vertex_ai/gemini-2.5-flash"))


class Called(unittest.TestCase):
    def sdk(self, completion, cost=0.0042):
        m = fake_litellm(completion, cost)
        patcher = mock.patch.dict(sys.modules, {"litellm": m})
        patcher.start()
        self.addCleanup(patcher.stop)
        return m

    def test_once_dispatches_litellm_specs(self):
        with mock.patch.object(llm_litellm, "call", return_value=({"ok": True}, None)) as call:
            self.assertEqual(llm._once("p", SCHEMA, SPEC, 30, "sys"), ({"ok": True}, None))
        call.assert_called_once_with("p", SCHEMA, SPEC, "sys", 30)

    def test_request_is_the_openai_one_with_json_mode_and_dropped_params(self):
        sdk = self.sdk(reply('{"ok": true}'))
        out, cost = llm_litellm.call("Fold this.", SCHEMA, SPEC, "You fold papers.", 120)
        self.assertEqual(out, {"ok": True})
        self.assertEqual(cost, 0.0042)
        kw = sdk.completion.call_args.kwargs
        self.assertEqual(kw["model"], "bedrock/anthropic.claude-sonnet-4-6")
        self.assertEqual(kw["messages"][0], {"role": "system", "content": "You fold papers."})
        self.assertIn(json.dumps(SCHEMA), kw["messages"][1]["content"])
        self.assertEqual(kw["response_format"], {"type": "json_object"})
        self.assertTrue(kw["drop_params"])
        self.assertEqual(kw["timeout"], 120)
        self.assertNotIn("api_base", kw)
        self.assertNotIn("api_key", kw)
        self.assertNotIn("reasoning_effort", kw)

    def test_endpoint_and_reasoning_level_are_passed_on(self):
        sdk = self.sdk(reply('{"ok": true}'))
        llm_litellm.call("p", SCHEMA, {**SPEC, "base": "http://localhost:11434", "effort": "high"}, "s", 60)
        kw = sdk.completion.call_args.kwargs
        self.assertEqual(kw["api_base"], "http://localhost:11434")
        self.assertEqual(kw["reasoning_effort"], "high")

    def test_off_leaves_the_reasoning_to_the_model(self):
        sdk = self.sdk(reply('{"ok": true}'))
        llm_litellm.call("p", SCHEMA, {**SPEC, "effort": "off"}, "s", 60)
        self.assertNotIn("reasoning_effort", sdk.completion.call_args.kwargs)

    def test_json_mode_refused_is_asked_again_without_it(self):
        calls = []

        def completion(**kw):
            calls.append(kw)
            if "response_format" in kw:
                raise sdk.exceptions.BadRequestError("response_format is not supported")
            return reply('Here it is:\n```json\n{"ok": false}\n```')

        sdk = self.sdk(completion)
        out, _ = llm_litellm.call("p", SCHEMA, SPEC, "s", 60)
        self.assertEqual(out, {"ok": False})
        self.assertEqual(["response_format" in c for c in calls], [True, False])

    def test_errors_read_as_the_reader_needs(self):
        for error, said in (("AuthenticationError", "rejected the credentials"), ("NotFoundError", "not found"),
                            ("RateLimitError", "rate limited"), ("APIConnectionError", "could not connect")):
            with self.subTest(error=error):
                sdk = fake_litellm(None)
                sdk.completion.side_effect = getattr(sdk.exceptions, error)("upstream said no")
                with mock.patch.dict(sys.modules, {"litellm": sdk}):
                    with self.assertRaisesRegex(RuntimeError, said):
                        llm_litellm.call("p", SCHEMA, SPEC, "s", 60)

    def test_a_provider_error_litellm_leaves_unmapped_reads_the_same_way(self):
        sdk = self.sdk(None)
        sdk.completion.side_effect = RuntimeError("vertexai import failed please run `pip install google-cloud-aiplatform`")
        with self.assertRaisesRegex(RuntimeError, r"LiteLLM \(bedrock/anthropic.claude-sonnet-4-6\): vertexai import failed"):
            llm_litellm.call("p", SCHEMA, SPEC, "s", 60)

    def test_a_reply_without_json_is_an_error(self):
        self.sdk(reply("I cannot help with that."))
        with self.assertRaisesRegex(RuntimeError, "no JSON in reply"):
            llm_litellm.call("p", SCHEMA, SPEC, "s", 60)

    def test_unpriced_models_report_no_cost(self):
        sdk = self.sdk(reply('{"ok": true}'))
        sdk.completion_cost.side_effect = Exception("This model isn't mapped yet")
        self.assertEqual(llm_litellm.call("p", SCHEMA, SPEC, "s", 60), ({"ok": True}, None))

    def test_without_the_sdk_the_install_is_named(self):
        with mock.patch.dict(sys.modules, {"litellm": None}):
            with self.assertRaisesRegex(RuntimeError, r"textfold\[litellm\]"):
                llm_litellm.call("p", SCHEMA, SPEC, "s", 60)

    def test_a_cached_reply_records_the_cost(self):
        self.sdk(reply('{"ok": true}'))
        with tempfile.TemporaryDirectory() as tmp:
            rec, cached = llm.call("p", SCHEMA, SPEC, Path(tmp))
        self.assertFalse(cached)
        self.assertEqual((rec["out"], rec["cost"]), ({"ok": True}, 0.0042))


if __name__ == "__main__":
    unittest.main()
