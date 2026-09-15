"""Feature detection: find candidate holes, pegs/bosses, and flat mating
faces on a single part's mesh.

Pipeline:
1. Segment the mesh into patches of smoothly-connected surface
   (`segmentation.segment_smooth_patches`), split at sharp edges.
2. For each patch big enough to matter, classify it by how its face
   normals are distributed:
   - nearly identical normals -> flat face candidate.
   - normals swept around a common axis (i.e. clustered in the plane
     perpendicular to that axis) -> cylindrical candidate.
   - anything else (fillets, cones, freeform) -> not classified; skipped.
3. Fit the actual primitive with `pyransac3d` (a plane or a cylinder) to
   get precise parameters, and use the fit's inlier ratio / residual as a
   quality gate.
4. For cylindrical candidates, tell holes from pegs by whether the patch's
   face normals point away from the fitted axis (convex -> peg/boss) or
   toward it (concave -> hole).
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

import numpy as np
import pyransac3d as pyrsc
import trimesh

from interlock3d.core.features import Feature
from interlock3d.core.segmentation import segment_smooth_patches


@dataclass
class DetectionConfig:
    # Segmentation
    smooth_angle_deg: float = 35.0
    """Max dihedral angle (deg) between adjacent faces to keep them in one patch."""

    # Patch admission
    min_patch_faces: int = 2
    min_patch_area: float = 1.0

    # Flat vs. cylindrical classification
    flat_normal_tol_deg: float = 5.0
    """Max angular spread of a patch's normals to call it flat outright."""
    cylinder_axis_eig_ratio: float = 0.15
    """Max (smallest/middle) eigenvalue ratio of the normal-direction second
    moment matrix to accept a patch as cylindrical (normals confined near a
    plane perpendicular to one axis)."""

    # RANSAC fit quality gates
    ransac_thresh_fraction: float = 0.05
    """RANSAC inlier threshold as a fraction of the patch's estimated radius
    (cylinders) — keeps the threshold scale-appropriate for tiny and huge
    features alike."""
    ransac_max_iterations: int = 500
    min_inlier_ratio: float = 0.8

    # Cylinder acceptance
    min_radius: float = 0.3
    max_radius: float = 1000.0
    min_arc_degrees: float = 180.0
    """Minimum angular sweep around the axis to call something a hole/peg
    rather than a fillet or partial round."""

    random_seed: int | None = 0
    """Seed for pyransac3d's internal RNG, for reproducible detection runs."""


def detect_features(mesh: trimesh.Trimesh, config: DetectionConfig | None = None) -> list[Feature]:
    """Detect candidate mating features on a single mesh."""
    config = config or DetectionConfig()

    if config.random_seed is not None:
        import random

        random.seed(config.random_seed)

    patches = segment_smooth_patches(mesh, config.smooth_angle_deg)

    features: list[Feature] = []
    for face_indices in patches:
        if len(face_indices) < config.min_patch_faces:
            continue
        area = float(mesh.area_faces[face_indices].sum())
        if area < config.min_patch_area:
            continue

        feature = _classify_patch(mesh, face_indices, area, config)
        if feature is not None:
            features.append(feature)

    features.sort(key=lambda f: (f.feature_type, -f.area))
    return features


def feature_id_per_face(mesh: trimesh.Trimesh, features: list[Feature]) -> np.ndarray:
    """Map every face of `mesh` to the index of the feature it belongs to
    in `features`, or -1 if it isn't part of any detected feature.

    Used to attach a pickable per-face lookup to a rendered mesh, so a GUI
    click on a face can be resolved straight to a `Feature` without a
    separate spatial search.
    """
    ids = np.full(len(mesh.faces), -1, dtype=np.int64)
    for idx, feature in enumerate(features):
        ids[feature.face_indices] = idx
    return ids


def _classify_patch(
    mesh: trimesh.Trimesh, face_indices: np.ndarray, area: float, config: DetectionConfig
) -> Feature | None:
    normals = mesh.face_normals[face_indices]
    vertex_indices = np.unique(mesh.faces[face_indices])
    points = mesh.vertices[vertex_indices]

    # A near-zero mean normal means the patch's normals point every which
    # way (e.g. a full 360-degree cylindrical wall, where they cancel by
    # symmetry) rather than clustering around one direction — that alone
    # rules out "flat", so skip straight to the cylinder attempt.
    mean_normal_raw = normals.mean(axis=0)
    mean_norm = float(np.linalg.norm(mean_normal_raw))
    if mean_norm > 1e-6:
        mean_normal = mean_normal_raw / mean_norm
        angular_spread_deg = np.degrees(
            np.arccos(np.clip(normals @ mean_normal, -1.0, 1.0))
        ).max()
        if angular_spread_deg <= config.flat_normal_tol_deg:
            return _fit_flat(mesh, face_indices, points, mean_normal, area, config)

    return _try_fit_cylinder(mesh, face_indices, points, normals, area, config)


