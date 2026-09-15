"""Geometry modification: apply a computed compensation (Phase 4) to a
feature's actual mesh vertices.

Given a `Feature` (Phase 2) and a `material_removed_mm` value (Phase 4's
`FeatureAdjustment.material_removed_mm`, always >= 0), move exactly that
feature's vertices in the direction implied by its `feature_type`:
- hole: radially outward from the fitted axis (the bore grows).
- peg: radially inward toward the fitted axis (the peg shrinks).
- flat: along the negative of its own normal (the face is recessed into
  the solid it belongs to).

Topology (which vertices exist, which faces reference them) never
changes here -- only vertex *positions* move. That means if the input
mesh was watertight and manifold, this can only break that in two ways,
both of which are checked for rather than silently allowed:
1. A physically invalid request, like shrinking a peg by more than its
   own radius (radius would go to zero or negative) -- rejected before
   touching any geometry.
2. Self-intersection from a large displacement relative to local mesh
   density -- not something this module can prevent outright (whether a
   given displacement self-intersects depends on the specific mesh), but
   `check_mesh_integrity()` gives a caller a way to verify the result
   before trusting it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import trimesh

from interlock3d.core.features import Feature


class GeometryModificationError(RuntimeError):
    """Raised for a geometrically invalid adjustment request."""


def displace_feature_vertices(
    vertices: np.ndarray, faces: np.ndarray, feature: Feature, material_removed_mm: float
) -> np.ndarray:
    """Return a NEW vertices array with `feature`'s vertices displaced.

    Reads current positions from `vertices` (not from `feature` or a
    mesh object), so multiple adjustments on the same part can be chained
    by feeding each result into the next call -- later adjustments see
    earlier ones' displacements for any vertex they happen to share.
    """
    if material_removed_mm < 0:
        raise ValueError(f"material_removed_mm must be >= 0, got {material_removed_mm}")

    result = vertices.copy()
    vertex_indices = np.unique(faces[feature.face_indices])
    pts = result[vertex_indices]

    if feature.feature_type in ("hole", "peg"):
        if feature.radius is None:
            raise GeometryModificationError(f"{feature.feature_type} feature has no radius; cannot displace")
        if feature.feature_type == "peg" and material_removed_mm >= feature.radius:
            raise GeometryModificationError(
                f"Cannot shrink a peg of radius {feature.radius:.3f}mm by {material_removed_mm:.3f}mm "
                "-- would collapse it to a zero or negative radius"
            )

        sign = 1.0 if feature.feature_type == "hole" else -1.0
        t = (pts - feature.center) @ feature.axis
        closest_on_axis = feature.center + np.outer(t, feature.axis)
        radial = pts - closest_on_axis
        radial_len = np.linalg.norm(radial, axis=1, keepdims=True)
        radial_dir = radial / np.clip(radial_len, 1e-9, None)
        result[vertex_indices] = pts + sign * material_removed_mm * radial_dir

    elif feature.feature_type == "flat":
        result[vertex_indices] = pts - material_removed_mm * feature.axis

    else:
        raise GeometryModificationError(f"Unsupported feature type for displacement: {feature.feature_type}")

    return result


def apply_feature_adjustments(
    mesh: trimesh.Trimesh, features: list[Feature], adjustments: Iterable[tuple[int, float]]
) -> trimesh.Trimesh:
    """Apply several (feature_index, material_removed_mm) adjustments to a
    copy of `mesh` and return the result. `mesh` is never mutated."""
    vertices = mesh.vertices.copy()
    for feature_index, material_removed_mm in adjustments:
        vertices = displace_feature_vertices(vertices, mesh.faces, features[feature_index], material_removed_mm)

    new_mesh = mesh.copy()
    new_mesh.vertices = vertices
    return new_mesh


@dataclass(frozen=True)
class MeshIntegrityReport:
    is_watertight: bool
    is_winding_consistent: bool
    has_degenerate_faces: bool
    min_face_area: float
    volume: float
    """NaN if the mesh isn't watertight (trimesh's volume is only meaningful then)."""
    has_negative_volume: bool
    """True if watertight+winding-consistent but volume < 0 -- found by
    stress-testing this module with an oversized hole enlargement (grown
    past the part's own outer boundary): trimesh's watertight and
    winding-consistent checks are purely topological (do faces still form
    a closed, consistently-oriented shell?) and both stayed True even
    though the "hole" had self-intersected its way past the surrounding
    material and inverted the effective solid. Volume sign is a cheap,
    effective check those two miss entirely."""

    @property
    def is_valid(self) -> bool:
        return (
            self.is_watertight
            and self.is_winding_consistent
            and not self.has_degenerate_faces
            and not self.has_negative_volume
        )


def check_mesh_integrity(mesh: trimesh.Trimesh, degenerate_area_tol: float = 1e-9) -> MeshIntegrityReport:
    areas = mesh.area_faces
    watertight = bool(mesh.is_watertight)
    volume = float(mesh.volume) if watertight else float("nan")
    return MeshIntegrityReport(
        is_watertight=watertight,
        is_winding_consistent=bool(mesh.is_winding_consistent),
        has_degenerate_faces=bool((areas < degenerate_area_tol).any()) if len(areas) else False,
        min_face_area=float(areas.min()) if len(areas) else 0.0,
        volume=volume,
        has_negative_volume=watertight and volume < 0,
    )
