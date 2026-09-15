"""Embedded interactive 3D viewer widget, backed by PyVista.

Beyond just rendering parts (Phase 1), this now supports:
- click-to-select detected features (Phase 2 output), resolved via two
  per-cell arrays (`interlock3d_part_id`, `interlock3d_feature_id`)
  attached to each part's mesh — these survive PyVista's cell-extraction
  during picking, so a click resolves straight to (part, feature) without
  a separate spatial lookup.
- translucent highlight overlays per feature, whose color/opacity the
  caller (MainWindow) can update to reflect selection/pairing state.
"""

from __future__ import annotations

import itertools

import numpy as np
import pyvista as pv
from PySide6.QtWidgets import QVBoxLayout, QWidget
from pyvistaqt import QtInteractor

_PART_COLORS = [
    "#4C9AFF",
    "#FF8A65",
    "#81C784",
    "#BA68C8",
    "#FFD54F",
    "#4DB6AC",
    "#F06292",
    "#A1887F",
]

_PART_ID_KEY = "interlock3d_part_id"
_FEATURE_ID_KEY = "interlock3d_feature_id"


class MeshViewer(QWidget):
    """A QWidget wrapping an embedded PyVista QtInteractor."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.plotter = QtInteractor(self)
        self.plotter.set_background("#1e1e1e")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.plotter.interactor)

        self._color_cycle = itertools.cycle(_PART_COLORS)
        self._part_actors: dict[str, object] = {}
        self._part_ids: dict[str, int] = {}
        self._next_part_id = 0
        self._feature_actors: dict[tuple[str, int], object] = {}
        self._pick_callback = None

    def add_part(
        self,
        name: str,
        mesh: pv.PolyData,
        feature_id_by_face: np.ndarray | None = None,
        color: str | None = None,
    ):
        """Add a part's mesh to the scene, pickable down to individual faces."""
        color = color or next(self._color_cycle)
        part_id = self._next_part_id
        self._next_part_id += 1
        self._part_ids[name] = part_id

        mesh = mesh.copy()
        n_cells = mesh.n_cells
        mesh.cell_data[_PART_ID_KEY] = np.full(n_cells, part_id, dtype=np.int64)
        if feature_id_by_face is None:
            feature_id_by_face = np.full(n_cells, -1, dtype=np.int64)
        mesh.cell_data[_FEATURE_ID_KEY] = np.asarray(feature_id_by_face, dtype=np.int64)

        actor = self.plotter.add_mesh(
            mesh,
            color=color,
            show_edges=False,
            smooth_shading=True,
            name=name,
            pickable=True,
        )
        self._part_actors[name] = actor
        return actor

    def add_feature_overlay(
        self, part_name: str, feature_index: int, overlay_mesh: pv.PolyData, color: str, opacity: float
    ) -> None:
        """Add (or replace) the highlight overlay actor for one feature."""
        actor_name = f"highlight::{part_name}::{feature_index}"
        actor = self.plotter.add_mesh(
            overlay_mesh,
            color=color,
            opacity=opacity,
            name=actor_name,
            pickable=False,
            show_edges=False,
            smooth_shading=True,
        )
        self._feature_actors[(part_name, feature_index)] = actor

    def set_feature_appearance(self, part_name: str, feature_index: int, color: str, opacity: float) -> None:
        actor = self._feature_actors.get((part_name, feature_index))
        if actor is None:
            return
        actor.prop.color = color
        actor.prop.opacity = opacity

    def enable_feature_picking(self, callback) -> None:
        """`callback(part_name: str, feature_index: int | None)` fires on
        every left-click that lands on a part's surface (`feature_index`
        is None if the click hit the part but not a detected feature).

        Deliberately built on `enable_surface_point_picking` with our own
        `picker.GetCellId()` lookup rather than PyVista's higher-level
        `enable_element_picking`: that helper resolves the picked cell via
        `mesh.find_containing_cell(picked_point)`, which is unreliable for
        plain triangulated surfaces (a zero-thickness cell "containing" a
        3D point is a degenerate test) and was empirically found to
        return -1 for points that the picker itself had just correctly
        hit. `GetCellId()` is the picker's own answer and doesn't have
        that problem.
        """
        self._pick_callback = callback
        self.plotter.enable_surface_point_picking(
            callback=self._on_picked,
            left_clicking=True,
            use_picker=True,
            picker="cell",
            show_point=False,
            show_message=False,
        )

    def _on_picked(self, _point, picker) -> None:
        if self._pick_callback is None:
            return
        dataset = picker.GetDataSet()
        if dataset is None:
            return
        cell_id = picker.GetCellId()
        if cell_id < 0:
            return

        mesh = pv.wrap(dataset)
        part_ids = mesh.cell_data.get(_PART_ID_KEY)
        feature_ids = mesh.cell_data.get(_FEATURE_ID_KEY)
        if part_ids is None or feature_ids is None:
            return

        part_id = int(part_ids[cell_id])
        part_name = next((n for n, pid in self._part_ids.items() if pid == part_id), None)
        if part_name is None:
            return

        feature_id = int(feature_ids[cell_id])
        self._pick_callback(part_name, feature_id if feature_id >= 0 else None)

    def clear_parts(self) -> None:
        self.plotter.clear()
        self._part_actors.clear()
        self._part_ids.clear()
        self._feature_actors.clear()
        self._next_part_id = 0
        self._color_cycle = itertools.cycle(_PART_COLORS)

    def reset_camera(self) -> None:
        self.plotter.reset_camera()

    def shutdown(self) -> None:
        self.plotter.close()
