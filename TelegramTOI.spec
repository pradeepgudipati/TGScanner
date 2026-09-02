# PyInstaller build spec for the TelegramTOI desktop app.
#   uv run pyinstaller TelegramTOI.spec
# Produces dist/TelegramTOI/ (a folder app; TelegramTOI.app on macOS).
# PyInstaller does not cross-compile — build on each target OS (or in CI).

import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

datas = [(".env.sample", ".")]
datas += collect_data_files("customtkinter")
datas += collect_data_files("langdetect")

hiddenimports = collect_submodules("telethon")

a = Analysis(
    ["toi_gui.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["black", "isort", "ruff", "pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TelegramTOI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TelegramTOI",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="TelegramTOI.app",
        icon=None,
        bundle_identifier="com.telegramtoi.app",
        info_plist={"NSHighResolutionCapable": True},
    )
