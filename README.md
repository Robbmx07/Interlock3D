# Interlock3D

Desktop tool for FDM 3D-printing hobbyists: open a multi-part STL assembly,
detect the features meant to mate (holes, pegs, bosses, flat faces), confirm
pairings, apply printer/material-appropriate dimensional compensation, and
export a corrected, ready-to-print STL. Fully offline, one-time purchase.

This repo is being built in phases — see `PROGRESS.md` for current status.

## Phase 1 status: scaffold + load & view STL

What works right now: pick one or more STL files, they load via `trimesh`
and render in an interactive, embedded PyVista 3D view (rotate/pan/zoom),
each part in a distinct color. No feature detection yet — that's Phase 2.

## Project layout

```
src/interlock3d/
  main.py            # entry point
  core/
    mesh_loader.py    # STL loading (trimesh) + conversion to PyVista
  gui/
    main_window.py    # main window: toolbar, part list, viewer
    viewer.py          # embedded PyVista QtInteractor widget
scripts/
  make_sample_stl.py  # generates a couple of throwaway STLs for smoke-testing
test_data/            # sample STLs (generated, not the Phase 2 known-dimension set)
```

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e .
```

## Running the app

```bash
source .venv/bin/activate
interlock3d
# or: python -m interlock3d.main
```

Use the **Open STL(s)...** toolbar button to pick one or more `.stl` files.
Each loaded part appears in the left-hand list and in the 3D view with a
distinct color. **Reset View** re-fits the camera to everything loaded;
**Clear** removes all loaded parts.

Don't have an STL handy? Generate two small sample files (a plate and a
cylindrical boss) to try it out:

```bash
python scripts/make_sample_stl.py
```

This writes `test_data/sample_plate.stl` and `test_data/sample_boss.stl`.

### Note for headless Linux (CI / containers without a desktop)

The GUI needs a small set of system X11/EGL libraries even to run under
Xvfb (`libegl1`, `libxkbcommon-x11-0`, `libxcb-cursor0`, `libxcb-icccm4`,
`libxcb-keysyms1`, `libxcb-shape0`, and friends). A normal Windows, Mac, or
desktop Linux install already has the equivalent system libraries, so this
is only relevant if you're running this in a minimal container.

## Validation performed for Phase 1

Ran the app end-to-end under Xvfb (virtual display): loaded both sample
STLs through the real GUI code path (`MainWindow._load_and_add`), confirmed
both parts registered in the part list and viewer, and confirmed the
rendered screenshot showed both parts correctly shaped, positioned, and
distinctly colored (see session notes / `PROGRESS.md`).

## Roadmap

See the phase list in `PROGRESS.md`. Phase 2 (feature detection) is next,
pending go-ahead.
