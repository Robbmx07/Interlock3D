"""Tests for the Phase 6 export/report pipeline: confirmed pairs (Phase
3) -> compensation (Phase 4) -> applied geometry (Phase 5) -> files +
report (Phase 6), all chained together.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from interlock3d.core.compensation import CompensationTable
from interlock3d.core.export import build_export_plan, export_plan, format_report
from interlock3d.core.feature_detection import DetectionConfig, detect_features
from interlock3d.core.geometry_modification import check_mesh_integrity
from interlock3d.core.mesh_loader import load_trimesh
from interlock3d.core.pairing import FeaturePair, FeatureRef
from known_parts import KNOWN_DIR, generate_known_parts

generate_known_parts()


def _find(features, feature_type):
    for i, f in enumerate(features):
        if f.feature_type == feature_type:
            return i, f
    raise AssertionError(f"no {feature_type} in {features}")


@pytest.fixture
def matched_hole_and_peg():
    hole_mesh = load_trimesh(KNOWN_DIR / "plate_with_matched_hole.stl")
    peg_mesh = load_trimesh(KNOWN_DIR / "boss_cylinder.stl")
    hole_features = detect_features(hole_mesh, DetectionConfig())
    peg_features = detect_features(peg_mesh, DetectionConfig())
    hole_idx, hole = _find(hole_features, "hole")
    peg_idx, peg = _find(peg_features, "peg")

    parts = {"plate_with_matched_hole": hole_mesh, "boss_cylinder": peg_mesh}
    features_by_part = {"plate_with_matched_hole": hole_features, "boss_cylinder": peg_features}
    pair = FeaturePair(
        FeatureRef("plate_with_matched_hole", hole_idx),
        FeatureRef("boss_cylinder", peg_idx),
        fit_type="sliding",
    )
    return parts, features_by_part, pair, hole, peg


def test_plan_with_no_pairs_leaves_parts_unmodified(matched_hole_and_peg) -> None:
    parts, features_by_part, _pair, _hole, _peg = matched_hole_and_peg
    plan = build_export_plan([], parts, features_by_part, material="PLA")

    assert plan.changes == []
    assert plan.integrity == {}
    assert set(plan.modified_meshes) == set(parts)
    for name in parts:
        assert plan.modified_meshes[name].vertices.shape == parts[name].vertices.shape


def test_plan_with_one_pair_produces_two_change_records(matched_hole_and_peg) -> None:
    parts, features_by_part, pair, hole, peg = matched_hole_and_peg
    table = CompensationTable.load()
    clearance = table.get_clearance_mm("PLA", "sliding")

    plan = build_export_plan([pair], parts, features_by_part, material="PLA", table=table)

    assert len(plan.changes) == 2
    hole_change = next(c for c in plan.changes if c.feature_type == "hole")
    peg_change = next(c for c in plan.changes if c.feature_type == "peg")

    assert hole_change.original_radius_mm == pytest.approx(hole.radius)
    assert hole_change.new_radius_mm == pytest.approx(hole.radius + clearance / 2)
    assert peg_change.original_radius_mm == pytest.approx(peg.radius)
    assert peg_change.new_radius_mm == pytest.approx(peg.radius - clearance / 2)

    assert hole_change.paired_part_name == "boss_cylinder"
    assert peg_change.paired_part_name == "plate_with_matched_hole"
    assert hole_change.fit_type == "sliding"
    assert hole_change.material == "PLA"


def test_plan_applies_geometry_and_reports_valid_integrity(matched_hole_and_peg) -> None:
    parts, features_by_part, pair, hole, peg = matched_hole_and_peg
    plan = build_export_plan([pair], parts, features_by_part, material="PLA")

    assert set(plan.integrity) == {"plate_with_matched_hole", "boss_cylinder"}
    for report in plan.integrity.values():
        assert report.is_valid
    assert not plan.has_integrity_problems
    assert plan.problem_parts() == []

    new_hole_features = detect_features(plan.modified_meshes["plate_with_matched_hole"], DetectionConfig())
    _, new_hole = _find(new_hole_features, "hole")
    assert new_hole.radius > hole.radius

    new_peg_features = detect_features(plan.modified_meshes["boss_cylinder"], DetectionConfig())
    _, new_peg = _find(new_peg_features, "peg")
    assert new_peg.radius < peg.radius


def test_plan_flags_integrity_problem_for_excessive_clearance(matched_hole_and_peg) -> None:
    """A deliberately absurd fit clearance (built by hand, not from the
    real profile table) should be caught by the integrity report, mirroring
    Phase 5's negative-volume finding -- confirms build_export_plan surfaces
    that instead of hiding it."""
    parts, features_by_part, pair, hole, _peg = matched_hole_and_peg
    huge_table = CompensationTable(
        {
            "materials": {
                "PLA": {
                    "sliding": {"clearance_per_side_mm": 40.0, "clearance_range_mm": [40.0, 40.0]},
                }
            }
        }
    )

    with pytest.raises(Exception):
        # a peg shrink of 20mm on a 6mm-radius peg is geometrically invalid
        # and rejected before ever producing a mesh -- confirms the plan
        # builder doesn't swallow a GeometryModificationError silently
        build_export_plan([pair], parts, features_by_part, material="PLA", table=huge_table)


def test_report_text_mentions_every_change(matched_hole_and_peg) -> None:
    parts, features_by_part, pair, _hole, _peg = matched_hole_and_peg
    plan = build_export_plan([pair], parts, features_by_part, material="PLA")
    report = format_report(plan)

    assert "PLA" in report
    assert "plate_with_matched_hole" in report
    assert "boss_cylinder" in report
    assert "hole radius" in report
    assert "peg radius" in report
    assert "OK" in report  # integrity status for both parts


def test_report_text_for_no_pairs_says_so(matched_hole_and_peg) -> None:
    parts, features_by_part, _pair, _hole, _peg = matched_hole_and_peg
    plan = build_export_plan([], parts, features_by_part, material="PLA")
    report = format_report(plan)
    assert "unmodified" in report.lower()


def test_export_writes_one_stl_per_part_plus_report(matched_hole_and_peg, tmp_path) -> None:
    parts, features_by_part, pair, _hole, _peg = matched_hole_and_peg
    plan = build_export_plan([pair], parts, features_by_part, material="PLA")

    written = export_plan(plan, tmp_path)

    assert (tmp_path / "plate_with_matched_hole_corrected.stl").exists()
    assert (tmp_path / "boss_cylinder_corrected.stl").exists()
    assert (tmp_path / "compensation_report.txt").exists()
    assert written["__report__"] == tmp_path / "compensation_report.txt"

    # exported files should actually load back and reflect the modification
    reloaded = load_trimesh(tmp_path / "plate_with_matched_hole_corrected.stl")
    reloaded_features = detect_features(reloaded, DetectionConfig())
    _, reloaded_hole = _find(reloaded_features, "hole")
    assert reloaded_hole.radius > _hole.radius
    assert check_mesh_integrity(reloaded).is_valid


def test_export_never_overwrites_input_filenames(matched_hole_and_peg, tmp_path) -> None:
    """Exporting into the same directory the input STLs live in must not
    silently clobber the originals -- every exported file gets `_corrected`
    even when a part had no changes applied. Uses a copy of the fixtures
    in tmp_path (not the real, tracked test_data/known/ directory) so the
    test doesn't leave export artifacts in version control."""
    import shutil

    for filename in ("plate_with_matched_hole.stl", "boss_cylinder.stl"):
        shutil.copy(KNOWN_DIR / filename, tmp_path / filename)

    parts, features_by_part, _pair, _hole, _peg = matched_hole_and_peg
    plan = build_export_plan([], parts, features_by_part, material="PLA")

    written = export_plan(plan, tmp_path)  # same dir the "input" copies live in
    for name, path in written.items():
        if name == "__report__":
            continue
        assert path.name != f"{name}.stl"
        assert path.name == f"{name}_corrected.stl"

    # the original (copied) input files must be untouched
    original = load_trimesh(tmp_path / "boss_cylinder.stl")
    assert original.is_watertight
    assert original.vertices.shape == parts["boss_cylinder"].vertices.shape
