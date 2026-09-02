"""In-GUI Telegram sign-in screen (customtkinter).

Shown before the main window when there is no stored session, and reachable
later from the sidebar's *Account* button. On success an authorised Telethon
session is persisted by :mod:`auth` and reused on every launch.
"""

from __future__ import annotations

import tkinter as tk
import webbrowser
from concurrent.futures import Future
from typing import Callable

import customtkinter as ctk

import auth
import paths

_PAD = {"padx": 24, "pady": 6}


def _humanize(exc: BaseException) -> str:
    name = type(exc).__name__
    text = str(exc)
    if "FloodWait" in name:
        return f"Too many attempts. Wait {getattr(exc, 'seconds', 'a while')}s, then retry."
    if "PhoneCodeInvalid" in name:
        return "That login code is incorrect."
    if "PhoneCodeExpired" in name:
        return "That code expired — send a new one."
    if "PhoneNumberInvalid" in name:
        return "That phone number is not valid."
    if "PasswordHashInvalid" in name:
        return "Incorrect two-step verification password."
    return text or name


class LoginView(ctk.CTkFrame):
    def __init__(self, master, on_done: Callable[[bool], None] | None = None) -> None:
        super().__init__(master)
        self.result = False
        self._on_done = on_done
        self._worker = auth.AsyncWorker()
        self._flow = auth.LoginFlow(self._worker)
        self._phone = ""

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(3, weight=1)

        ctk.CTkLabel(
            self, text="Sign in to Telegram", font=ctk.CTkFont(size=22, weight="bold")
        ).grid(row=1, column=0, pady=(0, 4))

        self._body = ctk.CTkFrame(self, fg_color="transparent")
        self._body.grid(row=2, column=0, sticky="n")
        self._body.grid_columnconfigure(0, weight=1)

        self._status = ctk.CTkLabel(self, text="", wraplength=340)
        self._status.grid(row=4, column=0, pady=(4, 16))

        if auth.credentials_present():
            self._page_phone()
        else:
            self._page_credentials()

    # --- lifecycle -----------------------------------------------------------
    def destroy(self) -> None:  # noqa: D401 - Tk hook
        try:
            self._worker.shutdown()
        except Exception:
            pass
        super().destroy()

    def on_cancel(self) -> None:
        self.result = False
        try:
            self._flow.cancel()
        except Exception:
            pass
        self._settle(False)

    def _finish_ok(self) -> None:
        self.result = True
        self._settle(True)

    def _settle(self, success: bool) -> None:
        callback, self._on_done = self._on_done, None
        if callback is not None:
            # Caller owns teardown (it swaps this frame for the main UI).
            try:
                self._worker.shutdown()
            except Exception:
                pass
            callback(success)
        else:
            self.destroy()  # overlay / wait_window mode

    # --- helpers -----------------------------------------------------------
    def _clear(self) -> None:
        for child in self._body.winfo_children():
            child.destroy()

    def _set_status(self, text: str, *, error: bool = False) -> None:
        color = "#e35d5d" if error else ("gray70", "gray70")
        self._status.configure(text=text, text_color=color)

    def _await(self, future: "Future", handler: Callable[[object], None]) -> None:
        def deliver(fut: "Future") -> None:
            try:
                self.winfo_exists()
            except tk.TclError:
                return

            def apply() -> None:
                try:
                    handler(fut.result())
                except BaseException as exc:  # noqa: BLE001 - surface to user
                    self._enable_inputs()
                    self._set_status(_humanize(exc), error=True)

            try:
                self.after(0, apply)
            except tk.TclError:
                pass

        future.add_done_callback(deliver)

    def _enable_inputs(self, enabled: bool = True) -> None:
        state = "normal" if enabled else "disabled"
        for child in self._body.winfo_children():
            if isinstance(child, (ctk.CTkButton, ctk.CTkEntry)):
                child.configure(state=state)

    # --- pages -----------------------------------------------------------
    def _page_credentials(self) -> None:
        self._clear()
        self._set_status(
            "Create an app at my.telegram.org (API development tools) to get "
            "your API ID and hash. This is stored only on this computer."
        )
        ctk.CTkLabel(self._body, text="API ID").grid(
            row=0, column=0, sticky="w", **_PAD
        )
        api_id = ctk.CTkEntry(self._body, width=300, placeholder_text="1234567")
        api_id.grid(row=1, column=0, **_PAD)
        ctk.CTkLabel(self._body, text="API hash").grid(
            row=2, column=0, sticky="w", **_PAD
        )
        api_hash = ctk.CTkEntry(self._body, width=300, placeholder_text="abcdef0123...")
        api_hash.grid(row=3, column=0, **_PAD)

        link = ctk.CTkButton(
            self._body,
            text="Open my.telegram.org",
            fg_color="transparent",
            border_width=1,
            command=lambda: webbrowser.open("https://my.telegram.org/apps"),
        )
        link.grid(row=4, column=0, **_PAD)

        def submit() -> None:
            raw_id = api_id.get().strip()
            raw_hash = api_hash.get().strip()
            if not raw_id.isdigit() or len(raw_hash) < 8:
                self._set_status(
                    "Enter a numeric API ID and the full API hash.", error=True
                )
                return
            paths.set_env_values({"TG_API_ID": raw_id, "TG_API_HASH": raw_hash})
            self._page_phone()

        ctk.CTkButton(self._body, text="Continue", command=submit).grid(
            row=5, column=0, **_PAD
        )

    def _page_phone(self) -> None:
        self._clear()
        self._set_status(
            "Enter your phone number in international format, e.g. +14155550123."
        )
        entry = ctk.CTkEntry(self._body, width=300, placeholder_text="+14155550123")
        entry.grid(row=0, column=0, **_PAD)
        entry.insert(0, self._phone)
        entry.focus_set()

        def submit() -> None:
            phone = entry.get().strip()
            if len(phone) < 6:
                self._set_status("That phone number looks too short.", error=True)
                return
            self._phone = phone
            self._enable_inputs(False)
            self._set_status("Sending login code…")
            self._await(self._flow.send_code(phone), self._after_send_code)

        btn = ctk.CTkButton(self._body, text="Send code", command=submit)
        btn.grid(row=1, column=0, **_PAD)
        entry.bind("<Return>", lambda _e: submit())

    def _after_send_code(self, outcome: object) -> None:
        if outcome == "authorized":
            self._set_status("Already signed in. Finishing…")
            self._await(self._flow.finalize(), self._after_finalize)
        else:
            self._page_code()

    def _page_code(self) -> None:
        self._clear()
        self._set_status("Enter the code Telegram just sent to your app or SMS.")
        entry = ctk.CTkEntry(self._body, width=300, placeholder_text="12345")
        entry.grid(row=0, column=0, **_PAD)
        entry.focus_set()

        def submit() -> None:
            code = entry.get().strip()
            if not code:
                self._set_status("Enter the login code.", error=True)
                return
            self._enable_inputs(False)
            self._set_status("Verifying…")
            self._await(self._flow.sign_in(code), self._after_sign_in)

        ctk.CTkButton(self._body, text="Verify", command=submit).grid(
            row=1, column=0, **_PAD
        )
        ctk.CTkButton(
            self._body,
            text="Use a different number",
            fg_color="transparent",
            border_width=1,
            command=self._page_phone,
        ).grid(row=2, column=0, **_PAD)
        entry.bind("<Return>", lambda _e: submit())

    def _after_sign_in(self, outcome: object) -> None:
        if outcome == "password_needed":
            self._page_password()
        else:
            self._set_status("Signing in…")
            self._await(self._flow.finalize(), self._after_finalize)

    def _page_password(self) -> None:
        self._clear()
        self._set_status("This account has two-step verification. Enter your password.")
        entry = ctk.CTkEntry(self._body, width=300, show="•")
        entry.grid(row=0, column=0, **_PAD)
        entry.focus_set()

        def submit() -> None:
            pw = entry.get()
            if not pw:
                self._set_status(
                    "Enter your two-step verification password.", error=True
                )
                return
            self._enable_inputs(False)
            self._set_status("Checking password…")
            self._await(self._flow.sign_in_password(pw), self._after_sign_in)

        ctk.CTkButton(self._body, text="Sign in", command=submit).grid(
            row=1, column=0, **_PAD
        )
        entry.bind("<Return>", lambda _e: submit())

    def _after_finalize(self, name: object) -> None:
        self._set_status(f"Signed in as {name}. Opening TelegramTOI…")
        self.after(600, self._finish_ok)


def run_login_overlay(root) -> bool:
    """Float the login view over an already-running window (sidebar re-login).

    Blocks on ``wait_window`` — only safe when ``root`` is already in its
    mainloop. For the pre-main sign-in gate use ``LoginView(root, on_done=...)``
    instead so the window maps normally.
    """
    view = LoginView(root)
    view.place(relx=0, rely=0, relwidth=1, relheight=1)
    root.wait_window(view)
    return view.result
