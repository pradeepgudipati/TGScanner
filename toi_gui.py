"""
TOI Telegram Link Finder - Modern Windows GUI
Redesigned with customtkinter for a premium look and feel.
"""
import customtkinter as ctk
import subprocess
import threading
import re
import webbrowser
from pathlib import Path
from datetime import datetime
from tkinter import scrolledtext, font
import tkinter as tk

# Set appearance mode and color theme
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

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
        # Row 7 will be the spacer
        self.sidebar_frame.grid_rowconfigure(7, weight=1)

        self.logo_label = ctk.CTkLabel(self.sidebar_frame, text="TOI Link Finder", font=ctk.CTkFont(size=20, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=20, pady=(20, 10))

        self.search_mode_label = ctk.CTkLabel(self.sidebar_frame, text="Search Mode:", anchor="w")
        self.search_mode_label.grid(row=1, column=0, padx=20, pady=(10, 0))
        self.search_mode_menu = ctk.CTkOptionMenu(self.sidebar_frame, values=["TOI Search", "Magazine Search"])
        self.search_mode_menu.grid(row=2, column=0, padx=20, pady=(5, 10))

        self.ai_label = ctk.CTkLabel(self.sidebar_frame, text="Keywords / AI Query:", anchor="w")
        self.ai_label.grid(row=3, column=0, padx=20, pady=(10, 0))
        self.ai_entry = ctk.CTkEntry(self.sidebar_frame, placeholder_text="e.g. 'Science magazines'")
        self.ai_entry.grid(row=4, column=0, padx=20, pady=(5, 10))
        self.ai_entry.bind("<Key>", self.auto_switch_to_magazine)
        self.ai_entry.bind("<Button-1>", self.auto_switch_to_magazine)

        self.search_button = ctk.CTkButton(self.sidebar_frame, text="Start Search", command=self.start_search)
        self.search_button.grid(row=5, column=0, padx=20, pady=10)

        self.stop_btn = ctk.CTkButton(self.sidebar_frame, text="Stop Search", command=self.stop_search, 
                                      fg_color="transparent", border_width=2, text_color=("gray10", "#DCE4EE"), state="disabled")
        self.stop_btn.grid(row=6, column=0, padx=20, pady=10)

        self.appearance_mode_label = ctk.CTkLabel(self.sidebar_frame, text="Appearance Mode:", anchor="w")
        self.appearance_mode_label.grid(row=8, column=0, padx=20, pady=(10, 0))
        self.appearance_mode_optionemenu = ctk.CTkOptionMenu(self.sidebar_frame, values=["Dark", "Light", "System"],
                                                                       command=self.change_appearance_mode_event)
        self.appearance_mode_optionemenu.grid(row=9, column=0, padx=20, pady=(10, 20))

        # --- Main Content Frame ---
        self.main_frame = ctk.CTkFrame(self.root, corner_radius=0, fg_color="transparent")
        self.main_frame.grid(row=0, column=1, sticky="nsew", padx=20, pady=20)
        self.main_frame.grid_columnconfigure(0, weight=1)
        self.main_frame.grid_rowconfigure(2, weight=1)

        # Status Header
        self.status_frame = ctk.CTkFrame(self.main_frame, fg_color="transparent")
        self.status_frame.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.status_frame.grid_columnconfigure(0, weight=1)

        self.status_label = ctk.CTkLabel(self.status_frame, text="Ready to search", font=ctk.CTkFont(size=14))
        self.status_label.grid(row=0, column=0, sticky="w")

        self.clear_btn = ctk.CTkButton(self.status_frame, text="Clear All", width=100, command=self.clear_output)
        self.clear_btn.grid(row=0, column=1, sticky="e")

        # Discovered Links Frame (Scrollable)
        self.links_frame = ctk.CTkScrollableFrame(self.main_frame, label_text="📎 Discovered Telegram Links", label_font=ctk.CTkFont(weight="bold"))
        self.links_frame.grid(row=1, column=0, sticky="nsew", pady=(0, 20))
        self.links_frame.grid_columnconfigure(0, weight=1)
        self.links_frame.grid_remove() # Hide initially

        # Console Output
        self.console_label = ctk.CTkLabel(self.main_frame, text="Console Output", font=ctk.CTkFont(weight="bold"))
        self.console_label.grid(row=2, column=0, sticky="w", pady=(0, 5))

        # We still use scrolledtext for better performance with large logs, but wrap it in a frame
        self.output_container = ctk.CTkFrame(self.main_frame)
        self.output_container.grid(row=3, column=0, sticky="nsew")
        self.output_container.grid_columnconfigure(0, weight=1)
        self.output_container.grid_rowconfigure(0, weight=1)

        self.output_text = scrolledtext.ScrolledText(self.output_container, wrap=tk.WORD, 
                                                    bg="#1e1e1e", fg="#d4d4d4", 
                                                    insertbackground="white",
                                                    font=("Consolas", 10),
                                                    borderwidth=0, highlightthickness=0)
        self.output_text.grid(row=0, column=0, sticky="nsew", padx=10, pady=10)

        # Configure text tags for clickable links
        self.output_text.tag_config("link", foreground="#3794ff", underline=True)
        self.output_text.tag_bind("link", "<Button-1>", self.open_link)
        self.output_text.tag_bind("link", "<Enter>", lambda e: self.output_text.config(cursor="hand2"))
        self.output_text.tag_bind("link", "<Leave>", lambda e: self.output_text.config(cursor=""))

        # Internal state
        self.discovered_links = []
        self.links = {}
        self.link_counter = 0
        self.stop_search_flag = False
        self.process = None

    def change_appearance_mode_event(self, new_appearance_mode: str):
        ctk.set_appearance_mode(new_appearance_mode)

    def append_output(self, text):
        """Append text to output area and detect links. Add timestamp if missing."""
        if not text:
            return

        # Add timestamp unless line already looks like it has one
        if re.match(r"^\d{4}[-/]", text.strip()):
            line = text.rstrip('\n')
        else:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            line = f"{ts} - {text.rstrip()}"

        link_pattern = r"(https?://t\.me/[\w\-_/]+|tg://[\w\-_/\?=&]+)"

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
            fname = m.group('fname').strip()
            channel = m.group('channel').strip()
            msgid = m.group('msgid').strip()
            link = m.group('link').strip()
            display_key = f"MATCH|{msgid}|{channel}"
            self.root.after(0, self.links_frame.grid)
            if display_key not in [l[0] for l in self.discovered_links if isinstance(l, tuple)]:
                 self.root.after(0, self.add_match_entry, fname, channel, msgid, display_key, link)

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
        
        btn = ctk.CTkButton(self.links_frame, text=link, anchor="w", fg_color="transparent", 
                            text_color="#3794ff", hover_color="#2b2b2b",
                            command=lambda lnk=link: self.open_discovered_link(lnk))
        btn.grid(row=len(self.discovered_links), column=0, sticky="ew", pady=2)

    def add_match_entry(self, fname: str, channel: str, msgid: str, key: str, link: str = ""):
        # Check if already added
        if any(isinstance(l, tuple) and l[0] == key for l in self.discovered_links):
            return
        self.discovered_links.append((key, fname))

        label_text = f"📄 {fname}\n📡 {channel} | ID: {msgid}"
        btn = ctk.CTkButton(self.links_frame, text=label_text, anchor="w", fg_color="#2d2d2d",
                            hover_color="#3d3d3d", text_color="#d4d4d4",
                            command=lambda ch=channel, mid=msgid, lnk=link: self.on_match_click(ch, mid, lnk))
        btn.grid(row=len(self.discovered_links), column=0, sticky="ew", pady=5, padx=5)

    def open_discovered_link(self, link: str):
        try:
            webbrowser.open(link)
            self.status_label.configure(text=f"✓ Opened: {link}")
        except Exception as e:
            self.append_output(f"✗ Failed to open link: {e}")

    def on_match_click(self, channel: str, msgid: str, link: str = ""):
        self.root.clipboard_clear()
        self.root.clipboard_append(f"{channel} msg_id: {msgid}")
        
        status_text = f"✓ Copied info to clipboard"
        
        if link and link != "N/A":
            try:
                webbrowser.open(link)
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
                    webbrowser.open(link)
                break

    def clear_output(self):
        self.output_text.delete(1.0, tk.END)
        self.links.clear()
        self.link_counter = 0
        for child in self.links_frame.winfo_children():
             if not isinstance(child, ctk.CTkLabel): # Keep the header label if any
                child.destroy()
        self.discovered_links.clear()
        self.links_frame.grid_remove()
        self.status_label.configure(text="Ready to search")
        self.stop_btn.configure(state="disabled")
        self.process = None

    def start_search(self):
        self.search_button.configure(state="disabled")
        self.status_label.configure(text="Searching... Please wait")
        self.stop_search_flag = False
        
        thread = threading.Thread(target=self.run_search, daemon=True)
        thread.start()

    def run_search(self):
        try:
            search_mode = self.search_mode_menu.get()
            if search_mode == "Magazine Search":
                script_path = Path(__file__).parent / "find_magazine.py"
                cmd = ["uv", "run", str(script_path)]
                keywords = self.ai_entry.get().strip()
                if keywords:
                    cmd.extend(["--keywords", keywords])
                else:
                    self.root.after(0, self.append_output, "⚠️ Please enter keywords for magazine search.")
                    self.root.after(0, self.search_button.configure, state="normal")
                    return
            else:
                script_path = Path(__file__).parent / "find_toi.py"
                cmd = ["uv", "run", str(script_path)]
                ai_query = self.ai_entry.get().strip()
                if ai_query:
                    cmd.extend(["--ai-query", ai_query])

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                encoding='utf-8',
                errors='replace',
                bufsize=1,
                cwd=script_path.parent
            )

            self.process = process
            self.root.after(0, lambda: self.stop_btn.configure(state="normal"))

            for line in process.stdout:
                if self.stop_search_flag:
                    try:
                        process.terminate()
                    except:
                        pass
                    self.root.after(0, self.append_output, "Search stopped by user.")
                    break
                self.root.after(0, self.append_output, line)

            if not self.stop_search_flag:
                if process.wait() == 0:
                    self.root.after(0, self.status_label.configure, text="✓ Search completed!")
                else:
                    self.root.after(0, self.status_label.configure, text="✗ Search failed")

        except Exception as e:
            self.root.after(0, self.append_output, f"✗ Error: {e}")
        finally:
            self.root.after(0, self.search_button.configure, state="normal")
            self.root.after(0, self.stop_btn.configure, state="disabled")
            self.process = None

    def stop_search(self):
        self.stop_search_flag = True
        if self.process:
            try:
                self.process.terminate()
            except:
                pass
        self.status_label.configure(text="⏸ Search stopped")

    def auto_switch_to_magazine(self, event=None):
        try:
            if hasattr(self, 'search_mode_menu') and self.search_mode_menu.get() != "Magazine Search":
                self.search_mode_menu.set("Magazine Search")
        except Exception:
            pass

def main():
    root = ctk.CTk()
    app = TOIFinderGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
