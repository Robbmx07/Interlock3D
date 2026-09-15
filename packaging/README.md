# Building a standalone Interlock3D executable

Uses [PyInstaller](https://pyinstaller.org) via `Interlock3D.spec` in this
directory.

## Important: this does not cross-compile

PyInstaller bundles the interpreter and libraries for **the OS you run it
on** — it cannot build a Windows `.exe` from Linux/Mac, or a Mac `.app`
from Windows/Linux. To get a Windows build, run these steps on Windows;
for a Mac build, run them on a Mac.

## Build steps (same on every OS)

```bash
# From the project root, with a fresh venv:
python3 -m venv .venv
# Windows: .venv\Scripts\activate      Mac/Linux: source .venv/bin/activate
pip install -e ".[dev]"
pip install pyinstaller

cd packaging
pyinstaller Interlock3D.spec
```

Output:
- **Windows**: `packaging/dist/Interlock3D/Interlock3D.exe` (plus a
  `_internal/` folder of dependencies next to it — distribute the whole
  `Interlock3D/` folder, or zip it).
- **macOS**: `packaging/dist/Interlock3D.app`, a normal double-clickable
  app bundle.
- **Linux**: `packaging/dist/Interlock3D/Interlock3D` (same
  folder-plus-`_internal/` layout as Windows).

The build takes a few minutes and produces a large output (VTK alone is
several hundred MB) — expect roughly 800MB-1GB unpacked. This is normal
for a PyVista/VTK application; there's no straightforward way to shrink
it substantially without dropping rendering capability.

## Validated so far

Built and run on Linux (this dev environment) under Xvfb:
- App launches with the full toolbar (Material picker, Export,
  Calibrate Printer, Clear Calibration, calibration status label) intact.
- **Loaded a real STL through the actual file dialog** (driven via real
  X11 input events, not a test hook) and confirmed the mesh renders
  correctly with feature overlays (hole/peg colors, status bar reporting
  detected feature count) — this specifically exercises VTK's shader
  and rendering-resource loading from inside the frozen bundle, a
  common PyInstaller+VTK trouble spot, and it worked cleanly.
- First build attempt (a plain `pyinstaller main.py` with no spec file)
  crashed on startup with `FileNotFoundError` for
  `compensation_profiles.json` — PyInstaller doesn't read
  `pyproject.toml`'s `package-data` declaration, so the JSON data file
  needs its own explicit entry, which is why this directory has a
  version-controlled `.spec` file (with that entry already correct)
  rather than relying on a plain PyInstaller command.

**Not yet validated on Windows or macOS** — needs testing on those
platforms directly, since this dev environment is Linux-only.

## If you hit issues

- **Blank/black 3D viewport, or a crash right at startup on Windows**:
  temporarily edit `Interlock3D.spec` and set `console=True` in the
  `EXE(...)` block, rebuild, and run from a terminal/Command Prompt
  instead of double-clicking — with `console=False` (the shipped
  default, so users don't see a terminal window) any startup error is
  otherwise invisible. Once you've fixed whatever it was, set
  `console=False` again for the real build.
- **Antivirus/SmartScreen flags the .exe**: expected for an unsigned
  PyInstaller build; code-signing is a separate step (a certificate),
  not something this repo sets up.
- **macOS "app is damaged" / Gatekeeper block**: also expected for an
  unsigned/unnotarized build — either right-click → Open the first time,
  or (for real distribution) sign and notarize with an Apple Developer
  account, again outside this repo's scope.

## Icon (optional)

Not included — add `packaging/icon.ico` (Windows) and/or
`packaging/icon.icns` (macOS) and the spec file will pick them up
automatically (see the `icon_path` logic near the top of
`Interlock3D.spec`).
