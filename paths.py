"""Filesystem locations for source checkout and PyInstaller builds.

By default, writable app state lives next to the running code:
- source checkout: repo root
- frozen app: executable directory (for example dist/TelegramTOI/)

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
    """Writable directory for a frozen app (same folder as the executable)."""
    return Path(sys.executable).resolve().parent


def user_data_dir() -> Path:
    """Stable writable directory for the installed app, outside the build output."""
    override = os.getenv("TELEGRAMTOI_HOME")
    if override:
        return Path(override).expanduser().resolve()

    if sys.platform.startswith("win"):
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        return base / APP_NAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_NAME

    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME


def data_dir() -> Path:
    """Root directory for all writable state (env, session, logs, outputs, cache)."""
    override = os.getenv("TELEGRAMTOI_HOME")
    if override:
        base = Path(override).expanduser()
    elif is_frozen():
        base = user_data_dir()
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
    """Load env values with precedence: user .env, bundled .env, bundled .env.sample."""
    from dotenv import load_dotenv

    target = env_file()
    if target.exists():
        load_dotenv(target)
        return target

    bundled = bundle_dir() / ".env"
    if bundled.exists():
        load_dotenv(bundled)
        return bundled

    sample = bundle_dir() / ".env.sample"
    if sample.exists():
        load_dotenv(sample)
        return sample

    load_dotenv()
    return target
