"""Atlas Cloud is offered as a preset and read from ATLASCLOUD_API_KEY. No network.

Run from the repo root:
    python3 -m unittest discover tests
"""
import os
import unittest
from unittest import mock

from textfold import llm_openai, providers


class AtlasCloud(unittest.TestCase):
    def test_preset_recommends_models_it_lists(self):
        p = next(x for x in providers.PRESETS if x["id"] == "atlascloud")
        self.assertEqual(p["base"], "https://api.atlascloud.ai/v1")
        probed = [{"id": i} for i in ("moonshotai/kimi-k3", "zai-org/glm-5.3", "some/other")]
        self.assertEqual(providers.preselect(probed, p), ["moonshotai/kimi-k3", "zai-org/glm-5.3"])

    def test_key_in_the_environment_registers_the_provider(self):
        with mock.patch.dict(os.environ, {"ATLASCLOUD_API_KEY": "k"}):
            got = [x for x in providers.env_providers(use_thoughtdag=False) if x["key"] == "atlascloud"]
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["base"], "https://api.atlascloud.ai/v1")
        self.assertEqual(got[0]["models"][0]["id"], "deepseek-ai/deepseek-v4-flash")

    def test_user_agent_only_for_atlas(self):
        # Atlas Cloud's Cloudflare front answers Python's default urllib user agent with 403 (1010)
        self.assertTrue(llm_openai.auth("https://api.atlascloud.ai/v1", "k")["User-Agent"].startswith("TextFold/"))
        self.assertEqual(llm_openai.auth("https://api.deepseek.com/v1", "k"), {"Authorization": "Bearer k"})


if __name__ == "__main__":
    unittest.main()
