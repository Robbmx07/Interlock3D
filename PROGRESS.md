# Interlock3D — Progress Log

## Status overview

| Phase | Description | Status |
|---|---|---|
| 1 | Scaffold + load & view STL | **Done** |
| 2 | Feature detection engine | Not started |
| 3 | Feature pairing UI | Not started |
| 4 | Compensation engine | Not started |
| 5 | Geometry modification | Not started |
| 6 | Export & report | Not started |
| 7 | Calibration wizard (v1.1) | Not started |
| 8 | Packaging | Not started |

## Phase 1 — Scaffold + load & view STL

**Done.**

### What was built

- `src` layout Python package `interlock3d`, installable via
  `pip install -e .` (`pyproject.toml`, setuptools backend).
- `interlock3d.core.mesh_loader`: loads an STL via `trimesh.load(..., force="mesh")`
  and converts the resulting `Trimesh` to a `pyvista.PolyData` for rendering
  (`trimesh_to_pyvista`). Raises `MeshLoadError` on missing files, unparsable
  files, or files that don't resolve to a single triangle mesh.
- `interlock3d.gui.viewer.MeshViewer`: a `QWidget` wrapping an embedded
  `pyvistaqt.QtInteractor` (not `BackgroundPlotter`, so it's part of the main
  window rather than a separate popup window). Cycles through a fixed palette
  of 8 colors so parts are visually distinguishable; supports add/clear/reset
  camera.
- `interlock3d.gui.main_window.MainWindow`: toolbar with **Open STL(s)...**
  (multi-select file dialog), **Clear**, **Reset View**; a part list panel on
  the left; the 3D viewer filling the rest. Loaded parts get de-duplicated
  display names (`part`, `part_2`, ...) if names collide.
- `scripts/make_sample_stl.py`: generates two small throwaway STL files
  (`sample_plate.stl`, a 40×40×5mm box; `sample_boss.stl`, a 12mm-diameter
  cylinder) into `test_data/` purely for manual smoke-testing of the load/
  render pipeline. **Not** the precision-dimension test set called for in
  Phase 2 — that will be built specifically to validate feature detection.

### Key decisions and why

- **trimesh owns loading, PyVista owns display.** STL parsing and all future
  mesh geometry work (Phase 2 detection, Phase 5 modification) goes through
  `trimesh.Trimesh` objects; PyVista only ever sees a converted `PolyData`
  for rendering. This keeps one source of truth for the actual mesh data
  that later phases will operate on, rather than juggling two mesh
  representations with potentially different vertex/face ordering.
- **Embedded `QtInteractor`, not `BackgroundPlotter`.** `BackgroundPlotter`
  opens its own top-level window; embedding `QtInteractor` inside the main
  window's layout is what lets the 3D view sit next to a part list and
  toolbar in one window, which Phase 3's pairing UI will need anyway (click
  a feature in the same view the part list lives in).
- **`src/` layout.** Keeps the installable package separate from
  `scripts/`, `test_data/`, and future top-level project files (packaging
  configs, calibration data, etc.) added in later phases.

### Validation

No unit test suite yet (nothing non-trivial to unit test at this phase —
it's wiring). Instead, ran a full end-to-end smoke test under Xvfb (this
dev container has no real display):

1. Installed the package into a venv (`pip install -e .`); confirmed actual
   installed versions: PySide6 6.11.2, pyvista 0.49.0, pyvistaqt 0.13.1,
   trimesh 5.1.0, numpy 2.4.6.
2. Generated the two sample STLs via `scripts/make_sample_stl.py`.
3. Launched the real `MainWindow` under `xvfb-run`, called the actual
   `_load_and_add` handler (same code path the toolbar button triggers) for
   both sample files, and asserted both parts registered in `self._parts`
   and the part list widget.
4. Took a screenshot of the live `QtInteractor` render and visually
   confirmed: the plate renders as a flat box, the boss renders as a
   cylinder sitting on top of it in the correct position, and the two parts
   are in distinct colors (blue / orange) — i.e. the trimesh→PyVista
   conversion and embedded rendering are both correct, not just
   "didn't crash."

**Environment note:** running this headless required installing several
system X11/EGL runtime libraries that a normal desktop already has
(`libegl1`, `libxkbcommon-x11-0`, `libxcb-cursor0`, `libxcb-icccm4`,
`libxcb-keysyms1`, `libxcb-shape0`, plus a few transitive deps). This is a
container-only wrinkle, documented in `README.md`, not expected to matter
on the user's actual Windows/Mac dev machine.

### Open questions / nothing blocking

None currently blocking. One thing to revisit later: PyVista/trimesh/numpy
versions installed are newer major versions than what was loosely implied
by "already decided" (e.g. numpy 2.x, pyvista 0.49). Nothing in Phase 1
exercised any breaking API surface, but worth keeping an eye on as Phase 2
starts using more of trimesh's mesh-query API and pyransac3d gets added.

## Next up

Phase 2 — feature detection engine (cylindrical holes/pegs, flat mating
faces via `pyransac3d`), validated numerically against a hand-made set of
known-dimension test STLs. **Waiting for go-ahead before starting.**
