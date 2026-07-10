# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules


datas = [("bili_recipe_notes/ui.py", "bili_recipe_notes")]
datas.extend(collect_data_files("streamlit"))

hiddenimports = []
for package in ("bili_recipe_notes", "streamlit", "yt_dlp", "faster_whisper", "reportlab", "docx"):
    hiddenimports.extend(collect_submodules(package))

a = Analysis(
    ["bili_recipe_notes/ui_launcher.py"],
    pathex=["."],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="BiliRecipeNotesUI",
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
)
