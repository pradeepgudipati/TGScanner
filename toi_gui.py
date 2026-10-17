"""
TOI Telegram Link Finder - Modern Windows GUI
Redesigned with customtkinter for a premium look and feel.
"""

import os
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext

import customtkinter as ctk

import auth
import login_gui
import paths

# Set appearance mode and color theme
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")


def terminate_process_tree(
    process: subprocess.Popen | None, timeout: float = 5.0
) -> None:
    """Force-stop a subprocess and any children (e.g. `uv run` → python).

    On Windows, Popen.terminate() only signals the direct child. Leaving the
    Python grandchild alive blocks stdout reads and locks the Telethon session,
    which freezes the GUI's ability to start later searches.
    """
    if process is None or process.poll() is not None:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                capture_output=True,
                text=True,
                check=False,
            )
        else:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
    except Exception:
        try:
            process.kill()
        except Exception:
            pass
    try:
        process.wait(timeout=timeout)
    except Exception:
        pass


def build_worker_command(worker: str, args: list[str]) -> tuple[list[str], Path]:
    """Return the command that runs a search worker plus its working directory.

    Frozen builds re-invoke the app executable with a ``--worker`` subcommand
    (there is no ``uv`` or ``.py`` file to run); source checkouts still use
    ``uv run <script>``.
    """
    if getattr(sys, "frozen", False):
        return (
            [sys.executable, "--worker", worker, *args],
            Path(sys.executable).resolve().parent,
        )
    root = Path(__file__).resolve().parent
    script = {"toi": "find_toi.py", "magazine": "find_magazine.py"}[worker]
    return (["uv", "run", str(root / script), *args], root)


def run_worker(worker: str, argv: list[str]) -> int:
    """Entry point for a ``--worker`` subprocess inside a frozen build."""
    if worker == "toi":
        import find_toi

        return find_toi.main(argv)
    if worker == "magazine":
        import asyncio

        import find_magazine

        asyncio.run(find_magazine.main(argv))
        return 0
    print(f"Unknown worker: {worker}", file=sys.stderr)
    return 2


class TOIFinderGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("TOI Telegram Link Finder Pro")
        self.root.geometry("1000x700")

        # Configure grid layout (1x2)
        self.root.grid_columnconfigure(1, weight=1)
        self.root.grid_rowconfigure(0, weight=1)

        # --- Sidebar Frame ---
        self.sidebar_frame = ctk.CTkFrame(self.root, width=200, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
        # Row 8 will be the spacer
        self.sidebar_frame.grid_rowconfigure(8, weight=1)

        # How long to wait between auto-repeat attempts (seconds).
        self.loop_interval = 60.0

        self.logo_label = ctk.CTkLabel(
            self.sidebar_frame,
            text="TOI Link Finder",
            font=ctk.CTkFont(size=20, weight="bold"),
        )
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        self.search_mode_label = ctk.CTkLabel(
            self.sidebar_frame, text="Search Mode:", anchor="w"
        )
        self.search_mode_label.grid(row=1, column=0, padx=20, pady=(10, 0))
        self.search_mode_menu = ctk.CTkOptionMenu(
            self.sidebar_frame, values=["TOI Search", "DC Search", "Magazine Search"]
        )
        self.search_mode_menu.grid(row=2, column=0, padx=20, pady=(5, 10))

        self.ai_label = ctk.CTkLabel(
            self.sidebar_frame, text="Keywords / AI Query:", anchor="w"
        )
        self.ai_label.grid(row=3, column=0, padx=20, pady=(10, 0))
        self.ai_entry = ctk.CTkEntry(
            self.sidebar_frame, placeholder_text="e.g. 'Science magazines'"
        )
        self.ai_entry.grid(row=4, column=0, padx=20, pady=(5, 10))
        self.ai_entry.bind("<Key>", self.auto_switch_to_magazine)
        self.ai_entry.bind("<Button-1>", self.auto_switch_to_magazine)

        self.search_button = ctk.CTkButton(
            self.sidebar_frame, text="Start Search", command=self.start_search
        )
        self.search_button.grid(row=5, column=0, padx=20, pady=10)

        self.stop_btn = ctk.CTkButton(
            self.sidebar_frame,
            text="Stop Search",
            command=self.stop_search,
            fg_color="transparent",
            border_width=2,
            text_color=("gray10", "#DCE4EE"),
            state="disabled",
        )
        self.stop_btn.grid(row=6, column=0, padx=20, pady=10)

        self.loop_var = tk.BooleanVar(value=False)
        self.loop_checkbox = ctk.CTkCheckBox(
            self.sidebar_frame,
            text="Auto-repeat every 1 min\nuntil found 🔊",
            variable=self.loop_var,
        )
        self.loop_checkbox.grid(row=7, column=0, padx=20, pady=(0, 10))

        self.appearance_mode_label = ctk.CTkLabel(
            self.sidebar_frame, text="Appearance Mode:", anchor="w"
        )
        self.appearance_mode_label.grid(row=9, column=0, padx=20, pady=(10, 0))
        self.appearance_mode_optionemenu = ctk.CTkOptionMenu(
            self.sidebar_frame,
            values=["Dark", "Light", "System"],
            command=self.change_appearance_mode_event,
        )
        self.appearance_mode_optionemenu.grid(row=10, column=0, padx=20, pady=(10, 10))

        self.account_button = ctk.CTkButton(
            self.sidebar_frame,
            text="Telegram account",
            fg_color="transparent",
            border_width=1,
            text_color=("gray10", "#DCE4EE"),
            command=self.open_account_menu,
        )
        self.account_button.grid(row=11, column=0, padx=20, pady=(0, 20))

        # --- Main Content Frame ---
        self.main_frame = ctk.CTkFrame(
            self.root, corner_radius=0, fg_color="transparent"
        )
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(2, weight=1)

        # Status Header
        self.status_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.status_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.status_frame.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(
            self.status_frame, text="Ready to search", font=ctk.CTkFont(size=14)
        )
        self.status_label.grid(row=0, column=0, sticky="w")

        self.clear_btn = ctk.CTkButton(
            self.status_frame, text="Clear All", width=100, command=self.clear_output
        )
        self.clear_btn.grid(row=0, column=1, sticky="e")

        # Discovered Links Frame (Scrollable)
        self.links_frame = ctk.CTkScrollableFrame(
            self.main_frame,
            label_text="📎 Discovered Telegram Links",
            label_font=ctk.CTkFont(weight="bold"),
        )
        self.links_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 20))
        self.links_frame.grid_columnconfigure(0, weight=1)
        self.links_frame.grid_remove()  # Hide initially

        # Console Output
        self.console_label = ctk.CTkLabel(
            self.main_frame, text="Console Output", font=ctk.CTkFont(weight="bold")
        )
        self.console_label.grid(row=2, column=0, sticky="w", pady=(0, 5))

        # We still use scrolledtext for better performance with large logs, but wrap it in a frame
        self.output_container = ctk.CTkFrame(self.main_frame)
        self.output_container.grid(row=3, column=0, sticky="nsew")
        self.output_container.grid_columnconfigure(0, weight=1)
        self.output_container.grid_rowconfigure(0, weight=1)

        self.output_text = scrolledtext.ScrolledText(
            self.output_container,
            wrap=tk.WORD,
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="white",
            font=("Consolas", 10),
            borderwidth=0,
            highlightthickness=0,
        )
        self.output_text.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        # Configure text tags for clickable links
        self.output_text.tag_config("link", foreground="#3794ff", underline=True)
        self.output_text.tag_bind("link", "<Button-1>", self.open_link)
        self.output_text.tag_bind(
            "link", "<Enter>", lambda e: self.output_text.config(cursor="hand2")
        )
        self.output_text.tag_bind(
            "link", "<Leave>", lambda e: self.output_text.config(cursor="")
        )

        # Internal state
        self.discovered_links = []
        self.links = {}
        self.link_counter = 0
        self.stop_search_flag = False
        self.process = None
        self._search_generation = 0
        self._search_lock = threading.Lock()

    def change_appearance_mode_event(self, new_appearance_mode: str):
        ctk.set_appearance_mode(new_appearance_mode)

    def open_account_menu(self):
        win = ctk.CTkToplevel(self.root)
        win.title("Telegram account")
        win.geometry("340x210")
        win.transient(self.root)
        win.after(150, win.lift)
        win.after(200, win.grab_set)

        ctk.CTkLabel(
            win,
            text="Manage your Telegram session",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).pack(pady=(24, 12))
        ctk.CTkButton(
            win,
            text="Sign in / switch account",
            command=lambda: (win.destroy(), self._relogin()),
        ).pack(pady=6)
        ctk.CTkButton(
            win,
            text="Sign out",
            fg_color="transparent",
            border_width=1,
            text_color=("gray10", "#DCE4EE"),
            command=lambda: (win.destroy(), self._sign_out()),
        ).pack(pady=6)

    def _relogin(self):
        if login_gui.run_login_overlay(self.root):
            self.status_label.configure(text="✓ Telegram sign-in updated")

    def _sign_out(self):
        if not messagebox.askyesno(
            "Sign out",
            "Delete the stored Telegram session? You will need to log in "
            "again the next time TelegramTOI starts.",
        ):
            return
        auth.sign_out()
        messagebox.showinfo(
            "Signed out",
            "Session removed. TelegramTOI will now close — reopen it to log in.",
        )
        self.root.destroy()

    def append_output(self, text):
        """Append text to output area and detect links. Add timestamp if missing."""
        if not text:
            return

        # Add timestamp unless line already looks like it has one
        if re.match(r"^\d{4}[-/]", text.strip()):
            line = text.rstrip("\n")
        else:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            line = f"{ts} - {text.rstrip()}"

        link_pattern = r"(https?://t\.me/[\w\-_/]+|tg://[^\s]+)"

        # Detect any links
        links_found = re.findall(link_pattern, line)
        if links_found:
            self.root.after(0, self.links_frame.grid)
            for link in links_found:
                clean = link.rstrip('.,)"')
                self.root.after(0, self.add_discovered_link, clean)

        # Detect [MATCH] lines
        match_pattern = re.compile(
            r"\[MATCH\]\s*(?P<fname>.*?)\s*\|\s*Channel:\s*(?P<channel>.*?)(?:\s*\|\s*Size:\s*(?P<size>[\d\.]+\s*MB))?\s*\|\s*msg_id:\s*(?P<msgid>\d+)\s*\|\s*Link:\s*(?P<link>\S+)",
            re.IGNORECASE,
        )
        m = match_pattern.search(line)
        if m:
            fname = m.group("fname").strip()
            channel = m.group("channel").strip()
            msgid = m.group("msgid").strip()
            link = m.group("link").strip()
            display_key = f"MATCH|{msgid}|{channel}"
            self.root.after(0, self.links_frame.grid)
            if display_key not in [
                entry[0] for entry in self.discovered_links if isinstance(entry, tuple)
            ]:
                self.root.after(
                    0, self.add_match_entry, fname, channel, msgid, display_key, link
                )

        # Insert into text area
        parts = re.split(link_pattern, line)
        for part in parts:
            if re.match(link_pattern, part):
                link_id = f"link_{self.link_counter}"
                self.link_counter += 1
                self.links[link_id] = part
                start_idx = self.output_text.index(tk.INSERT)
                self.output_text.insert(tk.INSERT, part)
                end_idx = self.output_text.index(tk.INSERT)
                self.output_text.tag_add("link", start_idx, end_idx)
                self.output_text.tag_add(link_id, start_idx, end_idx)
            else:
                self.output_text.insert(tk.INSERT, part)

        self.output_text.insert(tk.INSERT, "\n")
        self.output_text.see(tk.END)

    def add_discovered_link(self, link: str):
        if link in self.discovered_links:
            return
        self.discovered_links.append(link)

        btn = ctk.CTkButton(
            self.links_frame,
            text=link,
            anchor="w",
            fg_color="transparent",
            text_color="#3794ff",
            hover_color="#2b2b2b",
            command=lambda lnk=link: self.open_discovered_link(lnk),
        )
        btn.grid(row=len(self.discovered_links), column=0, sticky="ew", pady=2)

    def add_match_entry(
        self, fname: str, channel: str, msgid: str, key: str, link: str = ""
    ):
        # Check if already added
        if any(
            isinstance(entry, tuple) and entry[0] == key
            for entry in self.discovered_links
        ):
            return
        self.discovered_links.append((key, fname))

        label_text = f"📄 {fname}\n📡 {channel} | ID: {msgid}"
        btn = ctk.CTkButton(
            self.links_frame,
            text=label_text,
            anchor="w",
            fg_color="#2d2d2d",
            hover_color="#3d3d3d",
            text_color="#d4d4d4",
            command=lambda ch=channel, mid=msgid, lnk=link: self.on_match_click(
                ch, mid, lnk
            ),
        )
        btn.grid(row=len(self.discovered_links), column=0, sticky="ew", pady=5, padx=5)

    def open_discovered_link(self, link: str):
        try:
            self._open_telegram_link(link)
            self.status_label.configure(text=f"✓ Opened: {link}")
        except Exception as e:
            self.append_output(f"✗ Failed to open link: {e}")

    def on_match_click(self, channel: str, msgid: str, link: str = ""):
        self.root.clipboard_clear()
        self.root.clipboard_append(f"{channel} msg_id: {msgid}")

        status_text = "✓ Copied info to clipboard"

        if link and link != "N/A":
            try:
                self._open_telegram_link(link)
                status_text += " | Opening Telegram..."
            except Exception as e:
                self.append_output(f"✗ Failed to open deep link: {e}")

        self.status_label.configure(text=status_text)

    def open_link(self, event):
        index = self.output_text.index(f"@{event.x},{event.y}")
        tags = self.output_text.tag_names(index)
        for tag in tags:
            if tag.startswith("link_"):
                link = self.links.get(tag)
                if link:
                    try:
                        self._open_telegram_link(link)
                    except Exception as e:
                        self.append_output(f"✗ Failed to open link: {e}")
                break

    @staticmethod
    def _open_telegram_link(link: str) -> None:
        """Open t.me / tg:// links; prefer OS handler so Desktop Telegram receives them."""
        if sys.platform == "win32":
            os.startfile(link)  # type: ignore[attr-defined]
        else:
            webbrowser.open(link)

    def clear_output(self):
        self.output_text.delete(1.0, tk.END)
        self.links.clear()
        self.link_counter = 0
        for child in self.links_frame.winfo_children():
            if not isinstance(child, ctk.CTkLabel):  # Keep the header label if any
                child.destroy()
        self.discovered_links.clear()
        self.links_frame.grid_remove()
        self.status_label.configure(text="Ready to search")
        self.stop_btn.configure(state="disabled")
        # Do not clear self.process here — an active search may still own it.

    @staticmethod
    def play_alert_sound(repeats: int = 3) -> None:
        """Ring an audible alert (Windows beep + terminal bell fallback)."""
        try:
            if sys.platform == "win32":
                try:
                    import winsound

                    for _ in range(max(1, repeats)):
                        winsound.Beep(880, 500)
                        winsound.Beep(660, 500)
                    return
                except Exception:
                    pass
            # Fallback: console bell (\a prints in the log, bell in UI).
            print("\a", flush=True)
        except Exception:
            pass

    def _notify_found(self) -> None:
        """UI-thread alert: bell + status. Winsound already played in worker."""
        try:
            self.root.bell()
        except Exception:
            pass
        self.status_label.configure(text="✓ Found! Search complete — stopping.")

    def _wait_interruptible(self, seconds: float, generation: int) -> bool:
        """Sleep in 1s steps; return False if the search was stopped."""
        remaining = int(seconds)
        for left in range(remaining, 0, -1):
            if self.stop_search_flag or generation != self._search_generation:
                return False
            self.root.after(
                0,
                self.status_label.configure,
                {"text": f"⏳ No match — retrying in {left}s... (Stop to cancel)"},
            )
            time.sleep(1)
        return not self.stop_search_flag and generation == self._search_generation

    def start_search(self):
        with self._search_lock:
            if self.process is not None and self.process.poll() is None:
                self.append_output(
                    "⚠️ A search is already running. Stop it before starting another."
                )
                return
            self._search_generation += 1
            generation = self._search_generation
            self.stop_search_flag = False
            # Read the checkbox in the UI thread (tk vars aren't thread-safe).
            try:
                loop_enabled = bool(self.loop_var.get())
            except Exception:
                loop_enabled = False
            self._loop_requested = loop_enabled

        if loop_enabled:
            self.append_output(
                "🔁 Auto-repeat ON: will retry every 1 min until a file is found."
            )
        self.search_button.configure(state="disabled")
        self.status_label.configure(text="Searching... Please wait")

        thread = threading.Thread(
            target=self.run_search, args=(generation,), daemon=True
        )
        thread.start()

    def _run_single_attempt(self, generation: int, cmd, work_dir) -> bool:
        """Run one worker subprocess. Returns True when file(s) were found."""
        process = None
        found = False
        try:
            popen_kwargs = {
                "stdout": subprocess.PIPE,
                "stderr": subprocess.STDOUT,
                "encoding": "utf-8",
                "errors": "replace",
                "bufsize": 1,
                "cwd": str(work_dir),
            }
            if sys.platform == "win32":
                flags = subprocess.CREATE_NEW_PROCESS_GROUP
                if getattr(sys, "frozen", False):
                    flags |= subprocess.CREATE_NO_WINDOW
                popen_kwargs["creationflags"] = flags

            process = subprocess.Popen(cmd, **popen_kwargs)

            with self._search_lock:
                if generation != self._search_generation or self.stop_search_flag:
                    terminate_process_tree(process)
                    return False
                self.process = process

            self.root.after(0, lambda: self.stop_btn.configure(state="normal"))

            assert process.stdout is not None
            for line in process.stdout:
                if self.stop_search_flag or generation != self._search_generation:
                    terminate_process_tree(process)
                    self.root.after(0, self.append_output, "Search stopped by user.")
                    return False
                if "[MATCH]" in line:
                    found = True
                self.root.after(0, self.append_output, line)

            process.wait()
            return found
        finally:
            if process is not None and process.poll() is None:
                terminate_process_tree(process)
            with self._search_lock:
                if self.process is process:
                    self.process = None

    def run_search(self, generation: int):
        try:
            search_mode = self.search_mode_menu.get()
            if search_mode == "Magazine Search":
                worker = "magazine"
                worker_args: list[str] = []
                keywords = self.ai_entry.get().strip()
                if keywords:
                    worker_args.extend(["--keywords", keywords])
                else:
                    self.root.after(
                        0,
                        self.append_output,
                        "⚠️ Please enter keywords for magazine search.",
                    )
                    return
            else:
                worker = "toi"
                worker_args = []
                if search_mode == "DC Search":
                    worker_args.extend(["--newspaper", "dc"])
                ai_query = self.ai_entry.get().strip()
                if ai_query:
                    worker_args.extend(["--ai-query", ai_query])

            cmd, work_dir = build_worker_command(worker, worker_args)
            loop_enabled = bool(getattr(self, "_loop_requested", False))
            interval = float(getattr(self, "loop_interval", 60.0))

            attempt = 0
            while True:
                if self.stop_search_flag or generation != self._search_generation:
                    break
                attempt += 1
                if attempt > 1:
                    self.root.after(
                        0,
                        self.append_output,
                        f"\n🔁 Auto-repeat attempt {attempt} — searching again...",
                    )
                self.root.after(
                    0,
                    self.status_label.configure,
                    {
                        "text": f"Searching... (attempt {attempt})"
                        + (" — auto-repeat ON" if loop_enabled else "")
                    },
                )

                found = self._run_single_attempt(generation, cmd, work_dir)

                if self.stop_search_flag or generation != self._search_generation:
                    break

                if found:
                    self.root.after(
                        0, self.append_output, "✅ File found — search complete!"
                    )
                    self.play_alert_sound()
                    self.root.after(0, self._notify_found)
                    break

                # No match in this attempt.
                if not loop_enabled:
                    self.root.after(
                        0, self.status_label.configure, text="✗ No matches found"
                    )
                    break

                self.root.after(
                    0,
                    self.append_output,
                    f"⏳ Attempt {attempt}: no match — waiting 1 min before retry...",
                )
                if not self._wait_interruptible(interval, generation):
                    break
                # ... otherwise loop and run the next attempt.

        except Exception as e:
            self.root.after(0, self.append_output, f"✗ Error: {e}")
        finally:
            with self._search_lock:
                self.process = None
            if generation == self._search_generation:
                self.root.after(0, self.search_button.configure, state="normal")
                self.root.after(0, self.stop_btn.configure, state="disabled")
                if self.stop_search_flag:
                    self.root.after(
                        0, self.status_label.configure, text="⏸ Search stopped"
                    )

    def stop_search(self):
        self.stop_search_flag = True
        with self._search_lock:
            process = self.process
        terminate_process_tree(process)
        self.status_label.configure(text="⏸ Stopping search...")

    def auto_switch_to_magazine(self, event=None):
        try:
            if (
                hasattr(self, "search_mode_menu")
                and self.search_mode_menu.get() == "TOI Search"
            ):
                self.search_mode_menu.set("Magazine Search")
        except Exception:
            pass


def main():
    if len(sys.argv) >= 3 and sys.argv[1] == "--worker":
        sys.exit(run_worker(sys.argv[2], sys.argv[3:]))

    paths.load_env()

    root = ctk.CTk()

    def start_main_ui() -> None:
        for child in root.winfo_children():
            child.destroy()
        root.title("TOI Telegram Link Finder Pro")
        root.geometry("1000x700")
        TOIFinderGUI(root)

    if auth.session_present():
        start_main_ui()
    else:
        root.title("Sign in — TelegramTOI")
        root.geometry("460x560")

        def on_login_done(success: bool) -> None:
            if success:
                start_main_ui()
            else:
                root.destroy()

        root.protocol("WM_DELETE_WINDOW", lambda: on_login_done(False))
        login_gui.LoginView(root, on_done=on_login_done).pack(fill="both", expand=True)

    root.mainloop()


if __name__ == "__main__":
    main()
