"""Segment a mesh's faces into patches of smoothly-connected surface.

This is the first step of feature detection: before we try to fit a plane
or a cylinder to anything, we need to know *which* faces plausibly belong
to the same surface. Adjacent faces are grouped together as long as the
dihedral angle between them stays below a threshold, which keeps a
tessellated cylindrical wall in one patch while breaking cleanly at the
sharp edges where a hole or boss meets a surrounding flat face.
"""

from __future__ import annotations

import numpy as np
import trimesh
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components


def segment_smooth_patches(mesh: trimesh.Trimesh, angle_threshold_deg: float = 35.0) -> list[np.ndarray]:
    """Partition mesh faces into patches, split at edges sharper than the threshold.

    Returns a list of face-index arrays, one per connected patch. Order is
    arbitrary (whatever `connected_components` returns).
    """
    n_faces = len(mesh.faces)
    if n_faces == 0:
        return []

    angles_deg = np.degrees(mesh.face_adjacency_angles)
    edges = mesh.face_adjacency[angles_deg < angle_threshold_deg]

    if len(edges) == 0:
        return [np.array([i], dtype=np.int64) for i in range(n_faces)]

    rows = np.concatenate([edges[:, 0], edges[:, 1]])
    cols = np.concatenate([edges[:, 1], edges[:, 0]])
    graph = coo_matrix((np.ones(len(rows), dtype=bool), (rows, cols)), shape=(n_faces, n_faces))

    n_components, labels = connected_components(graph, directed=False)

    patches: list[list[int]] = [[] for _ in range(n_components)]
    for face_idx, label in enumerate(labels):
        patches[label].append(face_idx)
    return [np.array(p, dtype=np.int64) for p in patches]
