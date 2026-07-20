"""Unit tests for openai_compat.load_openai_compat."""

from __future__ import annotations

import os
import unittest
from unittest.mock import MagicMock, patch

from openai_compat import (
    PLACEHOLDER_API_KEY,
    OpenAICompatConfigError,
    load_openai_compat,
)


class LoadOpenAICompatTests(unittest.TestCase):
    def setUp(self) -> None:
        self._env_keys = ("OPENAI_MODEL", "OPENAI_API_KEY", "OPENAI_BASE_URL")
        self._saved = {k: os.environ.get(k) for k in self._env_keys}
        for k in self._env_keys:
            os.environ.pop(k, None)

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_missing_model_raises(self) -> None:
        with self.assertRaises(OpenAICompatConfigError) as ctx:
            load_openai_compat()
        self.assertIn("OPENAI_MODEL", str(ctx.exception))

    def test_key_unset_uses_placeholder(self) -> None:
        os.environ["OPENAI_MODEL"] = "local-model"
        with patch("openai_compat.AsyncOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = load_openai_compat()
        mock_cls.assert_called_once_with(api_key=PLACEHOLDER_API_KEY)
        self.assertEqual(result.model, "local-model")

    def test_base_url_set_passed_to_client(self) -> None:
        os.environ["OPENAI_MODEL"] = "local-model"
        os.environ["OPENAI_BASE_URL"] = "http://127.0.0.1:11434/v1"
        with patch("openai_compat.AsyncOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            load_openai_compat()
        mock_cls.assert_called_once_with(
            api_key=PLACEHOLDER_API_KEY,
            base_url="http://127.0.0.1:11434/v1",
        )

    def test_base_url_unset_omits_arg(self) -> None:
        os.environ["OPENAI_MODEL"] = "gpt-4o-mini"
        os.environ["OPENAI_API_KEY"] = "sk-test"
        with patch("openai_compat.AsyncOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            load_openai_compat()
        mock_cls.assert_called_once_with(api_key="sk-test")

    def test_model_and_key_from_env(self) -> None:
        os.environ["OPENAI_MODEL"] = "my-model"
        os.environ["OPENAI_API_KEY"] = "sk-real"
        with patch("openai_compat.AsyncOpenAI") as mock_cls:
            mock_cls.return_value = MagicMock()
            result = load_openai_compat()
        self.assertEqual(result.model, "my-model")
        mock_cls.assert_called_once_with(api_key="sk-real")


if __name__ == "__main__":
    unittest.main()
