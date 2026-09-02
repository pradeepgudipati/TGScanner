"""Telegram authentication helpers for the desktop GUI.

The GUI drives an interactive Telethon sign-in (phone -> code -> optional 2FA
password). The authorised session is written to ``paths.session_file()`` inside
the per-user config directory and reused on every launch. On Windows the file's
ACL is tightened to the current user; on POSIX it is chmod 0600.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading
from concurrent.futures import Future
from typing import Optional

import paths

try:
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
except Exception:  # pragma: no cover - telethon always present at runtime
    TelegramClient = None  # type: ignore
    SessionPasswordNeededError = Exception  # type: ignore


def api_credentials() -> tuple[Optional[str], Optional[str]]:
    """Return (api_id, api_hash) from the environment, or (None, None)."""
    api_id = (os.getenv("TG_API_ID") or "").strip() or None
    api_hash = (os.getenv("TG_API_HASH") or "").strip() or None
    return api_id, api_hash


def credentials_present() -> bool:
    api_id, api_hash = api_credentials()
    if not api_id or not api_hash:
        return False
    try:
        int(api_id)
    except ValueError:
        return False
    return True


def session_present() -> bool:
    """Offline check: are API credentials set and an auth key stored on disk?"""
    if not credentials_present() or not paths.session_file().exists():
        return False
    try:
        from telethon.sessions import SQLiteSession

        session = SQLiteSession(str(paths.session_base()))
        try:
            return session.auth_key is not None
        finally:
            session.close()
    except Exception:
        return False


def harden_session_permissions() -> None:
    """Restrict the session file to the current OS user, best effort."""
    path = paths.session_file()
    if not path.exists():
        return
    try:
        if os.name == "posix":
            os.chmod(path, 0o600)
        elif sys.platform == "win32":
            user = os.environ.get("USERNAME") or os.environ.get("USER")
            if user:
                subprocess.run(
                    [
                        "icacls",
                        str(path),
                        "/inheritance:r",
                        "/grant:r",
                        f"{user}:F",
                    ],
                    capture_output=True,
                    check=False,
                )
    except Exception:
        pass


def sign_out() -> None:
    """Delete the stored session so the next launch requires a fresh login."""
    base = paths.session_file()
    for suffix in (".session", ".session-journal", ".session-wal", ".session-shm"):
        candidate = base.with_suffix(suffix)
        try:
            candidate.unlink()
        except OSError:
            pass


class AsyncWorker:
    """A background thread running a private asyncio loop for Telethon calls."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run, name="telethon-loop", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro) -> "Future":
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def shutdown(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)


class LoginFlow:
    """Holds one connected client across the multi-step sign-in."""

    def __init__(self, worker: AsyncWorker) -> None:
        self._worker = worker
        self._client: Optional["TelegramClient"] = None
        self._phone = ""

    async def _ensure_client(self) -> None:
        if self._client is None:
            api_id, api_hash = api_credentials()
            self._client = TelegramClient(
                str(paths.session_base()), int(api_id), api_hash
            )
        if not self._client.is_connected():
            await self._client.connect()

    async def _send_code(self, phone: str) -> str:
        await self._ensure_client()
        self._phone = phone
        assert self._client is not None
        if await self._client.is_user_authorized():
            return "authorized"
        await self._client.send_code_request(phone)
        return "code_sent"

    async def _sign_in(self, code: str) -> str:
        assert self._client is not None
        try:
            await self._client.sign_in(phone=self._phone, code=code)
        except SessionPasswordNeededError:
            return "password_needed"
        return "done"

    async def _sign_in_password(self, password: str) -> str:
        assert self._client is not None
        await self._client.sign_in(password=password)
        return "done"

    async def _finalize(self) -> str:
        assert self._client is not None
        me = await self._client.get_me()
        await self._client.disconnect()
        harden_session_permissions()
        name = getattr(me, "first_name", None) or getattr(me, "username", None)
        return name or "your account"

    async def _cancel(self) -> None:
        if self._client is not None:
            try:
                await self._client.disconnect()
            except Exception:
                pass

    # Public API — each returns a concurrent.futures.Future.
    def send_code(self, phone: str) -> "Future":
        return self._worker.submit(self._send_code(phone))

    def sign_in(self, code: str) -> "Future":
        return self._worker.submit(self._sign_in(code))

    def sign_in_password(self, password: str) -> "Future":
        return self._worker.submit(self._sign_in_password(password))

    def finalize(self) -> "Future":
        return self._worker.submit(self._finalize())

    def cancel(self) -> "Future":
        return self._worker.submit(self._cancel())
