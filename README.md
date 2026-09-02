# TGScanner

A powerful Telegram scanning tool that finds newspapers (TOI/DC) and magazines using an OpenAI-compatible AI API.

## Features
- **TOI Search**: Find "Times of India" Hyderabad edition links.
- **DC Search**: Find "Deccan Chronicle" Hyderabad edition links.
- **Magazine Search**: Find any English magazine by keywords using AI for smart classification.
- **Deep Links**: Results include direct clickable links to Telegram messages.
- **Premium GUI**: Built with `customtkinter` for a modern look.

## Setup

1. **Install UV**: If you don't have it, install [uv](https://github.com/astral-sh/uv).
2. **Setup Environment**:
   - Create a `.env` file in the root directory.
   - Add your credentials:
     ```env
     TG_API_ID=your_api_id
     TG_API_HASH=your_api_hash
     OPENAI_MODEL=gpt-4o-mini
     OPENAI_API_KEY=your_openai_api_key
     MAGAZINE_MAX_AGE_DAYS=90
     ```
   - For any OpenAI-compatible server (Ollama, LM Studio, vLLM, OpenRouter, etc.), set a base URL. `OPENAI_API_KEY` is optional for local servers that ignore auth:
     ```env
     OPENAI_BASE_URL=http://127.0.0.1:11434/v1
     OPENAI_MODEL=llama3.2
     # OPENAI_API_KEY can be omitted; a placeholder is used automatically
     ```
   - `OPENAI_MODEL` is required whenever AI features run (magazine evaluation or TOI `--ai-query`).
3. **Install Dependencies**:
   ```bash
   uv sync
   ```
4. **Telegram login**:
   Just launch the app. On first run it shows a sign-in screen — enter your
   API ID/hash (from my.telegram.org), then your phone number, the login code
   Telegram sends you, and a two-step password if your account has one. The
   authorised session is saved and reused on every later launch. Use the
   **Telegram account** button in the sidebar to sign out or switch accounts.

   A pre-existing `toi_session.session` in the project root (or config dir) is
   picked up automatically and skips the sign-in screen.

## Running the App

Simply run the batch file:
```powershell
.\launch_toi_gui.bat
```

Or run via python directly:
```bash
uv run toi_gui.py
```

## Building a standalone desktop app

Package the GUI into a double-clickable app so no terminal / `uv run` is needed:

```bash
uv sync
uv run pyinstaller TelegramTOI.spec
```

Output lands in `dist/TelegramTOI/` (a folder app; `TelegramTOI.app` on macOS).
PyInstaller does **not** cross-compile — run the command on each OS you want a
build for (Windows `.exe`, macOS `.app`, Linux binary).

### Config location for the packaged app

The source checkout keeps using the repo root for `.env`, `toi_session.session`,
logs and `outputs/`. The **packaged app** instead uses a per-user directory:

| OS      | Path                                               |
|---------|----------------------------------------------------|
| Windows | `%APPDATA%\TelegramTOI`                             |
| macOS   | `~/Library/Application Support/TelegramTOI`         |
| Linux   | `${XDG_CONFIG_HOME:-~/.config}/TelegramTOI`         |

The sign-in screen writes `.env` and `toi_session.session` there for you; the
session file's permissions are tightened to your user account. Set
`TELEGRAMTOI_HOME` to override the location.

## How to use Magazine Search
1. Type keywords (e.g., 'National Geographic' or 'finance') in the **Keywords / AI Query** field.
2. The search mode will automatically switch to **Magazine Search**.
3. Click **Start Search**.
4. Click on any result in the sidebar to copy the link and open it in Telegram.
