# Interlock3D — Progress Log

## Status overview

| Phase | Description | Status |
|---|---|---|
| 1 | Scaffold + load & view STL | **Done** |
| 2 | Feature detection engine | **Done** |
| 3 | Feature pairing UI | **Done** |
| 4 | Compensation engine | **Done** |
| 5 | Geometry modification | **Done** |
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

## Phase 2 — Feature detection engine

**Done.**

### What was built

- `interlock3d.core.segmentation.segment_smooth_patches(mesh, angle_threshold_deg)`:
  partitions a mesh's faces into patches of smoothly-connected surface.
  Builds the face-adjacency graph trimesh already computes
  (`face_adjacency` / `face_adjacency_angles`), keeps only edges whose
  dihedral angle is below the threshold, and finds connected components
  with `scipy.sparse.csgraph.connected_components`. This is what lets a
  tessellated cylindrical wall (small angle between adjacent panels) stay
  in one patch while cleanly breaking at the sharp edge where a hole or
  boss meets a surrounding flat face (measured at exactly 90° for a
  straight bore/boss).
- `interlock3d.core.feature_detection.detect_features(mesh, config)`: for
  each patch above a minimum size, classifies it and — for accepted
  patches — fits the actual primitive with `pyransac3d`:
  - **Flat**: patch's face normals are all nearly parallel → fit a
    `pyransac3d.Plane`; reject if the fit's inlier ratio is low.
  - **Cylindrical**: normals aren't parallel but are confined to the plane
    perpendicular to some axis (checked via the eigenvalues of the
    normals' second-moment matrix — a cylinder's face normals sweep
    around its axis, so they have one near-zero eigenvalue whose
    eigenvector *is* the axis) → fit a `pyransac3d.Cylinder` using that
    axis to seed a scale-appropriate RANSAC inlier threshold.
    - **Hole vs. peg**: for each face in the patch, compare its normal to
      the radial direction (patch centroid → axis). Normals pointing
      outward (positive dot product) → convex → `"peg"`; pointing inward
      → concave → `"hole"`.
    - Rejects fits with low inlier ratio, radius outside a configured
      range, or angular coverage around the axis below 180° (filters out
      fillets/partial rounds, which aren't real mating holes/pegs).
  - Anything that's neither (fillets, freeform curvature, spheres, cones)
    is left unclassified and simply doesn't appear in the output — no
    forced guess.
  - Output per feature: `feature_type`, `center`, `axis`, `radius`,
    `extent` (depth/height along axis), `area`, plus `fit_residual` and
    `inlier_ratio` for downstream confidence display.
- `tests/known_parts.py`: generates the hand-made, known-dimension test
  STLs the brief calls for, each paired with its exact expected feature
  list (type, radius, extent, area, with tolerances):
  - `flat_plate.stl` — 40×40×5mm box (6 known flat faces).
  - `plate_with_hole.stl` — disk (r=20mm) with a concentric r=5mm
    (⌀10mm) through-hole, via `trimesh.creation.annulus` (exact primitive,
    no boolean ops needed).
  - `boss_cylinder.stl` — free-standing r=6mm, h=15mm cylinder.
  - `plate_with_boss.stl` — the 40×40×5mm plate with a r=6mm boss
    positioned 1mm embedded / 15mm exposed, to test a multi-region
    assembly rather than an isolated primitive.
- `scripts/validate_phase2.py`: generates the known parts, runs
  detection, greedily matches detected features to expected ones, and
  prints a numeric pass/fail report (radius/extent/area error per
  feature). Exit code doubles as a CI gate.
- `tests/test_feature_detection.py`: pytest versions of the same checks,
  plus rotation-invariance and a pinned low-tessellation limitation test
  (see below), so `pytest` catches regressions without reading a report.

### Validation results (numeric, not vibes)

Ran `python scripts/validate_phase2.py` against all 4 known parts:

```
TOTAL: 22/22 expected features matched (100.0%), 0 unexpected detections
```

Every matched feature's radius and extent error was **0.0000mm** (exact,
to floating-point precision) on the generated (high-tessellation, 64
sections) fixtures; area errors were at most ~1.9mm² out of ~1178mm²
(<0.2%), which is polygon-discretization error in the fixture geometry
itself, not a detector error. Also spot-checked and confirmed correct
(see `pytest` suite):
- **Rotation invariance**: a boss cylinder rotated to a random arbitrary
  3D orientation and translated is still detected with radius/extent
  accurate to <0.05mm — confirms the algorithm isn't implicitly relying
  on axis-aligned geometry.
