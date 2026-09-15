# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for Interlock3D.

Build (from this `packaging/` directory, with the project's venv active
and `pip install -e ".[dev]"` already done, which also installs
pyinstaller via requirements below):

    pyinstaller Interlock3D.spec

PyInstaller does not cross-compile -- this must be run separately ON
each target OS to get that OS's executable (Windows -> .exe, macOS ->
.app, Linux -> ELF binary). See packaging/README.md for the full
per-platform build/run instructions and known caveats.
"""

import sys
from pathlib import Path

block_cipher = None

# SPECPATH is injected into this file's globals by PyInstaller: the
# directory containing this .spec file, regardless of the invoking cwd.
PROJECT_ROOT = Path(SPECPATH).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"

# The compensation profile JSON is package data (declared in
# pyproject.toml's [tool.setuptools.package-data] for `pip install`), but
# PyInstaller doesn't read that -- it needs its own explicit `datas` entry
# or the app crashes at startup with FileNotFoundError the first time
# CompensationTable.load() runs (confirmed by actually building and
# running without this and watching it fail this exact way).
datas = [
    (str(SRC_DIR / "interlock3d" / "data" / "compensation_profiles.json"), "interlock3d/data"),
]

icon_path = None
if sys.platform == "darwin":
    candidate = PROJECT_ROOT / "packaging" / "icon.icns"
    icon_path = str(candidate) if candidate.exists() else None
elif sys.platform == "win32":
    candidate = PROJECT_ROOT / "packaging" / "icon.ico"
    icon_path = str(candidate) if candidate.exists() else None

a = Analysis(
    [str(SRC_DIR / "interlock3d" / "main.py")],
    pathex=[str(SRC_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Interlock3D",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_path,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Interlock3D",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Interlock3D.app",
        icon=icon_path,
        bundle_identifier="com.interlock3d.app",
        info_plist={
            "CFBundleShortVersionString": "0.1.0",
            "NSHighResolutionCapable": "True",
        },
    )