def _fit_flat(
    mesh: trimesh.Trimesh,
    face_indices: np.ndarray,
    points: np.ndarray,
    mean_normal: np.ndarray,
    area: float,
    config: DetectionConfig,
) -> Feature | None:
    plane = pyrsc.Plane()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        result = plane.fit(points, thresh=_plane_thresh(points), maxIteration=config.ransac_max_iterations)

    normal = np.array(result.equation[:3], dtype=np.float64)
    normal = _safe_normalize(normal)
    if normal is None:
        normal = mean_normal
    elif np.dot(normal, mean_normal) < 0:
        normal = -normal

    inlier_ratio = len(result.inliers) / len(points) if len(points) else 0.0
    if inlier_ratio < config.min_inlier_ratio:
        return None

    d = -np.dot(normal, points.mean(axis=0))
    residuals = points @ normal + d
    fit_residual = float(np.sqrt(np.mean(residuals**2)))

    return Feature(
        feature_type="flat",
        face_indices=face_indices,
        center=points.mean(axis=0),
        axis=normal,
        area=area,
        fit_residual=fit_residual,
        inlier_ratio=inlier_ratio,
    )


def _try_fit_cylinder(
    mesh: trimesh.Trimesh,
    face_indices: np.ndarray,
    points: np.ndarray,
    normals: np.ndarray,
    area: float,
    config: DetectionConfig,
) -> Feature | None:
    # Second moment matrix of face normal directions: for a cylindrical
    # patch, normals stay in the plane perpendicular to the axis, so this
    # matrix has one near-zero eigenvalue whose eigenvector is the axis.
    moment = (normals.T @ normals) / len(normals)
    eigvals, eigvecs = np.linalg.eigh(moment)
    if eigvals[1] <= 1e-12 or eigvals[0] / eigvals[1] > config.cylinder_axis_eig_ratio:
        return None  # not confined to a plane perpendicular to any axis

    axis_guess = eigvecs[:, 0]
    rough_radius = _rough_radius(points, axis_guess)
    if rough_radius <= 0:
        return None

    cylinder = pyrsc.Cylinder()
    thresh = max(config.ransac_thresh_fraction * rough_radius, 1e-3)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        result = cylinder.fit(points, thresh=thresh, maxIteration=config.ransac_max_iterations)

    radius = float(result.radius)
    if not (config.min_radius <= radius <= config.max_radius):
        return None

    axis = _safe_normalize(np.asarray(result.axis, dtype=np.float64))
    if axis is None:
        return None
    axis_point = np.asarray(result.center, dtype=np.float64)

    inlier_ratio = len(result.inliers) / len(points) if len(points) else 0.0
    if inlier_ratio < config.min_inlier_ratio:
        return None

    t = (points - axis_point) @ axis
    t_min, t_max = float(t.min()), float(t.max())
    extent = t_max - t_min
    center = axis_point + axis * ((t_min + t_max) / 2.0)

    radial = points - (axis_point + np.outer(t, axis))
    radial_dist = np.linalg.norm(radial, axis=1)
    fit_residual = float(np.sqrt(np.mean((radial_dist - radius) ** 2)))

    arc_degrees = _arc_coverage_deg(radial, axis)
    if arc_degrees < config.min_arc_degrees:
        return None

    feature_type = _classify_convexity(mesh, face_indices, axis_point, axis)

    return Feature(
        feature_type=feature_type,
        face_indices=face_indices,
        center=center,
        axis=axis,
        area=area,
        radius=radius,
        extent=extent,
        arc_degrees=arc_degrees,
        fit_residual=fit_residual,
        inlier_ratio=inlier_ratio,
    )


def _classify_convexity(
    mesh: trimesh.Trimesh, face_indices: np.ndarray, axis_point: np.ndarray, axis: np.ndarray
) -> str:
    centroids = mesh.triangles_center[face_indices]
    face_normals = mesh.face_normals[face_indices]
    face_areas = mesh.area_faces[face_indices]

    t = (centroids - axis_point) @ axis
    closest_on_axis = axis_point + np.outer(t, axis)
    radial = centroids - closest_on_axis
    radial = radial / np.linalg.norm(radial, axis=1, keepdims=True).clip(min=1e-12)

    outwardness = np.sum(radial * face_normals, axis=1)
    weighted_mean = np.average(outwardness, weights=face_areas)
    return "peg" if weighted_mean > 0 else "hole"


def _rough_radius(points: np.ndarray, axis: np.ndarray) -> float:
    centroid = points.mean(axis=0)
    t = (points - centroid) @ axis
    radial = points - centroid - np.outer(t, axis)
    return float(np.linalg.norm(radial, axis=1).mean())


def _arc_coverage_deg(radial_vectors: np.ndarray, axis: np.ndarray) -> float:
    """Angular sweep (deg) that a set of radial vectors covers around `axis`."""
    ref = _safe_normalize(radial_vectors[np.argmax(np.linalg.norm(radial_vectors, axis=1))])
    if ref is None:
        return 0.0
    u = ref
    v = np.cross(axis, u)
    v = _safe_normalize(v)
    if v is None:
        return 0.0

    angles = np.arctan2(radial_vectors @ v, radial_vectors @ u)
    angles = np.sort(np.mod(angles, 2 * np.pi))
    gaps = np.diff(angles, append=angles[0] + 2 * np.pi)
    max_gap = gaps.max()
    return float(np.degrees(2 * np.pi - max_gap))


def _plane_thresh(points: np.ndarray) -> float:
    diag = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
    return max(diag * 0.01, 1e-3)


def _safe_normalize(vec: np.ndarray) -> np.ndarray | None:
    norm = np.linalg.norm(vec)
    if norm < 1e-12:
        return None
    return vec / norm
