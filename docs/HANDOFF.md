# Handoff — standalone desktop app + releases

_Last updated: 2026-09-02. Branch: `main` @ `6ed7d83`._

## TL;DR of where things stand

- **TGScanner is now a double-clickable desktop app.** No terminal, no `uv run`.
  The `customtkinter` GUI ([toi_gui.py](../toi_gui.py)) is packaged with PyInstaller.
- **In-app Telegram login works** (phone → code → 2FA). Session is stored per-user
  and reused on every launch. Verified end-to-end on Windows.
- **Release [v0.1.0](https://github.com/pradeepgudipati/TGScanner/releases/tag/v0.1.0)
  is published** with 3 assets:
  - `TelegramTOI-windows-x64.zip`
  - `TelegramTOI-linux-x64.tar.gz`
  - `TelegramTOI-macos-arm64.zip` (Apple Silicon)
- **Intel macOS build is the one open item** — see [Pick up here](#pick-up-here).

## What changed (commits `d439acb` → `6ed7d83`)

| Area | File(s) | Notes |
|------|---------|-------|
| Path resolution | [paths.py](../paths.py) *(new)* | Single source of truth for `.env`, session, logs, `outputs/`, `.cache/`. Source runs unchanged (repo root). Frozen runs use a per-user config dir. `TELEGRAMTOI_HOME` overrides. |
| Auth plumbing | [auth.py](../auth.py) *(new)* | `session_present()` (offline check), `AsyncWorker` (bg asyncio loop), `LoginFlow` (multi-step sign-in), `sign_out()`, `harden_session_permissions()` (icacls on Win, chmod 0600 on POSIX). |
| Login UI | [login_gui.py](../login_gui.py) *(new)* | `LoginView` frame with credential / phone / code / 2FA pages. `LoginView(root, on_done=cb)` for the pre-main gate; `run_login_overlay(root)` for the sidebar re-login. |
| Main GUI | [toi_gui.py](../toi_gui.py) | `main()` gates on `auth.session_present()`; "Telegram account" sidebar button (switch account / sign out). Frozen builds re-invoke the exe as `TelegramTOI --worker toi|magazine …` instead of `uv run` (see `build_worker_command` / `run_worker`). |
| Search scripts | [find_toi.py](../find_toi.py), [find_magazine.py](../find_magazine.py) | All CWD-relative paths now go through `paths`. `find_magazine.main()` takes an optional `argv`. |
| Packaging | [TelegramTOI.spec](../TelegramTOI.spec) *(new)* | PyInstaller **onedir**, windowed. Bundles `customtkinter` + `langdetect` data and `telethon` submodules. `BUNDLE` → `TelegramTOI.app` on macOS. No icon set (`icon=None`). |
| Config | [pyproject.toml](../pyproject.toml) | Added `[dependency-groups] dev = ["pyinstaller>=6.11.1"]`. |
| CI | [.github/workflows/release.yml](../.github/workflows/release.yml) *(new)* | Build Win + Linux + macOS(arm64) on `v*` tag push or manual dispatch; publish/attach via `softprops/action-gh-release@v2`. |
| Docs | [README.md](../README.md) | "Building a standalone app" + "Telegram login" + config-dir table. |

## Config / data location (packaged app)

| OS | Path |
|----|------|
| Windows | `%APPDATA%\TelegramTOI` |
| macOS | `~/Library/Application Support/TelegramTOI` |
| Linux | `${XDG_CONFIG_HOME:-~/.config}/TelegramTOI` |

Holds `.env` (seeded from bundled `.env.sample` on first run), `toi_session.session`
(ACL-locked to the user), `logs/`, `outputs/`, `.cache/`.

## Build locally

```bash
uv sync
uv run pyinstaller --noconfirm TelegramTOI.spec
# -> dist/TelegramTOI/  (folder app; TelegramTOI.app on macOS)
```

PyInstaller does **not** cross-compile — build on each target OS.

## How releases work

```bash
git tag v0.1.1 && git push origin v0.1.1
```

That triggers `.github/workflows/release.yml`, which builds all matrix legs and
creates/updates the GitHub Release for the tag with the archives attached.
Manual re-run without moving a tag: **Actions → Release → Run workflow**, enter
the existing tag (e.g. `v0.1.0`). `action-gh-release` updates the release in place.

> The `v0.1.0` git tag could not be force-moved from this environment, so the
> macOS/Intel iterations were done via manual `workflow_dispatch` with
> `tag=v0.1.0`. That's the pattern to reuse for patching an existing release.

## Pick up here

### 1. Intel macOS (`x86_64`) build — the open item

GitHub-hosted `macos-13` (Intel) runners **never picked up the job** across two
attempts (~1.5 h queued each) — they're being retired platform-wide. The matrix
leg is **commented out** in `release.yml`.

Options, easiest first:

- **Build it on your Mac** (only produces `x86_64` if the Mac is Intel, or if you
  set up an `x86_64` Python + universal deps):
  ```bash
  git pull
  uv sync && uv run pyinstaller --noconfirm TelegramTOI.spec
  ditto -c -k --keepParent dist/TelegramTOI.app TelegramTOI-macos-x64.zip
  gh release upload v0.1.0 TelegramTOI-macos-x64.zip
  ```
- **Retry CI later** — uncomment the `- os: macos-13` matrix entry and the Intel
  row in the release-notes table in `release.yml`, then dispatch. If runners are
  available it "just works".
- **Drop Intel entirely** — Apple Silicon covers every Mac since late 2020.
  Decide and delete the commented block.

### 2. Minor cleanups (optional, non-blocking)

- **`cryptg`** — CI logs show `libssl not found, falling back to (slower) Python
  encryption`. `uv add cryptg` speeds up Telethon's MTProto crypto.
- **`tzdata`** — `uv add tzdata` silences the PyInstaller `Hidden import "tzdata"
  not found` warning (harmless; code only uses `timezone.utc`).
- **`softprops/action-gh-release@v2`** still targets Node 20 (CI warns). No Node 24
  major yet; revisit when one ships.
- **App icon** — `TelegramTOI.spec` has `icon=None`. Add `.ico` / `.icns` when there
  is artwork.
- **Signing / notarization** — apps ship unsigned. macOS needs right-click → Open
  (or `xattr -dr com.apple.quarantine`), Windows SmartScreen needs "Run anyway".
  The release notes already say this.

### 3. Pre-existing debt (not introduced by this work, but noticed)

- `python check.py` fails on **`isort`** for `test_openai_compat.py`,
  `test_toi_matcher.py`, `test_find_magazine_recency.py` and on **`black`** for
  `find_magazine.py` — all failing on a clean checkout before these changes too.
  The new/touched files (`paths.py`, `auth.py`, `login_gui.py`, `toi_gui.py`,
  `find_toi.py`) are `ruff` + `isort` + `black` clean.
- `api_server.py` and `n8n/` are **untracked, unrelated WIP** — left alone, not
  part of any commit here.

### 4. Not done / possible follow-ups

- Only `TG_API_ID` / `TG_API_HASH` are editable in the GUI (via the login screen).
  `OPENAI_MODEL` / `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `MAGAZINE_MAX_AGE_DAYS`
  still require hand-editing `.env` in the config dir. A settings panel would close
  that gap.
- `launch_toi_gui.bat` still points at an old hardcoded path (`C:\Dev\Personal\…`)
  and `uv run` — now redundant for end users; keep only for dev.
- No auto-update mechanism.

## Testing notes

- Verified on Windows: sign-in flow (phone/code/2FA), session reuse on relaunch,
  sign-out, and a real search run — from both source and the frozen build.
- **Gotcha that bit us:** the login screen originally ran `root.wait_window()`
  *before* `mainloop()`. customtkinter keeps its root withdrawn until the mainloop
  starts, so the window existed (`TkTopLevel`) but stayed invisible — the process
  ran with no visible UI. Fixed by making `main()` always enter `mainloop()` and
  swapping the `LoginView` frame for `TOIFinderGUI` via an `on_done` callback.
  The overlay path (`run_login_overlay`) can still use `wait_window` because the
  root is already mapped there.
- Headless render check: constructing `LoginView` and cycling all four pages
  raises nothing (`uv run python` with a withdrawn root).
