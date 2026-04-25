import os
import unittest
from unittest.mock import patch

with patch("openai.OpenAI"):
    with patch("anthropic.Anthropic"):
        from utils.ai_helper import AIHelper, get_models_config

mock_config_data = {
    "openai_image": "mock-gpt-5-mini",
    "openai-big_image": "mock-gpt-5",
    "claude_image": "mock-claude-sonnet",
    "gemini_image": "mock-gemini-3.0",
    "grok_image": "mock-grok",
    "rag_llm_image": "mock-gpt-5-mini"
}

class TestAIHelperConfig(unittest.TestCase):
    @patch("utils.ai_helper.get_models_config", return_value=mock_config_data)
    def test_ai_helper_openai_model_resolution(self, mock_func):
        helper = AIHelper(key="test", model="openai")
        self.assertEqual(helper.exact_model, "mock-gpt-5-mini")

    @patch("utils.ai_helper.get_models_config", return_value=mock_config_data)
    def test_ai_helper_claude_model_resolution(self, mock_func):
        helper = AIHelper(key="test", model="claude")
        self.assertEqual(helper.exact_model, "mock-claude-sonnet")

    @patch("utils.ai_helper.get_models_config", return_value={})
    def test_ai_helper_fallback_behavior(self, mock_func):
        helper = AIHelper(key="test", model="openai")
        self.assertEqual(helper.exact_model, "gpt-5-mini")  # Default fallback

    @patch("utils.ai_helper.get_models_config", return_value=mock_config_data)
    def test_ai_helper_grok_model_resolution(self, mock_func):
        helper = AIHelper(key="test", model="grok")
        self.assertEqual(helper.exact_model, "mock-grok")

if __name__ == "__main__":
    unittest.main()
