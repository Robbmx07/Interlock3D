"""Data model for confirmed feature pairs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

FitType = Literal["press", "sliding", "clearance"]

FIT_TYPE_LABELS: dict[FitType, str] = {
    "press": "Press fit",
    "sliding": "Sliding fit",
    "clearance": "Clearance fit",
}


@dataclass(frozen=True)
class FeatureRef:
    """Identifies one detected feature: which part, and its index in that
    part's `detect_features()` output list."""

    part_name: str
    feature_index: int


@dataclass
class FeaturePair:
    a: FeatureRef
    b: FeatureRef
    fit_type: FitType = "sliding"

    def involves(self, ref: FeatureRef) -> bool:
        return ref == self.a or ref == self.b


def are_types_compatible(feature_type_a: str, feature_type_b: str) -> bool:
    """Whether two feature types can be meaningfully paired for a mating fit.

    Only hole+peg (a cylindrical feature inserting into another) and
    flat+flat (two faces meant to sit flush) correspond to an actual
    physical mating relationship; anything else (hole+hole, peg+peg,
    hole+flat, peg+flat) doesn't have a sensible compensation to compute.
    """
    types = {feature_type_a, feature_type_b}
    return types == {"hole", "peg"} or (feature_type_a == "flat" and feature_type_b == "flat")
