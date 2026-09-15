"""Numeric accuracy report for Phase 2 feature detection.

Generates the hand-made known-dimension test parts, runs the detection
engine on each, greedily matches detected features to expected ones, and
prints a pass/fail table with the actual numeric error for every matched
value. Exits non-zero if anything fails or goes unmatched, so this can be
used as a CI gate as well as a human-readable report.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from interlock3d.core.feature_detection import DetectionConfig, detect_features  # noqa: E402
from interlock3d.core.mesh_loader import load_trimesh  # noqa: E402
from known_parts import ExpectedFeature, KnownPart, generate_known_parts  # noqa: E402


def _matches(expected: ExpectedFeature, detected) -> bool:
    if expected.feature_type != detected.feature_type:
        return False
    if expected.radius is not None:
        if detected.radius is None or abs(detected.radius - expected.radius) > expected.radius_tol:
            return False
    if expected.extent is not None:
        if detected.extent is None or abs(detected.extent - expected.extent) > expected.extent_tol:
            return False
    if expected.area is not None:
        tol = max(expected.area * expected.area_tol_frac, 1.0)
        if abs(detected.area - expected.area) > tol:
            return False
    return True


def _fmt_err(expected_val, detected_val) -> str:
    if expected_val is None or detected_val is None:
        return "-"
    return f"{detected_val - expected_val:+.4f}"


def validate_part(part: KnownPart) -> tuple[int, int, int]:
    print(f"\n=== {part.name} ===")
    print(part.description)

    mesh = load_trimesh(part.path)
    detected = detect_features(mesh, DetectionConfig())

    remaining = list(detected)
    n_pass = 0
    n_fail = 0

    for expected in part.expected:
        candidates = [d for d in remaining if _matches(expected, d)]
        if candidates:
            best = min(
                candidates,
                key=lambda d: abs((d.radius or 0) - (expected.radius or 0))
                + abs((d.extent or 0) - (expected.extent or 0))
                + abs((d.area or 0) - (expected.area or 0)),
            )
            remaining.remove(best)
            n_pass += 1
            inlier_str = f"{best.inlier_ratio:.3f}" if best.inlier_ratio is not None else "-"
            print(
                f"  PASS  {expected.describe():<28} "
                f"radius_err={_fmt_err(expected.radius, best.radius):<10} "
                f"extent_err={_fmt_err(expected.extent, best.extent):<10} "
                f"area_err={_fmt_err(expected.area, best.area):<10} "
                f"inlier_ratio={inlier_str}"
            )
        else:
            n_fail += 1
            print(f"  FAIL  {expected.describe():<28} no matching detected feature")

    n_extra = len(remaining)
    for leftover in remaining:
        print(f"  EXTRA (unexpected) detected feature: {leftover}")

    print(f"  -> {n_pass}/{len(part.expected)} expected features matched, {n_extra} unmatched detections")
    return n_pass, n_fail, n_extra


def main() -> int:
    parts = generate_known_parts()

    total_pass = total_fail = total_extra = 0
    for part in parts:
        n_pass, n_fail, n_extra = validate_part(part)
        total_pass += n_pass
        total_fail += n_fail
        total_extra += n_extra

    total_expected = total_pass + total_fail
    print("\n" + "=" * 60)
    print(
        f"TOTAL: {total_pass}/{total_expected} expected features matched "
        f"({100 * total_pass / total_expected:.1f}%), {total_extra} unexpected detections"
    )

    return 0 if (total_fail == 0 and total_extra == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
