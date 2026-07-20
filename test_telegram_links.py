"""Unit tests for Telegram message deep-link helpers."""

from __future__ import annotations

import unittest

from telegram_links import bare_channel_id, message_deep_link


class TelegramLinksTests(unittest.TestCase):
    def test_bare_channel_id_strips_bot_api_prefix(self) -> None:
        self.assertEqual(bare_channel_id(-1001567920615), "1567920615")
        self.assertEqual(bare_channel_id("-1001567920615"), "1567920615")

    def test_public_channel_uses_resolve(self) -> None:
        link = message_deep_link(
            channel_id=-1001567920615, msg_id=173550, username="@mintnews"
        )
        self.assertEqual(link, "tg://resolve?domain=mintnews&post=173550")

    def test_private_channel_uses_privatepost(self) -> None:
        link = message_deep_link(channel_id=-1001567920615, msg_id=173550)
        self.assertEqual(link, "tg://privatepost?channel=1567920615&post=173550")


if __name__ == "__main__":
    unittest.main()