- **Multi-feature assembly**: a plate with 6 bosses of different radii
  (3–8mm) all detected with exact radius/extent recovery, in ~1.3s with
  the tuned RANSAC iteration count (500 — dropped from an initial default
  of 2000 after confirming accuracy was unaffected on this data; see
  `DetectionConfig.ransac_max_iterations`).
- **Hole vs. peg convexity**: confirmed on `plate_with_hole` — the
  concave through-hole reads as `"hole"`, the disk's own convex outer rim
  reads as `"peg"` (a convex cylindrical surface, not a functional peg —
  see trade-off note below).

### Key decisions and why

- **Segment first by dihedral angle, then classify by normal
  distribution, then fit with `pyransac3d`.** This is a 3-stage pipeline
  rather than one omnibus RANSAC pass because `pyransac3d` fits *one*
  primitive to a point cloud — it doesn't segment a mixed mesh into
  regions on its own. Segmentation has to come first; PCA-style
  classification (flat vs. cylindrical) narrows down *which* primitive to
  even attempt fitting, so we're not blindly trying a cylinder fit on
  every patch.
- **Classify cylindrical vs. flat from face-normal distribution, not
  curvature directly.** A patch's face normals for a true cylindrical
  wall are confined to the plane perpendicular to the axis; the smallest
  eigenvalue of their second-moment matrix and its eigenvector give a
  robust, closed-form axis estimate to seed RANSAC with — cheaper and
  more reliable than trying blind RANSAC cylinder fits on every patch
  indiscriminately.
- **Convexity by radial-direction vs. normal-direction dot product**,
  majority vote weighted by face area. Simple, and it's exactly the
  physical definition of concave (hole) vs. convex (peg) — no
  primitive-specific special-casing needed.
- **Known-dimension fixtures built from exact primitives
  (`box`/`cylinder`/`annulus`), not boolean CAD operations.** trimesh
  needs an external boolean backend (e.g. `manifold3d`) to actually drill
  a hole into a solid via CSG subtraction, which isn't installed. Using
  `annulus` for the hole test gives an *exact*, analytically-known hole
  radius/depth without that dependency. The trade-off: `plate_with_boss`
  is built by concatenating a box and a cylinder with the boss embedded
  1mm into the plate (not boolean-unioned), so the assembly isn't a
  single watertight solid — two separate touching/interpenetrating
  shells. That's fine for Phase 2, which only needs identifiable surface
  patches, not global manifoldness (that's explicitly a Phase 5 concern
  per the brief). **Open question for later:** if Phase 5's real-world
  test parts need actual boolean-drilled/fused geometry (closer to what a
  real CAD export looks like), we'll want a boolean mesh library —
  `manifold3d` is the natural pick (MIT-licensed, small, trimesh's
  current recommended backend) but it's a new dependency, so I'm flagging
  it rather than adding it now.

### Known limitation (surfaced, not silently patched around)

