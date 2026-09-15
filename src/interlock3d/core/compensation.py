"""Compensation engine: given a confirmed feature pair, a material, and a
fit type, compute how much to adjust each feature so the printed parts
actually achieve that fit.

This module only computes numbers (how much to grow/shrink each feature,
in mm) -- it doesn't touch mesh geometry. Applying an adjustment to the
actual mesh is Phase 5's job.

Every adjustment is expressed as a single non-negative "material removed"
value per feature, always in the direction that widens the gap between
the two mating surfaces:
- hole: material_removed_mm is *added* to the hole's radius (bore grows).
- peg: material_removed_mm is *subtracted* from the peg's radius (peg shrinks).
- flat: material_removed_mm is recessed inward along the face's own
  normal (the face is pulled back into its own solid).
In all three cases "remove material in the direction that increases the
gap" is the same operation described in feature-appropriate terms, which
is what Phase 5 needs to know to actually move the right vertices.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from interlock3d.core.pairing import FeaturePair, FeatureRef, FitType, are_types_compatible

DEFAULT_PROFILE_PATH = Path(__file__).resolve().parent.parent / "data" / "compensation_profiles.json"

Material = str


class CompensationError(RuntimeError):
    """Raised for an unknown material/fit type, or an unpairable feature
    type combination (nothing meaningful to compute for e.g. hole+flat)."""


@dataclass(frozen=True)
class FeatureAdjustment:
    ref: FeatureRef
    feature_type: str
    material_removed_mm: float
    """Always >= 0. Direction is implied by `feature_type` -- see module docstring."""


@dataclass(frozen=True)
class PairCompensation:
    pair: FeaturePair
    material: Material
    total_clearance_mm: float
    """Target radial (hole/peg) or normal-direction (flat/flat) gap between
    the two mating surfaces after printing, per side."""
    source_range_mm: tuple[float, float] | None
    adjustment_a: FeatureAdjustment
    adjustment_b: FeatureAdjustment

    def adjustments(self) -> tuple[FeatureAdjustment, FeatureAdjustment]:
        return (self.adjustment_a, self.adjustment_b)


class CompensationTable:
    """Loaded material/fit-type clearance profiles."""

    def __init__(self, profiles: dict) -> None:
        self._profiles = profiles

    @classmethod
    def load(cls, path: str | Path | None = None) -> CompensationTable:
        path = Path(path) if path is not None else DEFAULT_PROFILE_PATH
        try:
            data = json.loads(path.read_text())
        except OSError as exc:
            raise CompensationError(f"Could not read compensation profile file {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise CompensationError(f"Compensation profile file {path} is not valid JSON: {exc}") from exc
        return cls(data)

    def materials(self) -> list[Material]:
        return sorted(self._profiles.get("materials", {}))

    def fit_types(self, material: Material) -> list[FitType]:
        return sorted(self._material_entry(material))

    def get_clearance_mm(self, material: Material, fit_type: FitType) -> float:
        return float(self._fit_entry(material, fit_type)["clearance_per_side_mm"])

    def get_clearance_range_mm(self, material: Material, fit_type: FitType) -> tuple[float, float] | None:
        rng = self._fit_entry(material, fit_type).get("clearance_range_mm")
        return (float(rng[0]), float(rng[1])) if rng else None

    def _material_entry(self, material: Material) -> dict:
        materials = self._profiles.get("materials", {})
        if material not in materials:
            raise CompensationError(f"Unknown material '{material}'. Known materials: {sorted(materials)}")
        return materials[material]

    def _fit_entry(self, material: Material, fit_type: FitType) -> dict:
        entry = self._material_entry(material)
        if fit_type not in entry:
            raise CompensationError(
                f"Unknown fit type '{fit_type}' for material '{material}'. Known: {sorted(entry)}"
            )
        return entry[fit_type]


_default_table: CompensationTable | None = None


def _get_default_table() -> CompensationTable:
    global _default_table
    if _default_table is None:
        _default_table = CompensationTable.load()
    return _default_table


def compute_pair_compensation(
    pair: FeaturePair,
    feature_type_a: str,
    feature_type_b: str,
    material: Material,
    split_ratio: float = 0.5,
    table: CompensationTable | None = None,
) -> PairCompensation:
    """Compute the compensation for one confirmed pair.

    `split_ratio` only matters for a hole+peg pair: it's the hole's share
    of the total clearance (the peg gets the remainder), letting the two
    features be corrected asymmetrically if desired. Defaults to an even
    50/50 split -- there's no calibration data yet to justify favoring one
    feature over the other (that's exactly what Phase 7's calibration
    wizard is for). A flat+flat pair always splits 50/50 regardless of
    `split_ratio`: the two faces play an identical role, so there's no
    physical basis to weight one over the other.
    """
    if not 0.0 <= split_ratio <= 1.0:
        raise ValueError(f"split_ratio must be between 0 and 1, got {split_ratio}")
    if not are_types_compatible(feature_type_a, feature_type_b):
        raise CompensationError(
            f"Feature types '{feature_type_a}' and '{feature_type_b}' cannot be paired for compensation "
            "(only hole+peg and flat+flat are supported)"
        )

    table = table or _get_default_table()
    clearance = table.get_clearance_mm(material, pair.fit_type)
    source_range = table.get_clearance_range_mm(material, pair.fit_type)

    if feature_type_a == "flat":
        share_a = share_b = 0.5
    elif feature_type_a == "hole":
        share_a, share_b = split_ratio, 1.0 - split_ratio
    else:  # feature_type_a == "peg"
        share_a, share_b = 1.0 - split_ratio, split_ratio

    adjustment_a = FeatureAdjustment(pair.a, feature_type_a, clearance * share_a)
    adjustment_b = FeatureAdjustment(pair.b, feature_type_b, clearance * share_b)

    return PairCompensation(
        pair=pair,
        material=material,
        total_clearance_mm=clearance,
        source_range_mm=source_range,
        adjustment_a=adjustment_a,
        adjustment_b=adjustment_b,
    )
