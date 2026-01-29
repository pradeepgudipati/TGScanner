# TGScanner

A powerful Telegram scanning tool that finds newspapers (TOI) and magazines using Gemini AI.

## Features
- **TOI Search**: Find "Times of India" Hyderabad edition links.
- **Magazine Search**: Find any English magazine by keywords using Gemini AI for smart classification.
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
     GOOGLE_API_KEY=your_gemini_api_key
     ```
3. **Install Dependencies**:
   ```bash
   uv sync
   ```
4. **Telegram Session**:
   Ensure you have a valid `toi_session.session` file in the root. (Log in through Telethon if needed).

## Running the App

Simply run the batch file:
```powershell
.\launch_toi_gui.bat
```

Or run via python directly:
```bash
uv run toi_gui.py
```

## How to use Magazine Search
1. Type keywords (e.g., 'National Geographic' or 'finance') in the **Keywords / AI Query** field.
2. The search mode will automatically switch to **Magazine Search**.
3. Click **Start Search**.
4. Click on any result in the sidebar to copy the link and open it in Telegram.
