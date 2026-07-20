"""Telegram message deep-link helpers."""

from __future__ import annotations

from typing import Optional, Union


def bare_channel_id(channel_id: Union[int, str]) -> str:
    """Strip Telethon/Bot API peer prefixes for privatepost channel ids."""
    text = str(channel_id)
    if text.startswith("-100"):
        return text[4:]
    if text.startswith("-"):
        return text[1:]
    return text


def message_deep_link(
    *,
    channel_id: Union[int, str],
    msg_id: int,
    username: Optional[str] = None,
) -> str:
    """Build a tg:// deep link that opens a channel/supergroup message.

    Public: tg://resolve?domain=<username>&post=<id>
    Private: tg://privatepost?channel=<bare_id>&post=<id>
    """
    post = int(msg_id)
    if username:
        domain = username.strip().lstrip("@")
        return f"tg://resolve?domain={domain}&post={post}"
    return f"tg://privatepost?channel={bare_channel_id(channel_id)}&post={post}"
