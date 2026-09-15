# Interlock3D — Progress Log

## Status overview

| Phase | Description | Status |
|---|---|---|
| 1 | Scaffold + load & view STL | **Done** |
| 2 | Feature detection engine | **Done** |
| 3 | Feature pairing UI | **Done** |
| 4 | Compensation engine | **Done** |
| 5 | Geometry modification | **Done** |
| 6 | Export & report | **Done** |
| 7 | Calibration wizard (v1.1) | **Done** |
| 8 | Packaging | **Done for Linux; Windows/Mac unvalidated** |

A separate **Web Reimplementation Track** (browser/JS, no install) was
started after Phase 8, at the user's request for a no-install way to try
the tool. See its own status table further down — it does not replace
or supersede the Python desktop app above, which remains the primary,
complete product.

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

## Phase 6 — Export & report

**Done.** This is the phase that connects everything: Phase 3's
confirmed pairs, through Phase 4's compensation, through Phase 5's
geometry modification, out to actual files a user can print. **With this
phase done, the full MVP (Phases 1-6, per the brief's own framing) is
complete end to end** — not just each phase's pieces individually.

### What was built

- `core/export.py`:
  - `build_export_plan(pairs, parts, features_by_part, material,
    split_ratio=0.5, table=None) -> ExportPlan`: for every confirmed
    pair, computes its compensation (Phase 4) and groups the resulting
    per-feature adjustments by part; applies each part's adjustments in
    one `apply_feature_adjustments()` call (Phase 5) and runs
    `check_mesh_integrity()` on the result. Parts with no pairs at all
    are included as unchanged copies — the point is exporting a complete,
    consistent assembly, not just the parts that happened to get modified.
  - `ChangeRecord`: one feature's change in plain language (`"plate:
    hole radius 5.000mm -> 5.250mm (+0.250mm) -- paired with boss's peg,
    Sliding fit, PLA"`) — this is directly the "which feature, original
    size, new size, why" the brief asked for.
  - `format_report(plan) -> str`: the human-readable summary — every
    `ChangeRecord`, plus each modified part's integrity status. Falls
    back to a clear "no pairs confirmed, exported unmodified" message
    when there's nothing to report, rather than an empty or confusing
    output.
  - `export_plan(plan, output_dir)`: writes one `{part}_corrected.stl`
    per part (via `trimesh`'s own export) plus `compensation_report.txt`,
    both to a chosen directory. Every file gets the `_corrected` suffix,
    even an entirely unmodified part — so exporting into the same folder
    the original inputs live in can never silently overwrite them.
- GUI wiring (the "Apply Compensation" + material picker work flagged as
  open in Phase 5's notes):
  - A **Material** combo box on the toolbar (PLA/PETG/ABS, from
    `CompensationTable.materials()`, defaulting to PLA).
  - **Export Corrected STL(s)...** toolbar action: builds the plan for
    the selected material; if `plan.has_integrity_problems`, blocks with
    a `QMessageBox.critical` naming the affected part(s) and explaining
    likely causes — **before** ever prompting for a save location. This
    is the "surface integrity checks to the user" item flagged as open in
    Phase 5's PROGRESS.md notes, now actually wired up rather than just
    planned.
  - Otherwise: a folder picker, then the files are written and a
    `ReportDialog` (read-only, monospace `QTextEdit`) shows the same
    report text that got written to disk.

### Validation

- `tests/test_export.py`: 8 pytest cases against the core pipeline —
  no-pairs leaves parts unmodified; one pair produces exactly two
  `ChangeRecord`s with the right original/new radii; the applied geometry
  is actually valid and the modified mesh, re-detected, shows the
  expected radius change; an artificially huge clearance is correctly
  rejected (not silently swallowed) via the same `GeometryModificationError`
  path Phase 5 already validated; the report text mentions every change
  and the right material; `export_plan` writes exactly the expected files
  and — checked by reloading the written STL from disk and re-detecting —
  they actually reflect the modification, not just exist; and exporting
  into the same directory as the known-part fixtures never overwrites an
  original filename.
- `tests/test_export_gui.py`: 4 pytest cases through the real
  `MainWindow`, in the same style as Phase 3's picking tests (`QFileDialog`
  and the report dialog's modal `exec()` are handled via monkeypatching /
  a scheduled `QTimer` accept, not skipped): exporting with nothing loaded
  never opens a dialog; exporting with no pairs writes unmodified files
  whose report says so; a real click-confirmed pair, exported, produces
  files that reload and show the expected larger hole; and — using a
  hand-built `ExportPlan` with a deliberately invalid `MeshIntegrityReport`
  to force the block path, since the real known parts don't happen to
  break at any real material's clearance values — confirms the folder
  picker is never shown and exactly one error dialog appears when there's
  a geometry problem.
- Manual end-to-end smoke test of the actual running app (screenshots
  captured): loaded two parts, **switched the material picker to PETG**
  (not the default), paired a hole and peg via real simulated clicks,
  exported, and confirmed the written `compensation_report.txt` reflects
  PETG's clearance value (0.30mm/side, not PLA's 0.25mm) — proving the
  picker actually drives the calculation end to end, not just that a
  hardcoded default flows through. Full report text and both
  screenshots included in the session; both features' overlays correctly
  shared one pair color, matching Phase 3's established behavior.
- Full suite: 62/62 pass under `xvfb-run pytest` (14 Phase 2 + 19 Phase 4
  + 7 Phase 3 + 10 Phase 5 + 8 + 4 new Phase 6); 51 pass + 2 GUI modules
  skipped cleanly without a display.

### Key decisions and why

- **Unmodified parts are still exported (as unchanged copies), not
  skipped.** The whole point is a ready-to-print *assembly* — a user
  shouldn't have to remember which of their loaded files happened to get
  geometry changes and manually track down the rest from wherever they
  originally were.
- **Integrity problems block the entire export, not just the affected
  part.** Considered exporting the valid parts and skipping/warning about
  the broken one, but a partial export of a multi-part assembly (some
  parts corrected, one silently missing) seems more likely to cause a
  confusing failed print than a clear upfront "fix this pair first."
  Simpler to reason about; revisit if partial export turns out to matter
  in practice.
- **`_corrected` suffix on every exported file, unconditionally.** The
  alternative (only modified parts get the suffix) would mean an
  unmodified part's export filename exactly matches its original input
  filename — a real risk of silently overwriting the source STL if a user
  exports into the same folder they loaded from. Consistent suffixing
  costs nothing and removes that risk entirely.
- **Report is plain text, not JSON/HTML/PDF.** The brief calls for "a
  human-readable summary" — plain text is the simplest thing that's
  actually readable by a hobbyist without opening another tool, and it's
  trivially both a file on disk and text to display in a dialog with no
  extra rendering work. Worth reconsidering only if a future phase wants
  something more structured (e.g. re-parsing the report programmatically,
  which nothing currently does — `ExportPlan`/`ChangeRecord` are already
  the structured form for any code that needs one).

### Open item carried forward (not new, still not fully resolved)

Phase 5 flagged that nothing bounds "how much can a hole grow" from a
`Feature` alone. That's now connected to a real user-facing consequence:
an aggressive-enough compensation value genuinely can block export. The
block is clear and actionable (names the part, explains likely causes),
which is the right behavior for now, but it's still possible for a
combination of material + fit type + an unusually small/thin feature to
be blocked when a smarter algorithm might have found a valid partial
adjustment. Not addressed here — flagging it stays more honest than
quietly working around it with an untested heuristic.

## Phase 7 — Calibration wizard (v1.1)

**Done.** The brief calls this the "Pro-tier differentiator" and gates
it on Phases 1-6 being solid; built with the same validation rigor as
the higher-risk earlier phases (Phase 2, Phase 5) rather than treating
it as a lighter-weight add-on.

### What was built

- `core/calibration.py`:
  - `generate_calibration_part(nominal_radius_mm=5.0, ...)`: one small
    STL containing a through-hole and a free-standing peg, both at the
    same nominal radius, positioned apart so they print together in one
    job. Deliberately one size, not a size ladder — the brief calls for
    "a small calibration test STL," and one hole+peg pair at a
    caliper-friendly size (10mm diameter by default) is enough to
    characterize a printer's systematic bias without turning this into a
    multi-hour print. Built the same way as `plate_with_boss.stl` back
    in Phase 2 (concatenated disjoint primitives — an annulus and a
    cylinder — not boolean-fused; still one valid multi-body STL file
    that slicers handle natively).
  - `CalibrationMeasurement`: what the user enters (nominal radius +
    measured hole/peg *diameters*, since that's what a caliper reads,
    not radii). `.hole_radius_bias_mm()` / `.peg_radius_bias_mm()`
    convert to the printer's actual radial error.
  - `CalibrationProfile`: the derived, persistable result (bias values +
    a timestamp + an optional label like "Ender 3, PLA, 0.4mm nozzle").
  - `save_calibration_profile()` / `load_calibration_profile()`: JSON
    persistence to `~/.interlock3d/calibration.json` by default (this is
    an offline app — calibration has to survive between sessions without
    any server to store it on). Missing file returns `None` (the normal
    "never calibrated" state, not an error); a corrupt file raises
    `CalibrationError` rather than silently pretending no calibration
    exists.
  - `compute_calibrated_pair_compensation()`: wraps Phase 4's
    `compute_pair_compensation()` and **adds** the calibrated bias to
    each hole/peg adjustment, rather than replacing the generic
    computation. This was a real design decision (see below) — the
    fit-type philosophy (press/sliding/clearance) stays exactly what
    Phase 4 already validated; calibration only corrects for *this
    printer's* measured deviation from nominal, layered on top. Flat+flat
    pairs pass through unchanged (the calibration part only measures
    circular features).
- `core/export.py` extended (not replaced) with an optional
  `calibration` parameter on `build_export_plan()`: when active, hole+peg
  pairs use the calibrated computation; flat+flat pairs and no-calibration
  runs are byte-identical to Phase 6's existing behavior. `ChangeRecord`
  gained a `calibration_applied` flag so the human-readable report says
  "calibrated" next to any change that used it — the report should be
  honest about *why* a number is what it is, which was already the whole
  point of Phase 6.
- GUI: `gui/calibration_wizard.py` (`QWizard`, 3 pages — export the test
  STL, enter measurements with a live bias preview, review and save) and
  `gui/main_window.py` wiring: **Calibrate Printer...** / **Clear
  Calibration** toolbar actions, a status label showing whether
  calibration is active, and `export_corrected()` now passes the active
  profile through to `build_export_plan()`.

### Two real bugs found by testing, not by inspection

1. **Same "outer rim reads as a peg" issue from Phase 2, biting a new
   test.** The calibration part's hole-plate has an outer rim that's a
   genuine convex cylindrical surface — same as `plate_with_hole.stl` —
   so it also registers as `feature_type="peg"`, and being larger, it has
   more area and sorts *before* the actual test peg in
   `detect_features()`'s output. My first test draft's `_find(features,
   "peg")` silently grabbed the rim (radius 15mm) instead of the test peg
   (radius 5mm), which cascaded into a `GeometryModificationError` several
   steps later in an unrelated-looking assertion — the kind of failure
   that's confusing to debug from the error alone, and exactly why the
   fix was in the *test's* feature-selection logic (disambiguate by
   radius proximity to the known nominal value), not in the detection
   engine, which was behaving correctly the whole time.
