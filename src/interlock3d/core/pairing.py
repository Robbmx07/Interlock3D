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
