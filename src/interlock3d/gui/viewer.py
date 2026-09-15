"""Embedded interactive 3D viewer widget, backed by PyVista."""

from __future__ import annotations

import itertools

from PySide6.QtWidgets import QVBoxLayout, QWidget
from pyvistaqt import QtInteractor

# Distinct colors cycled across loaded parts so multi-part assemblies are
# visually easy to tell apart.
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
        self._actors: dict[str, object] = {}

    def add_part(self, name: str, mesh, color: str | None = None):
        """Add a mesh to the scene under a unique name and return its actor."""
        color = color or next(self._color_cycle)
        actor = self.plotter.add_mesh(
            mesh,
            color=color,
            show_edges=False,
            smooth_shading=True,
            name=name,
        )
        self._actors[name] = actor
        return actor

    def clear_parts(self) -> None:
        self.plotter.clear()
        self._actors.clear()
        self._color_cycle = itertools.cycle(_PART_COLORS)

    def reset_camera(self) -> None:
        self.plotter.reset_camera()

    def shutdown(self) -> None:
        self.plotter.close()
