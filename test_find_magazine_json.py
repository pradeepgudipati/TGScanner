"""Unit tests for magazine AI JSON parsing helpers."""

from __future__ import annotations

import unittest

from find_magazine import MagazineSearcher, completion_token_budget


class ParseLlmJsonTests(unittest.TestCase):
    def test_strips_markdown_and_trailing_commas(self) -> None:
        raw = """```json
{
  "0": {"decision": "RELEVANT", "confidence": 0.9,},
  "1": {"decision": "NOT_RELEVANT", "confidence": 0.1,},
}
```"""
        data = MagazineSearcher._parse_llm_json(raw)
        self.assertEqual(data["0"]["decision"], "RELEVANT")
        self.assertEqual(data["1"]["decision"], "NOT_RELEVANT")

    def test_extracts_object_from_prose(self) -> None:
        raw = 'Here you go:\n{"0": {"decision": "UNCERTAIN", "confidence": 0.5}}\nThanks'
        data = MagazineSearcher._parse_llm_json(raw)
        self.assertEqual(data["0"]["decision"], "UNCERTAIN")

    def test_normalize_string_and_alias_decisions(self) -> None:
        raw = {
            "0": "RELEVANT",
            "1": {"decision": "R", "confidence": "0.8"},
            "2": {"decision": "maybe", "confidence": 0.4},
        }
        out = MagazineSearcher._normalize_decisions(raw)
        self.assertEqual(out["0"]["decision"], "RELEVANT")
        self.assertEqual(out["1"]["decision"], "RELEVANT")
        self.assertEqual(out["1"]["confidence"], 0.8)
        self.assertEqual(out["2"]["decision"], "UNCERTAIN")


class CompletionTokenBudgetTests(unittest.TestCase):
    def test_scales_with_batch_size(self) -> None:
        small = completion_token_budget(10)
        large = completion_token_budget(500)
        self.assertGreaterEqual(small, 2048)
        self.assertGreater(large, small)
        self.assertEqual(large, 500 * 64 + 512)


if __name__ == "__main__":
    unittest.main()
