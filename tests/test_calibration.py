"""Tests for the Phase 7 calibration engine.

The key validation here is a "digital twin" of the real print-and-measure
workflow: generate the calibration part, apply a KNOWN synthetic
printer error to it (Phase 5's own vertex-displacement machinery, used
here to stand in for what a real, imperfect printer would produce), then
re-detect features on the "printed" mesh to get simulated caliper
readings, and confirm the derived calibration profile recovers exactly
the error that was injected. That's a much stronger claim than just
checking the arithmetic in isolation -- it validates that the whole
generate -> measure -> derive loop is self-consistent.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from interlock3d.core.calibration import (
    CalibrationError,
    CalibrationMeasurement,
    compute_calibrated_pair_compensation,
    compute_calibration_profile,
    generate_calibration_part,
    load_calibration_profile,
    save_calibration_profile,
)
from interlock3d.core.compensation import CompensationTable
from interlock3d.core.feature_detection import DetectionConfig, detect_features
from interlock3d.core.geometry_modification import apply_feature_adjustments, check_mesh_integrity
from interlock3d.core.mesh_loader import load_trimesh
from interlock3d.core.pairing import FeaturePair, FeatureRef
from known_parts import KNOWN_DIR, generate_known_parts

generate_known_parts()


def _find(features, feature_type):
    for i, f in enumerate(features):
        if f.feature_type == feature_type:
            return i, f
    raise AssertionError(f"no {feature_type} in {features}")


def _find_test_peg(features, nominal_radius_mm):
    """The calibration part's outer rim (the hole-plate's own edge) is
    ALSO a convex cylindrical surface and detects as feature_type "peg",
    same as the real test peg -- and being larger, it has more area and
    sorts first. Disambiguate by picking whichever "peg" is closest to
    the nominal radius, rather than just the first match."""
    pegs = [(i, f) for i, f in enumerate(features) if f.feature_type == "peg"]
    if not pegs:
        raise AssertionError(f"no peg in {features}")
    return min(pegs, key=lambda pair: abs(pair[1].radius - nominal_radius_mm))


# --- generate_calibration_part --------------------------------------------


def test_generated_part_has_expected_hole_and_peg() -> None:
    nominal = 5.0
    mesh = generate_calibration_part(nominal_radius_mm=nominal)
    assert check_mesh_integrity(mesh).is_valid

    features = detect_features(mesh, DetectionConfig())
    _, hole = _find(features, "hole")
    _, peg = _find_test_peg(features, nominal)
    assert hole.radius == pytest.approx(nominal, abs=0.02)
    assert peg.radius == pytest.approx(nominal, abs=0.02)


def test_generate_calibration_part_rejects_bad_dimensions() -> None:
    with pytest.raises(ValueError):
        generate_calibration_part(nominal_radius_mm=0)
    with pytest.raises(ValueError):
        generate_calibration_part(nominal_radius_mm=-1)
    with pytest.raises(ValueError):
        generate_calibration_part(nominal_radius_mm=10, plate_radius_mm=5)  # plate must be bigger than the hole


# --- digital-twin round trip: known error in, exact bias out --------------


@pytest.mark.parametrize(
    "hole_shrink_mm,peg_growth_mm",
    [
        (0.15, 0.10),  # typical case: hole undersized, peg oversized
        (0.05, 0.05),
        (0.30, 0.02),
        (0.0, 0.0),  # a hypothetically perfect printer: zero bias
    ],
)
def test_calibration_recovers_injected_printer_error_exactly(hole_shrink_mm, peg_growth_mm) -> None:
    nominal = 5.0
    mesh = generate_calibration_part(nominal_radius_mm=nominal)
    features = detect_features(mesh, DetectionConfig())
    hole_idx, _ = _find(features, "hole")
    peg_idx, _ = _find_test_peg(features, nominal)

    # Simulate "printing": a hole that shrinks and a peg that grows,
    # exactly like a real under-extruding-on-inner-walls printer would --
    # using Phase 5's real displacement math, not a hand-rolled stand-in.
    # Note the *signs*: to make the hole read SMALLER than nominal we
    # shrink it like a peg would be shrunk (radially inward); to make the
    # peg read LARGER we grow it like a hole would be grown (radially
    # outward). displace_feature_vertices' direction is keyed off
    # feature.feature_type, so we fake the type for this one call only.
    import dataclasses

    fake_shrinking_hole = dataclasses.replace(features[hole_idx], feature_type="peg")
    fake_growing_peg = dataclasses.replace(features[peg_idx], feature_type="hole")

    vertices = mesh.vertices.copy()
    from interlock3d.core.geometry_modification import displace_feature_vertices

    if hole_shrink_mm > 0:
        vertices = displace_feature_vertices(vertices, mesh.faces, fake_shrinking_hole, hole_shrink_mm)
    if peg_growth_mm > 0:
        vertices = displace_feature_vertices(vertices, mesh.faces, fake_growing_peg, peg_growth_mm)

    simulated_print = mesh.copy()
    simulated_print.vertices = vertices
    assert check_mesh_integrity(simulated_print).is_valid

    # Simulate "measuring with calipers": re-detect on the printed mesh.
    printed_features = detect_features(simulated_print, DetectionConfig())
    _, printed_hole = _find(printed_features, "hole")
    _, printed_peg = _find_test_peg(printed_features, nominal + peg_growth_mm)

    measurement = CalibrationMeasurement(
        nominal_radius_mm=nominal,
        measured_hole_diameter_mm=printed_hole.radius * 2,
        measured_peg_diameter_mm=printed_peg.radius * 2,
    )
    profile = compute_calibration_profile(measurement, label="digital twin test")

    assert profile.hole_radius_bias_mm == pytest.approx(hole_shrink_mm, abs=0.02)
    assert profile.peg_radius_bias_mm == pytest.approx(peg_growth_mm, abs=0.02)
    assert profile.nominal_radius_mm == nominal


def test_atypical_oversized_hole_gives_negative_bias() -> None:
    """An unusual printer whose holes print bigger than nominal (not the
    normal case, but should still be handled, not crash)."""
    measurement = CalibrationMeasurement(
        nominal_radius_mm=5.0, measured_hole_diameter_mm=10.4, measured_peg_diameter_mm=10.0
    )
    profile = compute_calibration_profile(measurement)
    assert profile.hole_radius_bias_mm == pytest.approx(-0.2, abs=1e-6)


# --- persistence -----------------------------------------------------------


def test_save_and_load_round_trip(tmp_path) -> None:
    measurement = CalibrationMeasurement(
        nominal_radius_mm=5.0, measured_hole_diameter_mm=9.7, measured_peg_diameter_mm=10.2
    )
    profile = compute_calibration_profile(measurement, label="my printer")
    path = tmp_path / "calibration.json"

    saved_path = save_calibration_profile(profile, path)
    assert saved_path == path
    assert path.exists()

    loaded = load_calibration_profile(path)
    assert loaded == profile


def test_load_missing_file_returns_none(tmp_path) -> None:
    assert load_calibration_profile(tmp_path / "does_not_exist.json") is None


def test_load_corrupt_file_raises(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{not valid json")
    with pytest.raises(CalibrationError):
        load_calibration_profile(path)


def test_load_malformed_profile_raises(tmp_path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('{"unexpected_field": 1}')
    with pytest.raises(CalibrationError):
        load_calibration_profile(path)


# --- compute_calibrated_pair_compensation ----------------------------------


@pytest.fixture
def hole_peg_pair():
    return FeaturePair(FeatureRef("plate", 0), FeatureRef("boss", 0), fit_type="sliding")


def test_calibrated_compensation_adds_bias_on_top_of_base_split(hole_peg_pair) -> None:
    table = CompensationTable.load()
    clearance = table.get_clearance_mm("PLA", "sliding")
    measurement = CalibrationMeasurement(
        nominal_radius_mm=5.0, measured_hole_diameter_mm=9.7, measured_peg_diameter_mm=10.2
    )
    profile = compute_calibration_profile(measurement)  # hole bias +0.15, peg bias +0.10

    result = compute_calibrated_pair_compensation(hole_peg_pair, "hole", "peg", "PLA", profile, table=table)

    assert result.adjustment_a.material_removed_mm == pytest.approx(clearance / 2 + profile.hole_radius_bias_mm)
    assert result.adjustment_b.material_removed_mm == pytest.approx(clearance / 2 + profile.peg_radius_bias_mm)
    # totals still reflect the real target clearance, unaffected by calibration bookkeeping
    assert result.total_clearance_mm == pytest.approx(clearance)


def test_calibrated_compensation_clamps_negative_result_to_zero(hole_peg_pair) -> None:
    # An extreme negative bias (hole prints far OVERSIZED) shouldn't drive
    # material_removed_mm negative -- that violates the invariant enforced
    # downstream by geometry_modification.
    from interlock3d.core.calibration import CalibrationProfile

    huge_negative_bias = CalibrationProfile(
        nominal_radius_mm=5.0, hole_radius_bias_mm=-5.0, peg_radius_bias_mm=0.0, created_at="2024-01-01T00:00:00"
    )
    result = compute_calibrated_pair_compensation(hole_peg_pair, "hole", "peg", "PLA", huge_negative_bias)
    assert result.adjustment_a.material_removed_mm == 0.0


def test_calibrated_compensation_leaves_flat_pairs_unchanged(hole_peg_pair) -> None:
    flat_pair = FeaturePair(FeatureRef("lid", 0), FeatureRef("box", 1), fit_type="sliding")
    table = CompensationTable.load()
    measurement = CalibrationMeasurement(
        nominal_radius_mm=5.0, measured_hole_diameter_mm=9.7, measured_peg_diameter_mm=10.2
    )
    profile = compute_calibration_profile(measurement)

    from interlock3d.core.compensation import compute_pair_compensation

    calibrated = compute_calibrated_pair_compensation(flat_pair, "flat", "flat", "PLA", profile, table=table)
    uncalibrated = compute_pair_compensation(flat_pair, "flat", "flat", "PLA", table=table)

    assert calibrated.adjustment_a.material_removed_mm == pytest.approx(
        uncalibrated.adjustment_a.material_removed_mm
    )
    assert calibrated.adjustment_b.material_removed_mm == pytest.approx(
        uncalibrated.adjustment_b.material_removed_mm
    )


# --- full pipeline: calibration part -> profile -> applied to a DIFFERENT part


def test_calibration_profile_applies_correctly_to_a_different_pair() -> None:
    """The calibration test part is a separate, throwaway artifact -- the
    derived profile needs to correctly generalize to a real pair of
    parts, not just describe the calibration part itself."""
    # Derive a profile from the calibration part with a known injected error.
    cal_mesh = generate_calibration_part(nominal_radius_mm=5.0)
    cal_features = detect_features(cal_mesh, DetectionConfig())
    hole_idx, _ = _find(cal_features, "hole")
    peg_idx, _ = _find_test_peg(cal_features, 5.0)

    import dataclasses

    from interlock3d.core.geometry_modification import displace_feature_vertices

    fake_shrinking_hole = dataclasses.replace(cal_features[hole_idx], feature_type="peg")
    fake_growing_peg = dataclasses.replace(cal_features[peg_idx], feature_type="hole")
    vertices = displace_feature_vertices(cal_mesh.vertices, cal_mesh.faces, fake_shrinking_hole, 0.15)
    vertices = displace_feature_vertices(vertices, cal_mesh.faces, fake_growing_peg, 0.10)
    printed = cal_mesh.copy()
    printed.vertices = vertices

    printed_features = detect_features(printed, DetectionConfig())
    measurement = CalibrationMeasurement(
        nominal_radius_mm=5.0,
        measured_hole_diameter_mm=_find(printed_features, "hole")[1].radius * 2,
        measured_peg_diameter_mm=_find_test_peg(printed_features, 5.1)[1].radius * 2,
    )
    profile = compute_calibration_profile(measurement)

    # Now apply that profile to a genuinely different hole+peg pair.
    hole_mesh = load_trimesh(KNOWN_DIR / "plate_with_matched_hole.stl")
    peg_mesh = load_trimesh(KNOWN_DIR / "boss_cylinder.stl")
    hole_features = detect_features(hole_mesh, DetectionConfig())
    peg_features = detect_features(peg_mesh, DetectionConfig())
    real_hole_idx, real_hole = _find(hole_features, "hole")
    real_peg_idx, real_peg = _find(peg_features, "peg")

    pair = FeaturePair(
        FeatureRef("plate_with_matched_hole", real_hole_idx), FeatureRef("boss_cylinder", real_peg_idx), "sliding"
    )
    table = CompensationTable.load()
    result = compute_calibrated_pair_compensation(pair, "hole", "peg", "PLA", profile, table=table)

    new_hole_mesh = apply_feature_adjustments(
        hole_mesh, hole_features, [(real_hole_idx, result.adjustment_a.material_removed_mm)]
    )
    new_peg_mesh = apply_feature_adjustments(
        peg_mesh, peg_features, [(real_peg_idx, result.adjustment_b.material_removed_mm)]
    )
    assert check_mesh_integrity(new_hole_mesh).is_valid
    assert check_mesh_integrity(new_peg_mesh).is_valid

    new_hole = _find(detect_features(new_hole_mesh, DetectionConfig()), "hole")[1]
    new_peg = _find(detect_features(new_peg_mesh, DetectionConfig()), "peg")[1]

    clearance = table.get_clearance_mm("PLA", "sliding")
    expected_hole_radius = real_hole.radius + clearance / 2 + profile.hole_radius_bias_mm
    expected_peg_radius = real_peg.radius - (clearance / 2 + profile.peg_radius_bias_mm)
    assert new_hole.radius == pytest.approx(expected_hole_radius, abs=0.03)
    assert new_peg.radius == pytest.approx(expected_peg_radius, abs=0.03)
