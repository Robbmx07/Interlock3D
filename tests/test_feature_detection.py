"""Regression tests for Phase 2 feature detection, against known-dimension
hand-made test parts (see `known_parts.py`).

`scripts/validate_phase2.py` is the human-readable accuracy report; this
file pins the same checks as automated tests so a regression fails CI /
`pytest` immediately.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from interlock3d.core.feature_detection import DetectionConfig, detect_features
from interlock3d.core.mesh_loader import load_trimesh
from interlock3d.core.segmentation import segment_smooth_patches
from known_parts import KnownPart, generate_known_parts

ALL_PARTS = generate_known_parts()


def _find_best_match(expected, detected_list):
    candidates = [d for d in detected_list if d.feature_type == expected.feature_type]
    if expected.radius is not None:
        candidates = [d for d in candidates if d.radius is not None]
    if not candidates:
        return None
    return min(
        candidates,
        key=lambda d: abs((d.radius or 0) - (expected.radius or 0))
        + abs((d.extent or 0) - (expected.extent or 0))
        + abs((d.area or 0) - (expected.area or 0)),
    )


@pytest.mark.parametrize("part", ALL_PARTS, ids=lambda p: p.name)
def test_known_part_feature_counts(part: KnownPart) -> None:
    mesh = load_trimesh(part.path)
    detected = detect_features(mesh, DetectionConfig())
    assert len(detected) == len(part.expected), (
        f"{part.name}: expected {len(part.expected)} features, got {len(detected)}: {detected}"
    )


@pytest.mark.parametrize("part", ALL_PARTS, ids=lambda p: p.name)
def test_known_part_feature_values(part: KnownPart) -> None:
    mesh = load_trimesh(part.path)
    detected = list(detect_features(mesh, DetectionConfig()))

    for expected in part.expected:
        match = _find_best_match(expected, detected)
        assert match is not None, f"{part.name}: no detection matched expected {expected.describe()}"
        detected.remove(match)

        if expected.radius is not None:
            assert match.radius == pytest.approx(expected.radius, abs=expected.radius_tol)
        if expected.extent is not None:
            assert match.extent == pytest.approx(expected.extent, abs=expected.extent_tol)
        if expected.area is not None:
            tol = max(expected.area * expected.area_tol_frac, 1.0)
            assert match.area == pytest.approx(expected.area, abs=tol)


def test_hole_vs_peg_convexity() -> None:
    """A through-hole must read concave; the disk's own rim must read convex."""
    mesh = trimesh.creation.annulus(r_min=5, r_max=20, height=5, sections=64)
    detected = detect_features(mesh, DetectionConfig())
    by_type = {f.feature_type for f in detected}
    assert "hole" in by_type
    assert "peg" in by_type


def test_detection_is_rotation_invariant() -> None:
    rng = np.random.default_rng(42)
    cyl = trimesh.creation.cylinder(radius=6.0, height=15.0, sections=64)

    axis = rng.normal(size=3)
    axis /= np.linalg.norm(axis)
    cyl.apply_transform(trimesh.transformations.rotation_matrix(rng.uniform(0, 2 * np.pi), axis))
    cyl.apply_translation(rng.uniform(-50, 50, size=3))

    detected = detect_features(cyl, DetectionConfig())
    pegs = [f for f in detected if f.feature_type == "peg"]
    assert len(pegs) == 1
    assert pegs[0].radius == pytest.approx(6.0, abs=0.05)
    assert pegs[0].extent == pytest.approx(15.0, abs=0.05)


def test_low_tessellation_cylinder_is_a_known_limitation() -> None:
    """Below ~16 sides the panel-to-panel angle exceeds the smoothness
    threshold and the wall segments into separate flats instead of one
    cylinder. Documented in PROGRESS.md; this test just pins the boundary
    so a future change to the threshold is a deliberate choice, not a
    silent regression."""
    coarse = trimesh.creation.cylinder(radius=6.0, height=15.0, sections=8)
    detected = detect_features(coarse, DetectionConfig())
    assert all(f.feature_type == "flat" for f in detected)

    fine_enough = trimesh.creation.cylinder(radius=6.0, height=15.0, sections=16)
    detected_fine = detect_features(fine_enough, DetectionConfig())
    assert any(f.feature_type == "peg" for f in detected_fine)


def test_segmentation_splits_at_sharp_edges_not_within_smooth_wall() -> None:
    box = trimesh.creation.box(extents=(10, 10, 10))
    patches = segment_smooth_patches(box, angle_threshold_deg=35.0)
    # 6 faces, each made of 2 coplanar triangles -> 6 patches of 2 faces each.
    assert len(patches) == 6
    assert all(len(p) == 2 for p in patches)

    cyl = trimesh.creation.cylinder(radius=6.0, height=15.0, sections=64)
    patches = segment_smooth_patches(cyl, angle_threshold_deg=35.0)
    # top cap, bottom cap, and one merged wall patch.
    assert len(patches) == 3