2. **`export_corrected()` referenced `self._active_calibration` before it
   was ever defined**, and separately never caught
   `GeometryModificationError` from `build_export_plan()` (only
   `CompensationError`) — a real crash-on-invalid-input gap that existed
   since Phase 6 but had gone unnoticed because Phase 6's own tests never
   happened to trigger `GeometryModificationError` through the GUI path.
   Adding calibration bias on top of a fit clearance makes hitting that
   path meaningfully more likely (a bigger combined adjustment is more
   likely to exceed a small peg's radius), which is what surfaced it.
   Both fixed: `self._active_calibration` is now set in `__init__`
   (loaded from disk, defaulting to `None`), and `export_corrected()`
   catches `GeometryModificationError` with the same clear-message
   pattern already used for the Phase 6 integrity-block case.

### Validation

- **Digital-twin round trip** (the core validation claim for this
  phase): generate the calibration part, apply a **known** synthetic
  printer error to it using Phase 5's own real vertex-displacement math
  (not a hand-rolled stand-in) — e.g. hole shrinks 0.15mm, peg grows
  0.10mm — then re-detect features on the "printed" mesh to get simulated
  caliper readings, feed those into `compute_calibration_profile()`, and
  confirm the derived bias **exactly recovers the injected error**
  (parametrized across 4 error magnitudes including zero-bias). This is a
  materially stronger claim than unit-testing the bias arithmetic in
  isolation: it validates that generate → (simulated) print → measure →
  derive is self-consistent end to end.
- Confirmed the derived profile **generalizes**: applied a profile
  derived from the calibration part to a genuinely different, separate
  hole+peg pair (`plate_with_matched_hole.stl` + `boss_cylinder.stl`) and
  confirmed the resulting radii match `base_compensation ±
  calibration_bias` exactly, with the modified meshes still passing
  `check_mesh_integrity()`.
- `tests/test_calibration.py`: 15 pytest cases — the round trip above,
  generator input validation, an atypical negative-bias case (a printer
  whose holes print *oversized*, handled rather than assumed away),
  save/load round trip, corrupt/malformed file handling, the additive
  compensation wrapper's arithmetic, the negative-result clamp, flat+flat
  pass-through, and the cross-pair generalization test.
- `tests/test_calibration_gui.py`: 9 pytest cases through the real
  `QWizard` and `MainWindow` — live bias preview updates as fields
  change, the exported STL is real/watertight, finishing the wizard
  saves+activates a profile and updates the toolbar label, cancelling
  leaves everything unchanged, "Clear Calibration" removes the saved
  file, and an active calibration measurably changes what
  `build_export_plan()` produces (compared directly against an
  uncalibrated run). Every test here uses an explicit `calibration_path`
  fixture pointed at `tmp_path`, never the real `~/.interlock3d/` — see
  the testability decision below for why that mattered.
- Manual end-to-end smoke test of the real running app (4 screenshots
  captured): opened the actual wizard, exported the test STL through the
  real button, ran it through the same digital-twin injection as the
  automated test (0.12mm hole / 0.08mm peg this time, to confirm it
  wasn't the exact same numbers as the pytest case), entered the
  simulated readings into the real spin boxes, watched the live preview
  and final summary page both show the exactly-correct derived bias, and
  confirmed the toolbar's calibration label updated to show the label
  text ("Smoke-test printer, PLA") after finishing.
- Full suite: 86/86 pass under `xvfb-run pytest` (66 from Phases 2-6 + 15
  Phase 7 core + 9 Phase 7 GUI, three fewer than a raw sum since 4 GUI
  test *modules*, not individual tests, are what skip without a display);
  66 pass + 3 modules skipped cleanly without one.

### Key decisions and why

- **Calibration adds to the generic compensation rather than replacing
  it.** Considered having a calibration profile define its own complete
  set of press/sliding/clearance values (mirroring the generic
  material table's shape). Rejected: that would require calibrating
  against *every* fit type and material combination a user might want,
  when the test print only measures one thing (this printer's systematic
  hole/peg sizing error) that's largely independent of which fit type is
  later requested. Additive composition needs only one calibration print
  ever, and reuses Phase 4's already-validated fit-type logic unchanged.
- **One size, not a size ladder.** A calibration test with holes/pegs at
  several diameters (common in more elaborate community calibration
  prints) would characterize how bias scales with feature size, which a
  single-size test can't capture. Traded that precision for print time
  and workflow simplicity, matching the brief's explicit "a small
  calibration test STL" — revisit if single-size calibration turns out
  to be a meaningfully worse predictor across very different feature
  sizes than the one it was measured at.
- **Calibration is one global, offline-persisted profile — not per
  material, per printer profile slot, or versioned.** Simplest possible
  model for a single-printer hobbyist (the stated target user), and the
  brief's own framing ("derives a custom compensation profile for that
  specific printer") doesn't ask for multi-printer profile management.
  A user with multiple printers/materials would need to
  recalibrate when switching — a real limitation for that use case, not
  addressed here, flagged rather than silently designed around.
- **Injectable `calibration_path` on `MainWindow`, not a hardcoded
  `~/.interlock3d/` everywhere.** Needed for test isolation: without it,
  one test's saved calibration would leak into the next test's fresh
  `MainWindow` instance (they'd all read/write the same real file), and
  tests would pollute the actual home directory of whatever machine runs
  them. Confirmed directly — checked `~/.interlock3d/` doesn't exist
  after running the full suite.

## Phase 8 — Packaging

**Done for Linux; Windows and Mac still need validating on those
platforms.** This is a genuine, structural limitation of this dev
environment (a Linux container), not something worked around — surfacing
it clearly rather than claiming full completion.

### What was built

- `packaging/Interlock3D.spec`: a version-controlled PyInstaller spec
  (not a one-off CLI command) that:
  - Bundles `data/compensation_profiles.json` explicitly via `datas=`.
    **This was not optional** — see the bug below.
  - Picks up `packaging/icon.ico` (Windows) or `packaging/icon.icns`
    (macOS) automatically if present, no changes needed; falls back to
    no icon if absent (neither is included — cosmetic, out of scope
    unless requested).
  - Builds a `.app` bundle via `BUNDLE(...)` on macOS specifically
    (`sys.platform == "darwin"`), a folder-plus-executable layout on
    Windows/Linux (PyInstaller's normal `--onedir`-equivalent output).
  - Uses `SPECPATH` (PyInstaller's injected global) to resolve the
    project root, so it works regardless of the invoking working
    directory rather than assuming one.
- `packaging/README.md`: build steps, the cross-compilation limitation
  stated up front, expected output size (~800MB-1GB — normal for a
  VTK-based app, not a packaging mistake), and troubleshooting tips
  (temporarily flip `console=True` to see a startup crash's traceback,
  since the shipped `console=False` build has no visible console at all;
  expected unsigned-binary warnings on both Windows SmartScreen and macOS
  Gatekeeper, with code-signing explicitly named as future/separate work
  rather than silently promised).
- `packaging/smoke_test.sh`: automates the actual validation this phase
  ran (below) so it's re-runnable after any future build, not just
  something done once by hand. Not added to the regular `pytest` suite —
  packaging builds take minutes and produce ~1GB of output, which doesn't
  belong in a fast, frequently-run test suite the way the rest of this
  project's tests do.
- `pyproject.toml`: added a `[project.optional-dependencies] build =
  ["pyinstaller>=6.0"]` group (separate from `dev`/pytest, since testing
  and packaging are different concerns with different audiences — a
  contributor running the test suite doesn't need PyInstaller).

### A real bug found immediately, by actually running the build

A first, naive `pyinstaller main.py` (no spec file) built "successfully"
with no errors — but **crashed on startup** with
`FileNotFoundError: compensation_profiles.json`. PyInstaller has no
awareness of `pyproject.toml`'s `[tool.setuptools.package-data]`
declaration (that's a `pip install`/wheel-building concept, not
something PyInstaller reads), so the JSON data file silently wasn't
bundled at all despite the bundle otherwise looking complete. Confirmed
by actually launching the built executable under Xvfb and watching it
crash with that exact traceback, not just inspecting the build log.
Fixed by adding an explicit `datas=[...]` entry, then re-verified the
file is present in the bundle and the app launches successfully. This is
exactly the kind of thing that "the build completed without errors"
would never have caught — PyInstaller considers a missing runtime data
file a complete non-issue at build time.

### Validation

A packaging step doesn't change any application logic — everything
Phases 1-7 validated about *correctness* still holds. What's specific to
this phase is: does the exact same code actually run once frozen,
outside the dev venv, with all its native dependencies (Qt, VTK, OpenGL)
bundled? That's genuinely a different question (PyInstaller + VTK is a
commonly cited rough combination in the community, and the missing-JSON
bug above is a direct example of "looks fine at build time, breaks at
run time"), so it got real validation rather than being assumed to work
because the earlier phases did:

1. Built via `Interlock3D.spec` on this Linux container (only platform
   available here).
2. Launched the resulting executable under Xvfb — confirmed the full
   toolbar renders correctly (Material picker, Export, Calibrate
   Printer, Clear Calibration, and the calibration status label all
   present and showing the correct "none (generic defaults)" state for
   a fresh run).
3. **Drove the actual "Open STL(s)..." file dialog with real X11 input
   events** (`xdotool` — mouse click on the real button coordinates,
   keyboard input into the real dialog, not a test hook or any code path
   unavailable in the shipped binary) to load a real STL file into the
   frozen app.
4. Confirmed via screenshot: the mesh rendered correctly (a disk with a
   through-hole, both surfaces in the correct feature-type colors),
   feature detection ran and reported the correct count in the status
   bar ("4 candidate features detected"), and the part list updated
   correctly — this specifically exercises VTK's shader/rendering-
   resource loading from inside the frozen bundle, which is the part of
   a PyInstaller+VTK build most likely to break silently or behave
   differently than in a dev venv.
5. Re-ran the whole procedure a second time via `packaging/smoke_test.sh`
   (built fresh from a clean `packaging/build`+`dist`, not reusing
   artifacts) to confirm the result reproduces, not a one-off fluke.

**Not done, and flagged rather than assumed**: no Windows or macOS build
has been produced or tested at all. This container is Linux-only;
PyInstaller cannot cross-compile, so producing and validating those
requires actually running `pyinstaller Interlock3D.spec` on a Windows
machine and a Mac. The spec file is written to be platform-generic (the
`sys.platform` branches for icon paths and the `BUNDLE` step are already
in place), so there's no known reason it wouldn't work the same way —
but "should work" and "validated" are different claims, and only the
Linux one has actually been tested.

### Key decisions and why

- **A committed `.spec` file, not a documented CLI command.** A build
  command with several flags (`--windowed`, `--add-data` with
  OS-specific path-separator syntax, etc.) is exactly the kind of thing
  that silently drifts from what's documented, or breaks on one OS
  because of the `:`/`;` separator difference in `--add-data`. A `.spec`
  file is real Python, runs identically however it's invoked, and is
  version-controlled — the missing-JSON bug is precisely the kind of
  regression a committed, working spec file prevents from recurring.
- **Build artifacts (`packaging/build/`, `packaging/dist/`) are
  gitignored, not committed.** ~1GB of regeneratable binary output
  doesn't belong in version control; already covered by the existing
  `build/`/`dist/` gitignore patterns (confirmed via `git status
  --ignored` rather than assumed).
- **`smoke_test.sh` as a standalone script, not a pytest test.**
  Consistent with why `validate_phase2.py` and `validate_phase5.py`
  are standalone scripts rather than folded into the main suite: this
  is a slow (minutes), heavy (~1GB), environment-specific (needs
  Xvfb+xdotool+ImageMagick on Linux) check that doesn't belong running
  on every `pytest` invocation the way the fast unit/integration tests
  do.

## Next up (Python desktop app)

**All 8 phases are done for Linux.** Nothing else is planned unless you
want: (a) the Windows/Mac builds actually produced and tested (needs
those platforms directly), (b) an app icon, (c) code-signing for
distribution (a separate, paid-certificate process on both platforms),
or (d) something new. This file will be updated once any of those
happen.

---

# Web Reimplementation Track

Started after Python Phase 8, at the user's explicit request (asked for
an HTML version "so I don't have to install it," then confirmed — after
being shown the tradeoff explicitly — that they wanted a full
reimplementation of the pipeline in the browser, not just a quick static
viewer). This is a **separate, parallel effort**, not a replacement: the
Python desktop app above is the complete, primary product this whole
project was built around. The web version exists because "no install"
is a real, different value proposition, not because the desktop app is
lacking.

Developed the same way as the Python app: one phase at a time, real
validation before calling a phase done, stop and report back rather than
plowing ahead. The phase breakdown mirrors the original 8 (view → detect
→ pair → compensate → modify geometry → export → calibrate → "package"),
adapted for a browser environment where "package" doesn't apply the same
way (there's no install step to package — the page itself already runs
anywhere).

## Status overview

| Phase | Description | Status |
|---|---|---|
| W1 | Load & view STL | **Done** |
| W2 | Feature detection | Not started |
| W3 | Feature pairing UI | Not started |
| W4 | Compensation engine | Not started |
| W5 | Geometry modification | Not started |
| W6 | Export & report | Not started |
| W7 | Calibration wizard | Not started |

## Phase W1 — Load & view STL

**Done.**

### What was built

Single self-contained HTML file (`web/viewer.html`, also published as a
Claude Artifact: https://claude.ai/artifact/7WfUs7CACk7Yxhz4RxbYtH) — no
build step, no server, opens directly in any WebGL2-capable browser.

- **A hand-written STL parser** (binary + ASCII), not a library. Binary
  format detected by checking whether the declared triangle count
  (bytes 80-83) makes the file exactly the expected size
  (`84 + triCount*50`); falls back to regex-based ASCII parsing
  (`facet normal ... outer loop ... vertex ...`) otherwise.
- **A hand-written WebGL2 renderer** — no three.js or any other 3D
  library. Own `mat4Perspective`/`mat4LookAt`/`mat4Multiply` (small,
  standard column-major implementations), own orbit/pan/zoom camera
  (spherical coordinates around a target point, Z-up to match the
  print-bed convention the Python app and its STL fixtures already
  use), own flat-shaded lighting shader (key + fill directional lights
  plus ambient, no external lighting model).
- **A print-bed reference grid** rendered under the loaded parts, sized
  and spaced to the current scene's bounding box — both a visual anchor
  and a literal nod to the actual print bed these files are headed for.
- **Two bundled example parts**: the exact same `plate_with_matched_hole.stl`
  / `boss_cylinder.stl` pair the Python app's Phase 5 used to validate
  hole+peg compensation, embedded as base64 and auto-loaded on open (so
  the page shows real, meaningful geometry immediately rather than an
  empty canvas), clearly labeled "example" in the parts list. Loading
  the user's own file(s) works via drag-and-drop or a file picker,
  same as the desktop app.
- Visual design: a considered "workshop instrument" identity (Big
  Shoulders for the wordmark, IBM Plex Sans/Mono for UI and numeric
  readouts, a warm-orange/steel-blue accent pair evoking heated
  filament vs. precision measurement) rather than a default/generic
  look, with a proper light and dark theme (not just an inverted
  palette) and a bottom "DRO-style" numeric readout strip (part count,
  triangle count, bounding box in mm) echoing a digital caliper's
  readout, on-theme for an FDM tolerancing tool.

### Why no three.js (or any 3D library)

The Artifact runtime only allows loading external scripts from a small,
fixed set of hosts (cdnjs, jsdelivr, the Tailwind play CDN, jquery's
CDN), each at an exact pinned version path. This session's sandboxed
Bash tool cannot reach those hosts to verify an exact, currently-valid
URL (confirmed directly: a `curl` to `cdnjs.cloudflare.com` was rejected
by the environment's own egress proxy with a 403 policy denial) — and
guessing a version path wrong means the published page fails outright
for the user with no fallback, for a library used only for two features
(triangle rendering + orbit camera) that are straightforward to
implement directly. Writing a small, self-contained renderer instead
removes that failure mode entirely, and — as a genuine side benefit, not
the main motivation — means the page keeps working fully offline after
the first load (no runtime CDN fetch at all, just two Google Fonts
`<link>` tags), which is a closer echo of the original desktop app's
"nothing leaves your machine" pitch than a CDN-dependent page would be.

### Validation

No browser is available in this session to actually render and look at
the page (no Playwright/browser-automation tool, and this dev container
otherwise has no display). Validated everything that *could* be checked
without one, rather than skipping validation entirely:

- **Extracted and ran the actual embedded parsing code** (not a
  reimplementation — the real `parseSTL`/`parseBinarySTL`/`computeBounds`
  functions, pulled verbatim from the published file) in Node against
  the real embedded base64 example data, and confirmed both examples
  decode to exactly the expected real-world dimensions: the hole plate
  to a 40.00 × 40.00 × 5.00mm bounding box with 512 triangles, the peg
  cylinder to 12.00 × 12.00 × 15.00mm with 256 triangles — matching the
  Python side's own known values for these exact fixture files (r=20mm
  disk / r=6mm×h=15mm cylinder) and its previously-logged triangle
  counts for them.
- `node --check` on the full extracted script confirmed it's
  syntactically valid JavaScript (would have caught a typo the way a
  Python `py_compile` check would, though it can't catch a runtime or
  WebGL-specific bug the way actually rendering the page would).
- The camera/projection math (`mat4Perspective`, `mat4LookAt`,
  `mat4Multiply`) is a direct, standard column-major implementation
  (the same structure widely used in e.g. glMatrix) rather than
  something novel — lower risk than hand-derived math, but still
  unverified by actual rendering.

**Not validated: whether the page actually renders correctly in a real
browser.** This is a real gap, flagged rather than glossed over — the
parsing logic and matrix math are checked, but WebGL shader compilation,
the lighting result, and the orbit/pan/zoom feel have not been seen
rendered by anyone, including me. Asking you to open the published
Artifact link and report back what you see (or don't) is the
validation this phase is still missing.

### Key decisions and why

- **Z-up world convention**, matching the Python app's STL fixtures and
  the physical print-bed convention (bed = XY plane, nozzle travels up
  in Z) — not the Y-up convention common in general-purpose 3D engines
  (including three.js's default), which would have been a subtle,
  confusing mismatch for files meant to represent real 3D prints.
- **Example parts reused directly from the Python project's own test
  fixtures**, not new placeholder geometry — ties the web track back to
  geometry that's already been rigorously validated (Phase 2's exact
  radius detection, Phase 5's exact compensation math) rather than
  introducing an unrelated demo shape.
- **No pytest-equivalent test suite for the web track (yet).** The
  Python project's whole testing discipline (pytest, Xvfb-driven GUI
  tests) doesn't carry over directly to a browser artifact. Once later
  web phases add real logic worth regression-testing (feature detection
  in JS, compensation math), worth revisiting whether a headless-browser
  test setup is warranted — flagged as an open question, not decided
  here.

## Next up (Web track)

Phase W2 — feature detection in JS: port the segmentation (dihedral-angle
region growing) and cylinder/plane classification approach from
`interlock3d/core/feature_detection.py`, without a RANSAC library
(browser has no `pyransac3d` equivalent readily available under the same
CDN constraints described above) — likely a direct least-squares or
simple-RANSAC implementation written by hand, same reasoning as W1's
renderer. **Waiting for go-ahead, and — importantly — for confirmation
that W1 actually renders correctly for you first**, before building
further phases on top of an unverified foundation.