**Low-tessellation cylinders aren't detected.** The segmentation
threshold (`smooth_angle_deg`, default 35°) has to draw a line between
"these adjacent panels are one smooth cylindrical wall" and "these are
genuinely separate flat faces." Below ~16 sides per circle (panel angle
>35°, e.g. an 8-sided approximation of a round hole), each panel gets
read as its own separate flat facet instead of merging into one cylinder
— pinned as `test_low_tessellation_cylinder_is_a_known_limitation` so
this stays a deliberate, visible trade-off rather than a silent gap.
Mainstream slicers/CAD tools default to 24–64+ segments per circle for
STL export, so this shouldn't bite on typical real-world files, but very
low-poly or hand-optimized meshes could slip through undetected. The
threshold is a config value (`DetectionConfig.smooth_angle_deg`), tunable
without code changes if this turns out to matter in practice.

**Two reasonable defaults chosen without a strictly "correct" answer —
flagging per the working agreement:**
- `min_arc_degrees = 180`: a cylindrical patch needs at least half its
  circumference present to count as a hole/peg (filters out fillets and
  partial rounds). A tighter value (e.g. 270°) would filter more
  aggressively but risks rejecting genuinely truncated holes/bosses (e.g.
  a peg cut off by a nearby wall). Current value is a middle ground, not
  a strong claim.
- `min_inlier_ratio = 0.8`: how tolerant the fit-quality gate is. Higher
  is stricter (fewer false positives, more missed noisy real-world
  features); lower is more permissive. All test fixtures here are
  noise-free synthetic geometry, so this threshold is untested against
  real scanned/noisy STLs — worth revisiting once real user files are in
  the loop.

### Open questions / not blocking

- `manifold3d` (boolean ops) — see above; only needed if/when Phase 5
  wants true CAD-realistic fused test geometry.
- `min_inlier_ratio` and `min_arc_degrees` defaults are reasonable guesses,
  not empirically tuned against real (non-synthetic) STLs yet.

## Phase 3 — Feature pairing UI

**Done.**

### What was built

- **Picking wired to detected features, not just meshes.** Every part's
  `pyvista.PolyData` carries two custom per-cell arrays —
  `interlock3d_part_id` and `interlock3d_feature_id` (from
  `feature_detection.feature_id_per_face`, -1 where a face isn't part of
  any detected feature) — set once at `viewer.add_part()`. A click
  resolves straight to `(part_name, feature_index)` by reading those
  arrays at the picked cell id, with no separate spatial lookup needed.
