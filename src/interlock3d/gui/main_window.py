"""Main application window: file loading, part list, and 3D viewer."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QFileDialog,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QSplitter,
    QStatusBar,
    QToolBar,
)

from interlock3d.core.mesh_loader import MeshLoadError, load_trimesh, trimesh_to_pyvista
from interlock3d.gui.viewer import MeshViewer


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Interlock3D")
        self.resize(1200, 800)

        self._parts: dict[str, object] = {}

        self.viewer = MeshViewer(self)
        self.part_list = QListWidget(self)
        self.part_list.setMaximumWidth(260)

        splitter = QSplitter(self)
        splitter.addWidget(self.part_list)
        splitter.addWidget(self.viewer)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

        self.setStatusBar(QStatusBar(self))
        self._build_toolbar()

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Main", self)
        toolbar.setMovable(False)
        self.addToolBar(toolbar)

        open_action = QAction("Open STL(s)...", self)
        open_action.triggered.connect(self.open_files)
        toolbar.addAction(open_action)

        clear_action = QAction("Clear", self)
        clear_action.triggered.connect(self.clear_all)
        toolbar.addAction(clear_action)

        reset_view_action = QAction("Reset View", self)
        reset_view_action.triggered.connect(self.viewer.reset_camera)
        toolbar.addAction(reset_view_action)

    def open_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open STL file(s)", "", "STL files (*.stl);;All files (*)"
        )
        if not paths:
            return
        for path in paths:
            self._load_and_add(path)
        self.viewer.reset_camera()

    def _load_and_add(self, path: str) -> None:
        try:
            mesh = load_trimesh(path)
        except MeshLoadError as exc:
            QMessageBox.warning(self, "Failed to load STL", str(exc))
            return

        pv_mesh = trimesh_to_pyvista(mesh)
        name = self._unique_name(Path(path).stem)
        self.viewer.add_part(name, pv_mesh)
        self._parts[name] = mesh
        self.part_list.addItem(f"{name}  ({len(mesh.faces)} tris)")
        self.statusBar().showMessage(f"Loaded {name}", 3000)

    def _unique_name(self, base: str) -> str:
        name = base
        suffix = 1
        while name in self._parts:
            suffix += 1
            name = f"{base}_{suffix}"
        return name

    def clear_all(self) -> None:
        self.viewer.clear_parts()
        self._parts.clear()
        self.part_list.clear()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.viewer.shutdown()
        super().closeEvent(event)
