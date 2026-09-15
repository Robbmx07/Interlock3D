# Interlock3D

Desktop tool for FDM 3D-printing hobbyists: open a multi-part STL assembly,
detect the features meant to mate (holes, pegs, bosses, flat faces), confirm
pairings, apply printer/material-appropriate dimensional compensation, and
export a corrected, ready-to-print STL. Fully offline, one-time purchase.

This repo is being built in phases — see `PROGRESS.md` for current status.

## Status

- **Phase 1** (scaffold + load & view STL): done. Pick one or more STL
  files, they load via `trimesh` and render in an interactive, embedded
  PyVista 3D view (rotate/pan/zoom), each part in a distinct color.
- **Phase 2** (feature detection engine): done, backend only — not wired
  into the GUI yet (that's Phase 3). Given a mesh, `detect_features()`
  finds candidate holes, pegs/bosses, and flat mating faces. See
  `PROGRESS.md` for the numeric validation results.

## Project layout

```
src/interlock3d/
  main.py                 # entry point
  core/
    mesh_loader.py         # STL loading (trimesh) + conversion to PyVista
    segmentation.py         # mesh -> smooth-surface patches
    feature_detection.py    # patches -> Feature list (holes/pegs/flats)
    features.py              # Feature dataclass
  gui/
    main_window.py         # main window: toolbar, part list, viewer
    viewer.py                # embedded PyVista QtInteractor widget
scripts/
  make_sample_stl.py      # throwaway STLs for Phase 1 smoke-testing
  validate_phase2.py       # numeric accuracy report for feature detection
tests/
  known_parts.py           # hand-made, known-dimension test STL generator
  test_feature_detection.py # pytest regression suite
test_data/
  sample_*.stl             # Phase 1 smoke-test STLs
  known/                    # Phase 2 known-dimension validation STLs
```

## Feature detection (Phase 2)

```python
from interlock3d.core.mesh_loader import load_trimesh
from interlock3d.core.feature_detection import detect_features

mesh = load_trimesh("part.stl")
for f in detect_features(mesh):
    print(f.feature_type, f.radius, f.extent, f.center, f.axis)
```

Run the numeric validation report (generates known test STLs, runs
detection, prints per-feature error against known values):

```bash
python scripts/validate_phase2.py
```

Or run the pytest regression suite:

```bash
pytest
```

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"          # [dev] adds pytest, for the test suite
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