- **Highlight overlays.** Every detected feature gets its own small
  overlay mesh (`mesh_loader.feature_overlay_polydata`): just that
  feature's faces, re-triangulated and pushed outward slightly along
  vertex normals (an offset scaled to the part's size) so it doesn't
  z-fight against the part's own surface at the same coordinates. Default
  tint by type (red=hole, green=peg, light blue=flat, low opacity since
  flats can cover most of a part's surface); recolored to yellow while
  pending selection, and to one of 8 cycling pair colors once confirmed —
  both features in a pair share the exact same color so the connection
  reads visually, not just from the pairs list.
- **Pairing state machine** in `MainWindow`: click a feature to select it
  (pending); click a second feature on a *different* part to open a
  fit-type dialog (press/sliding/clearance) and confirm the pair; click
  the same feature again to deselect; click a second feature on the
  *same* part, or one that's already paired, and it's rejected with a
  status-bar message rather than silently ignored or silently pairing
  something wrong.
- **Pairs list** in the side panel, each row a plain-language label
  (`part_a:type <-> part_b:type (Fit label)`); **Remove Pair** reverts
  both features to their default color, **Change Fit Type...** reopens
  the same dialog pre-filled with the pair's current choice.
- `core/pairing.py`: `FeatureRef` (part name + feature index) and
  `FeaturePair` (two refs + fit type) — deliberately just data, no
  behavior, so Phase 4's compensation engine can consume a pair list
  without depending on any GUI code.

### A real bug the validation caught

Built a headless test that drives an actual `vtkCellPicker` at real
screen coordinates (not just calling our Python callback directly) and
found that PyVista's higher-level `enable_element_picking` resolves the
picked cell via `mesh.find_containing_cell(picked_point)` — which
returned **-1 for a point the picker itself had just correctly hit**, a
few lines earlier in the same call. `find_containing_cell` is built for
volumetric cells; treating "is this 3D point inside a zero-thickness
surface triangle" as a containment test is degenerate and unreliable.
Had I only tested by calling our own `_on_picked` callback directly (the
easier, more tempting test to write), this would never have surfaced —
that only tests our downstream logic, not whether a real click would
even reach it. **Every real mouse click in the shipped app would have
silently failed to select anything.**

Fixed by dropping down one layer: `enable_surface_point_picking(...,
use_picker=True)` still gives PyVista's own click-vs-drag disambiguation
(so click-to-select and click-drag-to-rotate coexist correctly) and its
own `vtkCellPicker` invocation, but the callback reads `picker.GetCellId()`
directly instead of going through the buggy `get_cell()` path. Documented
inline in `viewer.py` so this doesn't get "simplified" back to the
higher-level call later.

### Validation

- `tests/test_pairing_gui.py`: 6 pytest cases, all driven through a real
  `vtkCellPicker.Pick()` call at screen coordinates computed by
  projecting an actual visible point on the target feature's surface
  (not the feature's abstract `.center` — for a cylinder that's an axis
  point, which for a concave hole isn't even on the mesh surface, and
  picking it would fail for reasons having nothing to do with our code).
  Covers: default overlay colors, click-to-select highlighting,
  click-again-to-deselect, cross-part pairing through the real dialog,
  color-revert on pair removal, and same-part second click being
  rejected without creating a bad pair.
- Confirmed via `xvfb-run pytest`: **18/18 passed** (12 from Phase 2 +
  6 new). Confirmed the 6 new ones skip cleanly (not crash) when no
  display is available — see the "known limitation" note below on why
  that needed an explicit guard.
- Visual confirmation via screenshot: default red/green/blue overlays,
  yellow "selected" highlight, and a shared pair color across two
  different parts' features, all rendered correctly.

### Key decisions and why

- **Per-cell `part_id`/`feature_id` arrays instead of actor-identity
  matching.** PyVista/VTK's picker gives you a dataset + cell id, not
  automatically "which of my named actors was this." Attaching our own
  lookup data directly to the mesh (which survives cell extraction during
  picking, confirmed empirically) sidesteps needing to reverse-map actors
  to parts by object identity or name-string parsing.
- **Overlay meshes built in `core/` (trimesh-based), not by asking
  PyVista to recompute normals on an extracted submesh.** An extracted
  patch usually isn't a closed manifold on its own, so PyVista's
  normal-auto-orientation on it isn't reliable. The full source mesh's
  `vertex_normals` (computed once, correctly, by trimesh) are reliable
  and require no submesh-specific reasoning — pass them straight into a
  small standalone `pv.PolyData`.
- **Pairs disallow same-part-to-same-part and re-pairing an
  already-paired feature**, both surfaced via status-bar message rather
  than silently doing nothing or silently allowing it. Reasonable
  defaults, not the only valid design — flagging per the working
  agreement:
  - *Same-part pairing disallowed*: mating features are, by definition,
    on two different physical parts that get printed separately and
    assembled — pairing two features on the same STL wouldn't correspond
    to anything printable. If a future use case needs it (e.g. a single
    STL containing multiple loose parts), this would need revisiting.
  - *One pair per feature*: simpler mental model (each hole/peg mates
    with exactly one thing), matches how these fits work physically in
    the vast majority of cases. A feature that genuinely needs multiple
    simultaneous mates (unusual) isn't supported — would need an explicit
    decision to relax this later, not a silent one.

### Known limitation (surfaced, not silently patched around)

**Overlapping parts aren't auto-arranged.** Loaded STL files render at
whatever coordinates they were modeled at — if two part files were both
designed around their own local origin (common for separately-exported
CAD components), they can load directly on top of each other, making
their features hard or impossible to click until the user manually
separates them (there's no pan/nudge-part tool yet — only camera
rotate/pan/zoom). Worth revisiting once real multi-part assemblies are
being tested rather than the synthetic single-feature fixtures used here
(the pairing test itself hits this: `boss_cylinder.stl` and
`plate_with_hole.stl` overlap as-is, so the test offsets one before
loading — see `tests/test_pairing_gui.py`'s `two_part_window` fixture).
**Open question for you:** should a later phase auto-arrange
("explode view") newly loaded parts side-by-side, or is manual
camera/part positioning an acceptable MVP workflow? Not implemented
either way — flagging rather than guessing.

**`QApplication()` aborts the whole process (not a catchable exception)
when no display/platform plugin is available at all.** Affects test
infrastructure, not the app itself: `tests/test_pairing_gui.py` checks
for `DISPLAY`/`WAYLAND_DISPLAY`/`QT_QPA_PLATFORM` *before* importing Qt
at all and skips cleanly if none are set, because a `try/except` around
the constructor doesn't help — a fatal Qt abort isn't a Python exception.
Confirmed both paths: clean skip with no display, full pass under
`xvfb-run`.

## Phase 4 — Compensation engine

**Done.**

### What was built

- `data/compensation_profiles.json`: the config-driven lookup table the
  brief called for (not hardcoded). Per material (PLA, PETG, ABS) × fit
  type (press, sliding, clearance): a `clearance_per_side_mm` value plus
  the `clearance_range_mm` it was chosen from, and a top-level `notes`
  field documenting where the numbers came from (see below). PLA's values
  are exactly the brief's own rough starting numbers (press 0.05–0.10,
  sliding 0.20–0.30, clearance 0.40–0.50); PETG and ABS are widened from
  that baseline.
- `core/compensation.py`:
  - `CompensationTable.load()` reads the JSON file (default path, or an
    explicit override for testing/future custom profiles) and exposes
    `materials()`, `fit_types()`, `get_clearance_mm()`,
    `get_clearance_range_mm()` — all raising a clear `CompensationError`
    for an unknown material or fit type rather than a raw `KeyError`.
  - `compute_pair_compensation(pair, feature_type_a, feature_type_b,
    material, split_ratio=0.5)` — the actual "given a confirmed pair,
    compute the offset" the brief asked for. Returns a `PairCompensation`
    with `total_clearance_mm` (the target gap) and two `FeatureAdjustment`s
    (one per feature in the pair).
  - Every `FeatureAdjustment.material_removed_mm` is **always
    non-negative** — direction is implied by `feature_type` rather than
    encoded as a sign (hole: added to radius; peg: subtracted from
    radius; flat: recessed inward along its own normal). All three are
    the same underlying operation ("remove material in the direction that
    widens the gap") described in feature-appropriate terms, which is
    exactly what Phase 5 will need to know to move the right vertices in
    the right direction — chose this over a signed-delta convention
    specifically to avoid the reader having to remember "positive means
    different things for a hole vs. a peg."
  - Only **hole+peg** and **flat+flat** pairs are computable —
    `are_types_compatible()` (added to `core/pairing.py`, not
    `compensation.py`, since it's about pair *validity* generally, not
    specifically about compensation) rejects hole+hole, peg+peg,
    hole+flat, peg+flat with a clear `CompensationError`.
- **Small fix to Phase 3's pairing UI, motivated directly by this
  phase**: `MainWindow` now calls `are_types_compatible()` before opening
  the fit-type dialog, and rejects an incompatible second click with a
  status-bar message instead of letting the user create a pair that
  Phase 4 (or Phase 6's report) would later have nothing sensible to do
  with. This wasn't caught in Phase 3 because none of that phase's tests
  happened to pair two features of incompatible types — worth noting as
  a gap in Phase 3's own test coverage that Phase 4's stricter validation
  surfaced, not something Phase 3 got structurally wrong.

### Validation

- `tests/test_compensation.py`: 19 pytest cases. Confirms PLA's values
  match the brief's stated ranges exactly; confirms press < sliding <
  clearance and PLA < PETG < ABS ordering hold for all fit
  types/materials; confirms a hole+peg pair's two adjustments always sum
  to exactly the target clearance (both the default 50/50 split and a
  custom skewed split); confirms pair order doesn't matter (hole+peg
  gives the same per-type adjustment as peg+hole); confirms flat+flat
  always splits 50/50 regardless of `split_ratio` (no physical basis to
  weight one face over the other); confirms every incompatible type
  combination raises `CompensationError`.
- Extended `tests/test_pairing_gui.py` with a case pairing a peg and a
  flat on different parts (ruling out the same-part rejection as the
  cause) and confirming it's rejected before the fit-type dialog opens.
  **This broke two existing Phase 3 tests** that paired plate_with_hole's
  outer rim (a convex cylindrical surface, type `"peg"`) with
  boss_cylinder's peg — a peg+peg combination, which was never a
  physically valid pair, just a Phase 3 testing convenience chosen for
  being easy to click reliably. Fixed by switching those two tests to
  pair flat+flat instead (equally reliable to click, and actually valid)
  — a genuine, caught-by-testing correction rather than a hypothetical
  one.
- Full suite: 38/38 pass under `xvfb-run pytest` (12 Phase 2 + 19 Phase 4
  + 7 Phase 3); 31 pass + 1 module skipped (GUI tests) without a display.

### Key decisions and why

- **`material_removed_mm` always non-negative, direction from
  `feature_type`.** Covered above — avoids sign-convention ambiguity
  across three different feature types.
- **50/50 default split between hole and peg, configurable via
  `split_ratio`.** There's no calibration data yet to justify correcting
  one feature more than the other (a hole undersizing and a peg
  oversizing are both known FDM tendencies, but by how much varies per
  printer — exactly what Phase 7's calibration wizard exists to measure).
  Splitting evenly is the least-assumption default; `split_ratio` exists
  so a future phase (or an advanced user) can bias it without redesigning
  the function. **Flagging as a real design decision, not the only
  reasonable one**: an equally defensible alternative is putting 100% of
  the clearance on the hole (many maker-community tips are phrased as
  "add 0.2mm to your hole diameter," leaving pegs at nominal), which is
  simpler to reason about but arbitrarily privileges one feature. Went
  with the symmetric default; easy to revisit once real print data
  exists.
- **PETG/ABS values are reasoned estimates, not measured data — said so
  directly in the JSON file itself**, not just in this doc. The
  directional reasoning (PETG oozier → needs more room; ABS shrinks more
  → needs more room) is standard, well-known FDM material behavior, but I
  have no per-material measurement study backing the *exact* numbers
  chosen. Treating this as settled would misrepresent confidence I don't
  have; Phase 7 existing as "replace generic defaults with your printer's
  measured behavior" is the intended fix, not something to fake now.
- **Compensation engine stays backend-only this phase — no material
  picker added to the GUI.** Consistent with how Phase 2 (detection) was
  backend-only until Phase 3 wired it into the UI: a material selector
  belongs naturally in Phase 5, when it's actually needed to invoke this
  engine and modify real geometry, rather than added speculatively now
  with nothing yet consuming its value.

### Known limitation / open question (surfaced, not silently resolved)

**Flat+flat clearance semantics are less battle-tested than hole+peg.**
Published FDM tolerancing guidance (including the brief's own numbers)
is almost entirely about circular hole/peg fits; "recess each flat face
inward along its normal by half the clearance value" is a reasonable,
internally-consistent generalization I made, not something drawn from an
established reference the way the cylindrical case is. It behaves
correctly and predictably (validated numerically), but if flat-face
mating turns out to matter a lot in practice, the underlying clearance
*values* (same table as hole/peg) might deserve their own dedicated
tuning rather than inheriting hole/peg numbers.

## Phase 5 — Geometry modification

**Done.** The brief flags this (with Phase 2) as the highest-risk phase
in the project — validated accordingly, not on vibes.

### What was built

- `core/geometry_modification.py`:
  - `displace_feature_vertices(vertices, faces, feature, material_removed_mm)`:
    the core operation. Moves exactly the vertices belonging to one
    feature's `face_indices`, direction implied by `feature.feature_type`
    (matching Phase 4's convention): hole → radially outward from the
    fitted axis; peg → radially inward; flat → along the negative of its
    own normal (recessed into the solid). Takes and returns a plain
    vertices array (not a mesh) specifically so multiple adjustments can
    be chained — later calls see earlier ones' displacements for any
    vertex they happen to share.
  - `apply_feature_adjustments(mesh, features, adjustments)`: the
    convenience wrapper — takes a list of `(feature_index,
    material_removed_mm)` pairs, returns a **new** `Trimesh` (input never
    mutated, confirmed by test).
  - `check_mesh_integrity(mesh) -> MeshIntegrityReport`: watertight,
    winding-consistent, no degenerate (near-zero-area) faces, and —
    added after a stress-test finding, see below — not inside-out via a
    negative-volume check. `.is_valid` folds all four into one boolean.
  - `GeometryModificationError`: raised *before* touching geometry for a
    request that's analytically known to be invalid (shrinking a peg by
    more than its own radius) rather than silently producing a
    self-intersected or negative-radius peg.
- Topology (faces, vertex connectivity) is never changed here — only
  vertex *positions* move. That's the whole safety argument for why this
  can preserve watertightness at all: if the input was already a valid
  closed, consistently-wound manifold, moving vertices without adding or
  removing any can only break that via actual geometric self-intersection,
  not via a topology bug.
- Added `plate_with_matched_hole.stl` to `tests/known_parts.py` (hole
  r=6mm, matching `boss_cylinder.stl`'s r=6mm peg) specifically so a
  realistic same-nominal-size hole+peg pair could be run through the
  actual Phase 3→4→5 pipeline end to end, rather than only unit-testing
  each phase's pieces in isolation. (`plate_with_hole.stl`'s r=5mm hole
  was deliberately mismatched against `boss_cylinder`'s r=6mm peg for
  Phase 2/3 detection/pairing tests — fine there, since those don't care
  about the two features fitting together, but wrong for testing
  "does the compensated clearance actually come out right.")

### A real finding from stress-testing, not just theory

There's no analytic upper bound on how much a hole can grow the way there
is for a peg (a peg's radius can't go below zero; a hole has no such
built-in ceiling — a `Feature` alone doesn't know where the surrounding
material actually ends). So I deliberately stress-tested with an
excessive enlargement (`plate_with_hole`'s r=5mm hole grown by 17mm,
i.e. past the disk's own r=20mm outer boundary) to see what happens
rather than assuming it's fine. Result: **`is_watertight` and
`is_winding_consistent` both stayed `True`** — because topology never
changed, both checks (which are purely about face/edge connectivity, not
actual 3D shape) are satisfied even though the hole had folded past the
outer boundary and the effective solid had inverted. The volume, though,
went **negative** (-1317 vs. the original +5881). Added a
`has_negative_volume` check specifically because of this — without it,
`MeshIntegrityReport` would have reported this corrupted mesh as fully
valid. Pinned as `test_oversized_hole_enlargement_is_caught_via_negative_volume`.

This directly matters for later phases: nothing here currently stops a
user (via a future GUI) from requesting a compensation value large
enough to trigger this. Phase 5's job per the brief was "apply the
computed offset... without corrupting the model" and to *validate*
that — which now happens — not to make every conceivable input safe by
construction (there's no clean way to bound "how much can this hole grow
before it's absurd" from the Feature data alone, without also knowing
the local surrounding geometry, which is a materially bigger feature).
**Flagging as an open item for Phase 6/GUI wiring**: when a material
picker and "apply" action land, the resulting mesh's integrity report
should be checked and surfaced to the user (block or warn) rather than
silently exporting corrupted geometry from an unreasonable clearance
value — not implemented yet since there's no GUI trigger for this action
until Phase 6.

### Validation

- `tests/test_geometry_modification.py`: 10 pytest cases. For hole
  enlargement, peg shrinkage, and flat recession: applies a realistic
  clearance (0.2–0.3mm), checks `is_valid` both isn't needed before
  (already known-good fixtures) and is confirmed after, then **re-runs
  `detect_features()` on the modified mesh** and checks the newly
  detected radius/extent/area matches what was requested — not just that
  vertices moved *somewhere*, but that the result is actually the right
  shape. Also covers: multiple adjustments composing correctly on one
  part, the negative-volume stress case above, peg-shrink-past-zero and
  exactly-zero both rejected, negative `material_removed_mm` rejected,
  input mesh never mutated, and the full Phase 3→4→5 pipeline test
  described above.
- `scripts/validate_phase5.py`: numeric report in the same style as
  Phase 2's, run against all the known parts' hole/peg/flat features at
  a real PLA-sliding-fit clearance (0.25mm). **5/5 checks passed, radius
  error 0.0000mm on every case** (floating-point exact, same as Phase
  2's synthetic-geometry results — expected, since this is exact vertex
  math on clean primitives, not an approximation).
- Visual confirmation: rendered before/after screenshots at an
  exaggerated clearance (1.5–2mm, well beyond any realistic fit value)
  specifically so any corruption would be visually obvious rather than
  numerically subtle — hole visibly grew, rim visibly shrank, boss
  visibly narrowed, no visible artifacts in any case.
- Full suite: 50/50 pass under `xvfb-run pytest` (14 Phase 2 + 19 Phase 4
  + 7 Phase 3 + 10 new Phase 5; Phase 2's count went 12→14 from the new
  `plate_with_matched_hole` fixture being picked up automatically by its
  existing parametrized tests).

### Key decisions and why

- **Vertex-array-in, vertex-array-out for the core displacement
  function; mesh-in, mesh-out only at the convenience-wrapper level.**
  Keeps the actual math free of any mesh-object bookkeeping and makes
  chaining multiple adjustments on one part explicit and testable in
  isolation (`displace_feature_vertices` is directly unit-tested without
  needing a full `apply_feature_adjustments` call).
- **No attempt to auto-bound "how much can a hole grow."** Considered
  adding a heuristic (e.g. reject if new radius exceeds some fraction of
  the part's bounding box), but any such bound would be a guess without
  knowing the actual surrounding geometry, and could reject a
  *legitimate* large clearance on a large part while still not
  catching every real problem. Chose to validate the *output*
  unconditionally (the negative-volume check) rather than gate the
  *input* with a heuristic that gives false confidence either way.
- **Shared-vertex behavior between adjacent features is inherited from
  Phase 3's already-flagged limitation, not newly introduced.** Two
  features whose patches share a boundary vertex (e.g. a hole's wall and
  the surrounding flat face) naturally move together correctly here,
  since it's literally the same vertex index in both patches — that's a
  feature, not a workaround. What's *not* handled is two DIFFERENT
  *adjusted* features sharing a vertex directly (no flat "buffer" between
  them) — not exercised by any known test part, and not expected in
  typical hole/peg-in-a-flat-face geometry, but noted here rather than
  silently assumed away.

## Next up

Phase 6 — export & report: write corrected STL(s) to disk (feeding
`apply_feature_adjustments()`'s output through `trimesh`'s own export),
plus a human-readable summary of every change made. This is also the
natural point to add the material picker and an "Apply Compensation"
action to the GUI, connecting Phase 3's confirmed pairs through Phase
4's compensation and Phase 5's modification into one real user-facing
flow — and to surface `check_mesh_integrity()`'s result to the user
before/instead of exporting something corrupted, per the open item
above. **Waiting for go-ahead before starting.**
