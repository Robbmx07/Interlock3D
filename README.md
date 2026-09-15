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
- **Phase 2** (feature detection engine): done. Given a mesh,
  `detect_features()` finds candidate holes, pegs/bosses, and flat mating
  faces. See `PROGRESS.md` for the numeric validation results.
- **Phase 3** (feature pairing UI): done. Loaded parts show their
  detected features as translucent highlight overlays; click one, then
  click the matching feature on a *different* part, choose a fit type,
  and confirm the pair. Pairs list in the side panel; select a row to
  remove it or change its fit type.
- **Phase 4** (compensation engine): done, backend only — not wired into
  the GUI yet (no material picker; that's natural to add in Phase 5 when
  it's actually needed to drive geometry modification). Given a confirmed
  pair, a material, and its fit type, `compute_pair_compensation()` looks
  up a clearance value from a JSON profile table and returns how much to
  grow/shrink each feature.

## Project layout

```
src/interlock3d/
  main.py                 # entry point
  core/
    mesh_loader.py         # STL loading (trimesh) + PyVista conversion + overlay meshes
    segmentation.py         # mesh -> smooth-surface patches
    feature_detection.py    # patches -> Feature list (holes/pegs/flats)
    features.py              # Feature dataclass
    pairing.py                # FeatureRef / FeaturePair data model + type-compatibility check
    compensation.py            # material/fit-type clearance lookup + per-pair offset computation
  data/
    compensation_profiles.json # PLA/PETG/ABS x press/sliding/clearance clearance table
  gui/
    main_window.py         # main window: toolbar, part/pairs lists, pairing workflow
    viewer.py                # embedded PyVista QtInteractor: rendering + picking
    feature_colors.py       # highlight color/opacity per feature state
    fit_type_dialog.py       # modal dialog to pick a pair's fit type
scripts/
  make_sample_stl.py      # throwaway STLs for Phase 1 smoke-testing
  validate_phase2.py       # numeric accuracy report for feature detection
tests/
  known_parts.py           # hand-made, known-dimension test STL generator
  test_feature_detection.py # Phase 2 pytest regression suite
  test_pairing_gui.py       # Phase 3 pytest suite (real VTK-driven click simulation)
  test_compensation.py      # Phase 4 pytest suite
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

## Compensation engine (Phase 4)

```python
from interlock3d.core.compensation import compute_pair_compensation

result = compute_pair_compensation(pair, "hole", "peg", material="PLA")
print(result.total_clearance_mm)          # target radial gap, mm
print(result.adjustment_a.material_removed_mm)  # >= 0, direction implied by feature_type
print(result.adjustment_b.material_removed_mm)
```

`pair.fit_type` (set when the pair was confirmed in the UI) selects
press/sliding/clearance; `material` selects PLA/PETG/ABS from
`data/compensation_profiles.json`. Values only cover hole+peg and
flat+flat pairs — anything else raises `CompensationError` (and the GUI
already refuses to let you *create* an incompatible pair in the first
place). This only computes numbers; applying them to mesh geometry is
Phase 5.

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

Detected features render as translucent highlight overlays (red = hole,
green = peg/boss, light blue = flat face). Click one to select it
(highlights yellow), then click the matching feature on a *different*
part; a dialog asks for a fit type (press / sliding / clearance) and
confirms the pair, coloring both features to match in the **Pairs** list
below. Select a pair in that list to **Remove Pair** or **Change Fit
Type...**.

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
`tests/test_pairing_gui.py` needs an actual display (or Xvfb) to run its
Qt/VTK-driven checks — run it as `xvfb-run pytest`; without a display it
skips those tests cleanly rather than failing (Qt aborts the whole
process if `QApplication()` can't find a platform plugin at all, so the
test module checks for `DISPLAY`/`QT_QPA_PLATFORM` before ever importing
Qt).

## Validation performed for Phase 1

Ran the app end-to-end under Xvfb (virtual display): loaded both sample
STLs through the real GUI code path (`MainWindow._load_and_add`), confirmed
both parts registered in the part list and viewer, and confirmed the
rendered screenshot showed both parts correctly shaped, positioned, and
distinctly colored (see session notes / `PROGRESS.md`).

## Validation performed for Phase 3

Rather than calling our Python click-handler directly, the pairing tests
drive an actual VTK cell pick (project a known surface point to screen
coordinates, invoke a real `vtkCellPicker`, feed its result into the
same code path a real mouse click would hit) — see `PROGRESS.md` for why
that mattered: it caught a real bug where PyVista's higher-level picking
helper silently failed to resolve clicks on plain triangulated surfaces.

## Roadmap

See the phase list in `PROGRESS.md`. Phase 5 (geometry modification) is
next, pending go-ahead.
