"""Tests for the Phase 5 geometry modification engine.

Phase 5 is flagged in the brief as high-risk, so these don't just check
"did it run" -- every test checks the *resulting geometry* two ways:
1. Mesh integrity (watertight, winding-consistent, no degenerate faces)
   both before and after, so a failure clearly means "modification broke
   it" rather than "it was already broken."
2. A round-trip: re-run detect_features() on the MODIFIED mesh and
   confirm the new radius/extent/plane-position actually matches what
   was requested, not just that vertices moved *somewhere*.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from interlock3d.core.compensation import CompensationTable, compute_pair_compensation
from interlock3d.core.feature_detection import DetectionConfig, detect_features
from interlock3d.core.geometry_modification import (
    GeometryModificationError,
    apply_feature_adjustments,
    check_mesh_integrity,
    displace_feature_vertices,
)
from interlock3d.core.mesh_loader import load_trimesh
from interlock3d.core.pairing import FeaturePair, FeatureRef
from known_parts import KNOWN_DIR, generate_known_parts

generate_known_parts()


def _load(name: str):
    mesh = load_trimesh(KNOWN_DIR / f"{name}.stl")
    features = detect_features(mesh, DetectionConfig())
    return mesh, features


def _find(features, feature_type: str):
    for i, f in enumerate(features):
        if f.feature_type == feature_type:
            return i, f
    raise AssertionError(f"no {feature_type} feature in {features}")


# --- Core displacement correctness --------------------------------------


def test_enlarging_a_hole_increases_its_detected_radius() -> None:
    mesh, features = _load("plate_with_hole")
    idx, hole = _find(features, "hole")
    assert mesh.is_watertight

    delta = 0.25
    modified = apply_feature_adjustments(mesh, features, [(idx, delta)])

    integrity = check_mesh_integrity(modified)
    assert integrity.is_valid, f"hole enlargement broke mesh integrity: {integrity}"

    new_features = detect_features(modified, DetectionConfig())
    _, new_hole = _find(new_features, "hole")
    assert new_hole.radius == pytest.approx(hole.radius + delta, abs=0.02)
    # extent (through-hole depth) shouldn't change -- only radius should
    assert new_hole.extent == pytest.approx(hole.extent, abs=0.02)


def test_shrinking_a_peg_decreases_its_detected_radius() -> None:
    mesh, features = _load("boss_cylinder")
    idx, peg = _find(features, "peg")
    assert mesh.is_watertight

    delta = 0.25
    modified = apply_feature_adjustments(mesh, features, [(idx, delta)])

    integrity = check_mesh_integrity(modified)
    assert integrity.is_valid, f"peg shrinkage broke mesh integrity: {integrity}"

    new_features = detect_features(modified, DetectionConfig())
    _, new_peg = _find(new_features, "peg")
    assert new_peg.radius == pytest.approx(peg.radius - delta, abs=0.02)
    assert new_peg.extent == pytest.approx(peg.extent, abs=0.02)


def test_recessing_a_flat_face_moves_it_along_its_normal() -> None:
    mesh, features = _load("flat_plate")
    # top face: the one whose normal points +Z
    flat_indices = [i for i, f in enumerate(features) if f.feature_type == "flat"]
    idx = max(flat_indices, key=lambda i: features[i].axis[2])
    top = features[idx]
    assert mesh.is_watertight
    original_z_max = mesh.bounds[1][2]

    delta = 0.3
    modified = apply_feature_adjustments(mesh, features, [(idx, delta)])

    integrity = check_mesh_integrity(modified)
    assert integrity.is_valid, f"flat recession broke mesh integrity: {integrity}"

    # recessing the top face inward (along -Z, its own outward normal is
    # +Z) should lower the top of the bounding box by exactly delta
    new_z_max = modified.bounds[1][2]
    assert (original_z_max - new_z_max) == pytest.approx(delta, abs=1e-6)

    new_features = detect_features(modified, DetectionConfig())
    new_flats = [f for f in new_features if f.feature_type == "flat"]
    new_top = max(new_flats, key=lambda f: f.axis[2])
    assert new_top.area == pytest.approx(top.area, abs=1.0), "recessing shouldn't change the face's area"


def test_multiple_adjustments_on_one_part_compose() -> None:
    """plate_with_hole has both a hole (inner) and a peg-typed outer rim;
    applying both adjustments in one call should affect each
    independently without interference."""
    mesh, features = _load("plate_with_hole")
    hole_idx, hole = _find(features, "hole")
    rim_idx, rim = _find(features, "peg")

    hole_delta, rim_delta = 0.2, 0.3
    modified = apply_feature_adjustments(mesh, features, [(hole_idx, hole_delta), (rim_idx, rim_delta)])

    integrity = check_mesh_integrity(modified)
    assert integrity.is_valid, f"combined adjustments broke mesh integrity: {integrity}"

    new_features = detect_features(modified, DetectionConfig())
    _, new_hole = _find(new_features, "hole")
    _, new_rim = _find(new_features, "peg")
    assert new_hole.radius == pytest.approx(hole.radius + hole_delta, abs=0.02)
    assert new_rim.radius == pytest.approx(rim.radius - rim_delta, abs=0.02)


# --- Integrity checking actually has teeth --------------------------------


def test_oversized_hole_enlargement_is_caught_via_negative_volume() -> None:
    """There's no analytic upper bound on how much a hole can grow (unlike
    a peg, which can't shrink past its own radius) -- a Feature alone
    doesn't know where the surrounding material actually ends. So an
    excessive request doesn't raise; it produces geometry that has folded
    past the part's own outer boundary and inverted. Found by deliberately
    stress-testing this module: trimesh's is_watertight and
    is_winding_consistent both stay True in this case (topology never
    changed), so a caller trusting only those two would accept corrupted
    output. Volume sign is what actually catches it."""
    mesh, features = _load("plate_with_hole")  # hole r=5, outer r=20
    idx, hole = _find(features, "hole")

    reasonable = apply_feature_adjustments(mesh, features, [(idx, 1.0)])
    assert check_mesh_integrity(reasonable).is_valid

    excessive = apply_feature_adjustments(mesh, features, [(idx, 17.0)])  # new radius ~22 > outer r=20
    integrity = check_mesh_integrity(excessive)
    assert integrity.is_watertight
    assert integrity.is_winding_consistent
    assert not integrity.has_degenerate_faces
    assert integrity.has_negative_volume, "expected volume to go negative once the hole exceeds the outer radius"
    assert not integrity.is_valid


# --- Safety / error handling ---------------------------------------------


def test_shrinking_a_peg_past_zero_radius_is_rejected() -> None:
    mesh, features = _load("boss_cylinder")
    idx, peg = _find(features, "peg")

    with pytest.raises(GeometryModificationError):
        apply_feature_adjustments(mesh, features, [(idx, peg.radius + 0.5)])


def test_shrinking_a_peg_to_exactly_zero_radius_is_rejected() -> None:
    mesh, features = _load("boss_cylinder")
    idx, peg = _find(features, "peg")

    with pytest.raises(GeometryModificationError):
        apply_feature_adjustments(mesh, features, [(idx, peg.radius)])


def test_negative_material_removed_rejected() -> None:
    mesh, features = _load("boss_cylinder")
    idx, _ = _find(features, "peg")

    with pytest.raises(ValueError):
        displace_feature_vertices(mesh.vertices, mesh.faces, features[idx], -0.1)


def test_original_mesh_is_never_mutated() -> None:
    mesh, features = _load("boss_cylinder")
    idx, _ = _find(features, "peg")
    original_vertices = mesh.vertices.copy()

    apply_feature_adjustments(mesh, features, [(idx, 0.2)])

    assert np.array_equal(mesh.vertices, original_vertices)


# --- End-to-end: Phase 3 pair -> Phase 4 compensation -> Phase 5 apply ---


def test_full_pipeline_hole_and_peg_pair_achieves_target_clearance() -> None:
    """A realistic hole+peg pair (plate_with_matched_hole's 6mm hole,
    boss_cylinder's 6mm peg -- same nominal size, unlike plate_with_hole
    which is deliberately mismatched for detection-only tests) run
    through the actual Phase 3->4->5 pipeline: confirm a pair, compute
    its compensation, apply it to both parts' meshes independently, and
    verify the resulting radial gap matches the target clearance."""
    hole_mesh, hole_features = _load("plate_with_matched_hole")
    peg_mesh, peg_features = _load("boss_cylinder")
    hole_idx, hole = _find(hole_features, "hole")
    peg_idx, peg = _find(peg_features, "peg")
    assert hole.radius == pytest.approx(peg.radius, abs=0.02), "fixture assumption: same nominal radius"

    pair = FeaturePair(
        FeatureRef("plate_with_matched_hole", hole_idx),
        FeatureRef("boss_cylinder", peg_idx),
        fit_type="sliding",
    )
    table = CompensationTable.load()
    comp = compute_pair_compensation(pair, "hole", "peg", material="PLA", table=table)

    hole_adj = comp.adjustment_a if comp.adjustment_a.feature_type == "hole" else comp.adjustment_b
    peg_adj = comp.adjustment_a if comp.adjustment_a.feature_type == "peg" else comp.adjustment_b

    new_hole_mesh = apply_feature_adjustments(hole_mesh, hole_features, [(hole_idx, hole_adj.material_removed_mm)])
    new_peg_mesh = apply_feature_adjustments(peg_mesh, peg_features, [(peg_idx, peg_adj.material_removed_mm)])

    assert check_mesh_integrity(new_hole_mesh).is_watertight
    assert check_mesh_integrity(new_peg_mesh).is_watertight

    new_hole = _find(detect_features(new_hole_mesh, DetectionConfig()), "hole")[1]
    new_peg = _find(detect_features(new_peg_mesh, DetectionConfig()), "peg")[1]

    original_gap = hole.radius - peg.radius  # ~0, matched fixture
    new_gap = new_hole.radius - new_peg.radius
    assert (new_gap - original_gap) == pytest.approx(comp.total_clearance_mm, abs=0.03)
