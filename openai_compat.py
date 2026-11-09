"""OpenAI-compatible client configuration shared by magazine and TOI AI paths."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlparse

try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover
    AsyncOpenAI = None  # type: ignore

PLACEHOLDER_API_KEY = "not-needed"
OPENAI_DEFAULT_HOST = "api.openai.com"


class OpenAICompatConfigError(ValueError):
    """Raised when required OpenAI-compatible configuration is missing."""


@dataclass(frozen=True)
class OpenAICompat:
    client: "AsyncOpenAI"
    model: str


def _env_hint() -> str:
    try:
        import paths

        return f" Check config in: {paths.env_file()}"
    except Exception:
        return ""


def load_openai_compat(
    *,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    model: Optional[str] = None,
) -> OpenAICompat:
    """Build an AsyncOpenAI client from env (or explicit overrides).

    Env vars:
      OPENAI_MODEL (required)
      OPENAI_API_KEY (optional; uses placeholder when unset)
      OPENAI_BASE_URL (optional; omit for SDK default host)
    """
    if AsyncOpenAI is None:
        raise OpenAICompatConfigError(
            "openai package not installed. Add openai to pyproject.toml and run uv sync"
        )

    resolved_model = (model if model is not None else os.getenv("OPENAI_MODEL") or "").strip()
    if not resolved_model:
        raise OpenAICompatConfigError(
            "OPENAI_MODEL is required. Set it in .env to your OpenAI-compatible model id."
        )

    if api_key is not None:
        resolved_key = api_key.strip() or PLACEHOLDER_API_KEY
    else:
        env_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        resolved_key = env_key or PLACEHOLDER_API_KEY

    if base_url is not None:
        resolved_base = base_url.strip() or None
    else:
        resolved_base = (os.getenv("OPENAI_BASE_URL") or "").strip() or None

    if resolved_key == PLACEHOLDER_API_KEY:
        if not resolved_base:
            raise OpenAICompatConfigError(
                "OPENAI_API_KEY is required when OPENAI_BASE_URL is not set."
                " For local OpenAI-compatible servers, set OPENAI_BASE_URL"
                " (for example http://127.0.0.1:11434/v1) and leave OPENAI_API_KEY blank."
                + _env_hint()
            )

        host = (urlparse(resolved_base).hostname or "").lower()
        if host == OPENAI_DEFAULT_HOST:
            raise OpenAICompatConfigError(
                "OPENAI_API_KEY is required when OPENAI_BASE_URL points to api.openai.com."
                " Add a real OPENAI_API_KEY or use a local OPENAI_BASE_URL."
                + _env_hint()
            )

    kwargs = {"api_key": resolved_key}
    if resolved_base:
        kwargs["base_url"] = resolved_base

    return OpenAICompat(client=AsyncOpenAI(**kwargs), model=resolved_model)
