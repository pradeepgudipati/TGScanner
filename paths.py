"""Filesystem locations that work both from a source checkout and a PyInstaller bundle.

When running from source, everything stays where it is today: the repo root holds
``.env`` / ``toi_session.session`` / ``*.log`` and the ``outputs`` / ``.cache``
directories. When frozen (``sys.frozen``), writable state moves to a per-user
config directory so the app never tries to write next to its own executable:

    Windows : %APPDATA%\\TelegramTOI
    macOS   : ~/Library/Application Support/TelegramTOI
    Linux   : ${XDG_CONFIG_HOME:-~/.config}/TelegramTOI

Set ``TELEGRAMTOI_HOME`` to force a specific directory in either mode.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

APP_NAME = "TelegramTOI"

_DEFAULT_ENV = """# TelegramTOI configuration
# Telegram API credentials (https://my.telegram.org -> API development tools)
TG_API_ID=
TG_API_HASH=

# OpenAI-compatible AI server (used by Magazine search and TOI --ai-query)
OPENAI_MODEL=gpt-4o-mini
OPENAI_API_KEY=
# For a local server (Ollama, LM Studio, vLLM, ...) set a base URL and leave the key blank:
# OPENAI_BASE_URL=http://127.0.0.1:11434/v1

# Only consider magazine channel posts from the last N days
MAGAZINE_MAX_AGE_DAYS=90
"""


def is_frozen() -> bool:
    """True when running inside a PyInstaller (or similar) bundle."""
    return bool(getattr(sys, "frozen", False))


def bundle_dir() -> Path:
    """Directory holding read-only bundled resources (the source dir when not frozen)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def config_dir() -> Path:
    """Per-user writable directory for a frozen app."""
    if sys.platform == "win32":
        root = Path(os.getenv("APPDATA") or Path.home() / "AppData" / "Roaming")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.getenv("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / APP_NAME


def data_dir() -> Path:
    """Root directory for all writable state (env, session, logs, outputs, cache)."""
    override = os.getenv("TELEGRAMTOI_HOME")
    if override:
        base = Path(override).expanduser()
    elif is_frozen():
        base = config_dir()
    else:
        base = Path(__file__).resolve().parent
    base.mkdir(parents=True, exist_ok=True)
    return base


def _dir(*parts: str) -> Path:
    path = data_dir().joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def env_file() -> Path:
    return data_dir() / ".env"


def session_base() -> Path:
    """Session path without the ``.session`` suffix, as Telethon expects."""
    return data_dir() / "toi_session"


def session_file() -> Path:
    return session_base().with_suffix(".session")


def logs_dir() -> Path:
    # Source checkout keeps logs in the repo root, as before.
    return (
        _dir("logs") if (is_frozen() or os.getenv("TELEGRAMTOI_HOME")) else data_dir()
    )


def outputs_dir() -> Path:
    return _dir("outputs")


def cache_dir() -> Path:
    return _dir(".cache", "magazine_search")


def set_env_values(values: dict[str, str]) -> Path:
    """Create/update ``KEY=value`` lines in the install's ``.env`` and os.environ.

    Existing lines and comments are preserved; listed keys are replaced in place
    (or appended if absent).
    """
    target = env_file()
    lines = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
    remaining = dict(values)
    out: list[str] = []
    for line in lines:
        stripped = line.lstrip()
        if "=" in line and not stripped.startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)
    for key, value in remaining.items():
        out.append(f"{key}={value}")
    target.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.environ.update({k: str(v) for k, v in values.items()})
    return target


def load_env() -> Path:
    """Load the ``.env`` for this install, seeding a template on first run when frozen."""
    from dotenv import load_dotenv

    target = env_file()
    if not target.exists() and (is_frozen() or os.getenv("TELEGRAMTOI_HOME")):
        sample = bundle_dir() / ".env.sample"
        try:
            seed = sample.read_text(encoding="utf-8")
        except OSError:
            seed = _DEFAULT_ENV
        target.write_text(seed, encoding="utf-8")

    if target.exists():
        load_dotenv(target)
    else:
        load_dotenv()
    return target
