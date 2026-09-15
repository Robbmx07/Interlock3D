"""Data model for detected mating features."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

FeatureType = Literal["hole", "peg", "flat"]


@dataclass
class Feature:
    """A candidate mating feature detected on a single part's mesh."""

    feature_type: FeatureType
    face_indices: np.ndarray
    """Indices into the source mesh's `faces` array that make up this feature."""

    center: np.ndarray
    """Representative point (3,): axis midpoint for hole/peg, patch centroid for flat."""

    axis: np.ndarray
    """Unit vector (3,): cylinder axis for hole/peg, outward face normal for flat."""

    area: float
    """Surface area of the patch, in mesh units squared."""

    radius: float | None = None
    """Cylinder radius, hole/peg only."""

    extent: float | None = None
    """Depth (hole) or height (peg) measured along the axis; hole/peg only."""

    arc_degrees: float | None = None
    """Angular coverage around the axis; hole/peg only."""

    fit_residual: float | None = None
    """RMS distance of patch points from the fitted primitive (mesh units)."""

    inlier_ratio: float | None = None
    """Fraction of patch sample points the RANSAC fit counted as inliers."""

    def __repr__(self) -> str:  # pragma: no cover - cosmetic
        bits = [f"{self.feature_type}", f"faces={len(self.face_indices)}", f"area={self.area:.2f}"]
        if self.radius is not None:
            bits.append(f"r={self.radius:.3f}")
        if self.extent is not None:
            bits.append(f"extent={self.extent:.3f}")
        return f"Feature({', '.join(bits)})"
