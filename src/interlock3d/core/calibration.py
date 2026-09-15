"""Phase 7: calibration wizard engine.

Generic compensation profiles (Phase 4) assume the printer prints
exactly at nominal size, and the only thing added is the fit type's
intended functional clearance. Real printers don't: holes typically
print a bit undersized and pegs a bit oversized, by an amount specific
to that printer/material/nozzle combination. This module measures that
bias from one small test print and layers it on top of Phase 4's
generic compensation, rather than replacing it -- the fit-type
philosophy (press/sliding/clearance) stays the same, calibration just
corrects for this printer's specific dimensional error.

Flow: `generate_calibration_part()` -> user prints and measures it with
calipers -> `compute_calibration_profile()` turns that measurement into
a `CalibrationProfile` -> `save_calibration_profile()` persists it (this
is an offline app, so calibration has to survive between sessions) ->
`compute_calibrated_pair_compensation()` uses it for future exports.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

import trimesh

from interlock3d.core.compensation import (
    CompensationTable,
    FeatureAdjustment,
    PairCompensation,
    compute_pair_compensation,
)
from interlock3d.core.pairing import FeaturePair, FitType

DEFAULT_CALIBRATION_PATH = Path.home() / ".interlock3d" / "calibration.json"


class CalibrationError(RuntimeError):
    """Raised for a malformed/unreadable saved calibration profile."""


def generate_calibration_part(
    nominal_radius_mm: float = 5.0,
    plate_radius_mm: float = 15.0,
    thickness_mm: float = 4.0,
    peg_height_mm: float = 8.0,
    gap_mm: float = 8.0,
    sections: int = 64,
) -> trimesh.Trimesh:
    """A small test print: one through-hole and one free-standing peg,
    both at `nominal_radius_mm`, positioned apart in a single STL so they
    print together in one job. Deliberately just one size (not a size
    ladder) to keep the print small and the workflow to one measurement
    per feature -- the brief calls for "a small calibration test STL".
    """
    if nominal_radius_mm <= 0:
        raise ValueError(f"nominal_radius_mm must be positive, got {nominal_radius_mm}")
    if plate_radius_mm <= nominal_radius_mm:
        raise ValueError("plate_radius_mm must be larger than nominal_radius_mm (need material around the hole)")

    hole_part = trimesh.creation.annulus(
        r_min=nominal_radius_mm, r_max=plate_radius_mm, height=thickness_mm, sections=sections
    )
    peg_part = trimesh.creation.cylinder(radius=nominal_radius_mm, height=peg_height_mm, sections=sections)

    separation = plate_radius_mm + gap_mm + nominal_radius_mm
    hole_part.apply_translation([-separation / 2, 0, 0])
    peg_part.apply_translation([separation / 2, 0, 0])

    return trimesh.util.concatenate([hole_part, peg_part])


@dataclass(frozen=True)
class CalibrationMeasurement:
    """What the user enters after printing and measuring the test part
    with calipers. Diameters, not radii -- that's what a caliper reads."""

    nominal_radius_mm: float
    measured_hole_diameter_mm: float
    measured_peg_diameter_mm: float

    def hole_radius_bias_mm(self) -> float:
        """Positive = the hole printed smaller than nominal (the usual
        case) -- this much extra radius is needed just to reach the
        TRUE nominal size, before any functional clearance is added."""
        return self.nominal_radius_mm - (self.measured_hole_diameter_mm / 2.0)

    def peg_radius_bias_mm(self) -> float:
        """Positive = the peg printed larger than nominal (the usual case)."""
        return (self.measured_peg_diameter_mm / 2.0) - self.nominal_radius_mm


@dataclass(frozen=True)
class CalibrationProfile:
    nominal_radius_mm: float
    hole_radius_bias_mm: float
    peg_radius_bias_mm: float
    created_at: str
    """ISO 8601 timestamp, informational only."""
    label: str = ""
    """Optional free-text note, e.g. "Ender 3, PLA, 0.4mm nozzle"."""


def compute_calibration_profile(measurement: CalibrationMeasurement, label: str = "") -> CalibrationProfile:
    return CalibrationProfile(
        nominal_radius_mm=measurement.nominal_radius_mm,
        hole_radius_bias_mm=measurement.hole_radius_bias_mm(),
        peg_radius_bias_mm=measurement.peg_radius_bias_mm(),
        created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        label=label,
    )


def save_calibration_profile(profile: CalibrationProfile, path: str | Path | None = None) -> Path:
    path = Path(path) if path is not None else DEFAULT_CALIBRATION_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(profile), indent=2))
    return path


def load_calibration_profile(path: str | Path | None = None) -> CalibrationProfile | None:
    """Returns None if no calibration has been saved yet (not an error --
    that's the normal state for a user who hasn't calibrated)."""
    path = Path(path) if path is not None else DEFAULT_CALIBRATION_PATH
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise CalibrationError(f"Could not read calibration profile {path}: {exc}") from exc
    try:
        return CalibrationProfile(**data)
    except TypeError as exc:
        raise CalibrationError(f"Calibration profile {path} is malformed: {exc}") from exc


def compute_calibrated_pair_compensation(
    pair: FeaturePair,
    feature_type_a: str,
    feature_type_b: str,
    material: str,
    calibration: CalibrationProfile,
    split_ratio: float = 0.5,
    table: CompensationTable | None = None,
) -> PairCompensation:
    """Same as `compute_pair_compensation`, but adds the calibrated bias
    on top of each hole/peg adjustment. Flat+flat pairs are returned
    unchanged -- the calibration test part only measures circular
    features, so there's no measured bias to apply to a flat face.
    """
    base = compute_pair_compensation(pair, feature_type_a, feature_type_b, material, split_ratio, table)

    def adjusted(adjustment: FeatureAdjustment) -> FeatureAdjustment:
        if adjustment.feature_type == "hole":
            bias = calibration.hole_radius_bias_mm
        elif adjustment.feature_type == "peg":
            bias = calibration.peg_radius_bias_mm
        else:
            return adjustment
        # Clamped at 0 rather than allowed negative: material_removed_mm's
        # invariant (enforced downstream in geometry_modification) is that
        # it's never negative. A large enough negative bias (an atypical
        # printer whose holes print oversized, say) could otherwise push
        # this below zero; clamping means "at least don't make it worse",
        # not "cancel out an unusual bias we have no clearance budget for".
        return FeatureAdjustment(
            ref=adjustment.ref,
            feature_type=adjustment.feature_type,
            material_removed_mm=max(adjustment.material_removed_mm + bias, 0.0),
        )

    return PairCompensation(
        pair=base.pair,
        material=base.material,
        total_clearance_mm=base.total_clearance_mm,
        source_range_mm=base.source_range_mm,
        adjustment_a=adjusted(base.adjustment_a),
        adjustment_b=adjusted(base.adjustment_b),
    )
