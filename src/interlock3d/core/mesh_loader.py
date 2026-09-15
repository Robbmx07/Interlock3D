"""STL loading and conversion between trimesh and PyVista representations."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pyvista as pv
import trimesh


class MeshLoadError(RuntimeError):
    """Raised when a file cannot be loaded as a single triangle mesh."""


def load_trimesh(path: str | Path) -> trimesh.Trimesh:
    """Load an STL (or other mesh format trimesh supports) as a single Trimesh."""
    path = Path(path)
    if not path.exists():
        raise MeshLoadError(f"File not found: {path}")

    try:
        mesh = trimesh.load(path, force="mesh")
    except Exception as exc:  # trimesh raises a variety of exception types
        raise MeshLoadError(f"Could not parse {path.name}: {exc}") from exc

    if not isinstance(mesh, trimesh.Trimesh):
        raise MeshLoadError(f"{path.name} did not resolve to a single triangle mesh")
    if mesh.is_empty:
        raise MeshLoadError(f"{path.name} loaded but contains no geometry")

    return mesh


def trimesh_to_pyvista(mesh: trimesh.Trimesh) -> pv.PolyData:
    """Convert a trimesh.Trimesh to a pyvista.PolyData for rendering."""
    triangle_count = len(mesh.faces)
    faces = np.hstack(
        (np.full((triangle_count, 1), 3, dtype=np.int64), mesh.faces)
    ).astype(np.int64)
    return pv.PolyData(mesh.vertices, faces)


def feature_overlay_polydata(mesh: trimesh.Trimesh, face_indices: np.ndarray, offset: float) -> pv.PolyData:
    """Build a small standalone PolyData for just the given faces, pushed
    outward along vertex normals by `offset`.

    Used to render a highlight over a detected feature without it
    z-fighting against the part's own surface at the exact same
    coordinates (the highlight is a separate actor occupying the same
    geometry, so without an offset the two flicker for depth priority).
    """
    sub_faces = mesh.faces[face_indices]
    vertex_indices, remapped = np.unique(sub_faces, return_inverse=True)
    verts = mesh.vertices[vertex_indices] + mesh.vertex_normals[vertex_indices] * offset
    new_faces = remapped.reshape(sub_faces.shape)
    pv_faces = np.hstack(
        (np.full((len(new_faces), 1), 3, dtype=np.int64), new_faces)
    ).astype(np.int64)
    return pv.PolyData(verts, pv_faces)
