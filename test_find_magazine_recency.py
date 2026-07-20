"""Unit tests for magazine message recency filtering."""

from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone

from find_magazine import is_message_recent, message_cutoff, resolve_max_age_days


class MessageRecencyTests(unittest.TestCase):
    def test_cutoff_is_max_age_days_ago(self) -> None:
        now = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)
        cutoff = message_cutoff(90, now=now)
        self.assertEqual(cutoff, now - timedelta(days=90))

    def test_accepts_recent_and_rejects_old(self) -> None:
        now = datetime(2026, 7, 21, tzinfo=timezone.utc)
        cutoff = message_cutoff(90, now=now)
        recent = now - timedelta(days=30)
        old = now - timedelta(days=400)  # 2025-ish / 2024-era relative to window
        self.assertTrue(is_message_recent(recent, cutoff))
        self.assertFalse(is_message_recent(old, cutoff))

    def test_naive_dates_treated_as_utc(self) -> None:
        cutoff = datetime(2026, 4, 22, tzinfo=timezone.utc)
        naive_recent = datetime(2026, 6, 1)
        naive_old = datetime(2024, 1, 1)
        self.assertTrue(is_message_recent(naive_recent, cutoff))
        self.assertFalse(is_message_recent(naive_old, cutoff))


class ResolveMaxAgeDaysTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = os.environ.get("MAGAZINE_MAX_AGE_DAYS")
        os.environ.pop("MAGAZINE_MAX_AGE_DAYS", None)

    def tearDown(self) -> None:
        if self._saved is None:
            os.environ.pop("MAGAZINE_MAX_AGE_DAYS", None)
        else:
            os.environ["MAGAZINE_MAX_AGE_DAYS"] = self._saved

    def test_cli_override_wins(self) -> None:
        os.environ["MAGAZINE_MAX_AGE_DAYS"] = "30"
        self.assertEqual(resolve_max_age_days(120), 120)

    def test_env_value_used_when_no_cli(self) -> None:
        os.environ["MAGAZINE_MAX_AGE_DAYS"] = "45"
        self.assertEqual(resolve_max_age_days(None), 45)

    def test_default_when_env_unset(self) -> None:
        self.assertEqual(resolve_max_age_days(None), 90)


if __name__ == "__main__":
    unittest.main()
