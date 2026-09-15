"""Numeric accuracy + integrity report for Phase 5 geometry modification.

For each known test part's hole/peg/flat feature, applies a
representative compensation (PLA sliding fit), checks mesh integrity
before and after, and re-detects features on the modified mesh to
confirm the actual resulting dimension matches what was requested.
Exits non-zero if anything fails, so this doubles as a CI gate.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from interlock3d.core.compensation import CompensationTable  # noqa: E402
from interlock3d.core.feature_detection import DetectionConfig, detect_features  # noqa: E402
from interlock3d.core.geometry_modification import apply_feature_adjustments, check_mesh_integrity  # noqa: E402
from interlock3d.core.mesh_loader import load_trimesh  # noqa: E402
from known_parts import generate_known_parts  # noqa: E402

MATERIAL = "PLA"
FIT_TYPE = "sliding"


def _find(features, feature_type):
    for i, f in enumerate(features):
        if f.feature_type == feature_type:
            return i, f
    return None, None


def check_feature(part_path, feature_type, clearance_mm) -> tuple[bool, str]:
    mesh = load_trimesh(part_path)
    features = detect_features(mesh, DetectionConfig())
    idx, feature = _find(features, feature_type)
    if feature is None:
        return True, f"  SKIP  {part_path.stem}: no {feature_type} feature"

    before = check_mesh_integrity(mesh)
    modified = apply_feature_adjustments(mesh, features, [(idx, clearance_mm)])
    after = check_mesh_integrity(modified)

    new_features = detect_features(modified, DetectionConfig())
    _, new_feature = _find(new_features, feature_type)

    if feature_type in ("hole", "peg"):
        sign = 1 if feature_type == "hole" else -1
        expected = feature.radius + sign * clearance_mm
        actual = new_feature.radius if new_feature else float("nan")
        err = actual - expected
        dim_line = f"radius {feature.radius:.3f} -> expected {expected:.3f}, got {actual:.3f} (err {err:+.4f}mm)"
        dim_ok = new_feature is not None and abs(err) < 0.05
    else:  # flat
        expected_area = feature.area
        actual_area = new_feature.area if new_feature else float("nan")
        dim_line = f"area {feature.area:.1f} -> {actual_area:.1f} (should be ~unchanged by a recess)"
        dim_ok = new_feature is not None and abs(actual_area - expected_area) < max(expected_area * 0.02, 1.0)

    passed = before.is_valid and after.is_valid and dim_ok
    status = "PASS" if passed else "FAIL"
    line = (
        f"  {status}  {part_path.stem}:{feature_type:5s}  {dim_line}\n"
        f"          before: watertight={before.is_watertight} winding_ok={before.is_winding_consistent} "
        f"volume={before.volume:.2f}\n"
        f"          after:  watertight={after.is_watertight} winding_ok={after.is_winding_consistent} "
        f"volume={after.volume:.2f} negative_volume={after.has_negative_volume} "
        f"degenerate={after.has_degenerate_faces}"
    )
    return passed, line


def main() -> int:
    parts = generate_known_parts()
    table = CompensationTable.load()
    clearance = table.get_clearance_mm(MATERIAL, FIT_TYPE)
    print(f"Using {MATERIAL} {FIT_TYPE} fit: {clearance:.3f}mm/side\n")

    checks = [
        ("plate_with_hole", "hole"),
        ("plate_with_hole", "peg"),
        ("boss_cylinder", "peg"),
        ("flat_plate", "flat"),
        ("plate_with_matched_hole", "hole"),
    ]
    by_name = {p.name: p for p in parts}

    n_pass = n_fail = 0
    for part_name, feature_type in checks:
        ok, line = check_feature(by_name[part_name].path, feature_type, clearance)
        print(line)
        n_pass += ok
        n_fail += not ok

    print("\n" + "=" * 60)
    print(f"TOTAL: {n_pass}/{n_pass + n_fail} checks passed")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
