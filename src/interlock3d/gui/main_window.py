"""Main application window: file loading, part list, feature pairing, and
the 3D viewer."""

from __future__ import annotations

import itertools
from pathlib import Path

from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSplitter,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from interlock3d.core.calibration import (
    DEFAULT_CALIBRATION_PATH,
    CalibrationError,
    CalibrationProfile,
    load_calibration_profile,
    save_calibration_profile,
)
from interlock3d.core.compensation import CompensationError, CompensationTable
from interlock3d.core.export import build_export_plan, export_plan, format_report
from interlock3d.core.feature_detection import DetectionConfig, detect_features, feature_id_per_face
from interlock3d.core.features import Feature
from interlock3d.core.geometry_modification import GeometryModificationError
from interlock3d.core.mesh_loader import MeshLoadError, feature_overlay_polydata, load_trimesh, trimesh_to_pyvista
from interlock3d.core.pairing import FIT_TYPE_LABELS, FeaturePair, FeatureRef, are_types_compatible
from interlock3d.gui import feature_colors
from interlock3d.gui.calibration_wizard import CalibrationWizard
from interlock3d.gui.fit_type_dialog import FitTypeDialog
from interlock3d.gui.report_dialog import ReportDialog
from interlock3d.gui.viewer import MeshViewer

_OVERLAY_OFFSET_FRACTION = 0.0015
_OVERLAY_OFFSET_MIN = 0.01


class MainWindow(QMainWindow):
    def __init__(self, calibration_path: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle("Interlock3D")
        self.resize(1300, 850)

        self._parts: dict[str, object] = {}  # name -> trimesh.Trimesh
        self._part_features: dict[str, list[Feature]] = {}
        self._pairs: list[FeaturePair] = []
        self._pending: FeatureRef | None = None
        self._pair_color_cycle = itertools.cycle(feature_colors.PAIR_COLORS)
        self._compensation_table = CompensationTable.load()
        # Overridable (defaults to ~/.interlock3d/calibration.json) so tests
        # can point this at a tmp_path instead of the real home directory --
        # otherwise one test's saved calibration would leak into the next
        # test's fresh MainWindow instance.
        self._calibration_path = calibration_path or DEFAULT_CALIBRATION_PATH
        try:
            self._active_calibration: CalibrationProfile | None = load_calibration_profile(self._calibration_path)
        except CalibrationError:
            # A corrupt saved profile shouldn't prevent the app from
            # starting -- fall back to no calibration (generic defaults),
            # same as a user who's never calibrated.
            self._active_calibration = None

        self.viewer = MeshViewer(self)
        self.viewer.enable_feature_picking(self._on_feature_clicked)

        self.part_list = QListWidget(self)
        self.pairs_list = QListWidget(self)

        side_panel = self._build_side_panel()

        splitter = QSplitter(self)
        splitter.addWidget(side_panel)
        splitter.addWidget(self.viewer)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.setCentralWidget(splitter)

        self.setStatusBar(QStatusBar(self))
        self._build_toolbar()

    def _build_side_panel(self) -> QWidget:
        panel = QWidget(self)
        panel.setMaximumWidth(300)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(4, 4, 4, 4)

        layout.addWidget(QLabel("Parts"))
        layout.addWidget(self.part_list)

        layout.addWidget(QLabel("Pairs"))
        layout.addWidget(self.pairs_list)

        pair_buttons = QHBoxLayout()
        self.remove_pair_btn = QPushButton("Remove Pair", panel)
        self.remove_pair_btn.clicked.connect(self._remove_selected_pair)
        pair_buttons.addWidget(self.remove_pair_btn)

        self.change_fit_btn = QPushButton("Change Fit Type...", panel)
        self.change_fit_btn.clicked.connect(self._change_selected_pair_fit_type)
        pair_buttons.addWidget(self.change_fit_btn)
        layout.addLayout(pair_buttons)

        return panel

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

        toolbar.addSeparator()
        toolbar.addWidget(QLabel(" Material: "))
        self.material_combo = QComboBox(self)
        self.material_combo.addItems(self._compensation_table.materials())
        default_index = self.material_combo.findText("PLA")
        if default_index >= 0:
            self.material_combo.setCurrentIndex(default_index)
        toolbar.addWidget(self.material_combo)

        export_action = QAction("Export Corrected STL(s)...", self)
        export_action.triggered.connect(self.export_corrected)
        toolbar.addAction(export_action)

        toolbar.addSeparator()
        calibrate_action = QAction("Calibrate Printer...", self)
        calibrate_action.triggered.connect(self.open_calibration_wizard)
        toolbar.addAction(calibrate_action)

        self.clear_calibration_action = QAction("Clear Calibration", self)
        self.clear_calibration_action.triggered.connect(self.clear_calibration)
        toolbar.addAction(self.clear_calibration_action)

        self.calibration_label = QLabel(self)
        toolbar.addWidget(self.calibration_label)
        self._refresh_calibration_label()

    # -- Loading -----------------------------------------------------

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

        name = self._unique_name(Path(path).stem)
        features = detect_features(mesh, DetectionConfig())

        pv_mesh = trimesh_to_pyvista(mesh)
        feature_ids = feature_id_per_face(mesh, features)
        self.viewer.add_part(name, pv_mesh, feature_id_by_face=feature_ids)

        offset = max(mesh.scale * _OVERLAY_OFFSET_FRACTION, _OVERLAY_OFFSET_MIN)
        for idx, feature in enumerate(features):
            overlay = feature_overlay_polydata(mesh, feature.face_indices, offset)
            self.viewer.add_feature_overlay(
                name, idx, overlay, feature_colors.default_color(feature.feature_type),
                feature_colors.default_opacity(feature.feature_type),
            )

        self._parts[name] = mesh
        self._part_features[name] = features
        self.part_list.addItem(f"{name}  ({len(mesh.faces)} tris, {len(features)} features)")
        self.statusBar().showMessage(f"Loaded {name}: {len(features)} candidate features detected", 4000)

    def _unique_name(self, base: str) -> str:
        name = base
        suffix = 1
        while name in self._parts:
            suffix += 1
            name = f"{base}_{suffix}"
        return name

    # -- Feature pairing -----------------------------------------------

    def _on_feature_clicked(self, part_name: str, feature_index: int | None) -> None:
        if feature_index is None:
            self.statusBar().showMessage("No feature there", 2000)
            return

        feature = self._part_features[part_name][feature_index]
        ref = FeatureRef(part_name, feature_index)

        if self._pending is None:
            existing = self._find_pair(ref)
            if existing is not None:
                self.statusBar().showMessage(
                    f"That feature is already paired ({self._pair_label(existing)}). "
                    "Remove the pair first to re-pair it.",
                    4000,
                )
                return
            self._pending = ref
            self.viewer.set_feature_appearance(
                part_name, feature_index, feature_colors.SELECTED_COLOR, feature_colors.SELECTED_OPACITY
            )
            desc = self._describe_feature(feature)
            self.statusBar().showMessage(
                f"Selected {part_name}: {desc} — click a matching feature on another part to pair."
            )
            return

        if ref == self._pending:
            self.viewer.set_feature_appearance(
                part_name,
                feature_index,
                feature_colors.default_color(feature.feature_type),
                feature_colors.default_opacity(feature.feature_type),
            )
            self._pending = None
            self.statusBar().showMessage("Selection cleared", 2000)
            return

        if ref.part_name == self._pending.part_name:
            self.statusBar().showMessage("Pick a feature on a different part to form a pair.", 3000)
            return

        existing = self._find_pair(ref)
        if existing is not None:
            self.statusBar().showMessage(
                f"That feature is already paired ({self._pair_label(existing)}).", 3000
            )
            return

        pending_feature = self._part_features[self._pending.part_name][self._pending.feature_index]
        if not are_types_compatible(pending_feature.feature_type, feature.feature_type):
            self.statusBar().showMessage(
                f"Can't pair a {pending_feature.feature_type} with a {feature.feature_type} "
                "— only hole+peg or flat+flat make sense as a mating fit.",
                4000,
            )
            return

        self._confirm_pair(self._pending, ref)

    def _confirm_pair(self, first: FeatureRef, second: FeatureRef) -> None:
        dialog = FitTypeDialog(self)
        if dialog.exec() != QDialog.Accepted:
            return  # leave the first feature selected; user can pick a different second one

        pair = FeaturePair(first, second, dialog.selected_fit_type())
        self._pairs.append(pair)

        color = next(self._pair_color_cycle)
        for ref in (first, second):
            self.viewer.set_feature_appearance(
                ref.part_name, ref.feature_index, color, feature_colors.PAIR_OPACITY
            )

        self._pending = None
        self._refresh_pairs_list()
        self.statusBar().showMessage(f"Paired: {self._pair_label(pair)}", 4000)

    def _find_pair(self, ref: FeatureRef) -> FeaturePair | None:
        return next((p for p in self._pairs if p.involves(ref)), None)

    def _describe_feature(self, feature: Feature) -> str:
        bits = [feature.feature_type]
        if feature.radius is not None:
            bits.append(f"r={feature.radius:.2f}mm")
        if feature.extent is not None:
            bits.append(f"extent={feature.extent:.2f}mm")
        return " ".join(bits)

    def _pair_label(self, pair: FeaturePair) -> str:
        feat_a = self._part_features[pair.a.part_name][pair.a.feature_index]
        feat_b = self._part_features[pair.b.part_name][pair.b.feature_index]
        return (
            f"{pair.a.part_name}:{feat_a.feature_type} <-> "
            f"{pair.b.part_name}:{feat_b.feature_type} ({FIT_TYPE_LABELS[pair.fit_type]})"
        )

    def _refresh_pairs_list(self) -> None:
        self.pairs_list.clear()
        for pair in self._pairs:
            self.pairs_list.addItem(self._pair_label(pair))

    def _remove_selected_pair(self) -> None:
        row = self.pairs_list.currentRow()
        if row < 0:
            return
        pair = self._pairs.pop(row)
        for ref in (pair.a, pair.b):
            feature = self._part_features[ref.part_name][ref.feature_index]
            self.viewer.set_feature_appearance(
                ref.part_name,
                ref.feature_index,
                feature_colors.default_color(feature.feature_type),
                feature_colors.default_opacity(feature.feature_type),
            )
        self._refresh_pairs_list()
        self.statusBar().showMessage("Pair removed", 2000)

    def _change_selected_pair_fit_type(self) -> None:
        row = self.pairs_list.currentRow()
        if row < 0:
            return
        pair = self._pairs[row]
        dialog = FitTypeDialog(self, initial=pair.fit_type)
        if dialog.exec() != QDialog.Accepted:
            return
        pair.fit_type = dialog.selected_fit_type()
        self._refresh_pairs_list()
        self.pairs_list.setCurrentRow(row)

    # -- Export -----------------------------------------------------

    def export_corrected(self) -> None:
        if not self._parts:
            self.statusBar().showMessage("Nothing loaded to export.", 3000)
            return

        material = self.material_combo.currentText()
        try:
            plan = build_export_plan(
                self._pairs,
                self._parts,
                self._part_features,
                material=material,
                table=self._compensation_table,
                calibration=self._active_calibration,
            )
        except CompensationError as exc:
            QMessageBox.critical(self, "Cannot compute compensation", str(exc))
            return
        except GeometryModificationError as exc:
            # e.g. a fit clearance (possibly widened further by an active
            # calibration bias) would shrink a peg past its own radius --
            # analytically invalid, raised before any geometry is touched.
            QMessageBox.critical(self, "Cannot apply compensation", str(exc))
            return

        if plan.has_integrity_problems:
            problems = ", ".join(plan.problem_parts())
            QMessageBox.critical(
                self,
                "Export blocked: geometry problem",
                f"The requested fit produced invalid geometry for: {problems}.\n\n"
                "This usually means a compensation value is too large for the feature's size "
                "(e.g. shrinking a peg or growing a hole beyond what the surrounding part can "
                "support). Try a smaller fit clearance, a different material, or check the "
                "affected pair's fit type.\n\n"
                f"{format_report(plan)}",
            )
            return

        output_dir = QFileDialog.getExistingDirectory(self, "Choose export folder")
        if not output_dir:
            return

        written = export_plan(plan, output_dir)
        file_count = len(written) - 1  # exclude the report entry
        self.statusBar().showMessage(f"Exported {file_count} STL(s) and a report to {output_dir}", 5000)

        dialog = ReportDialog(self, format_report(plan))
        dialog.exec()

    # -- Calibration --------------------------------------------------

    def open_calibration_wizard(self) -> None:
        wizard = CalibrationWizard(self)
        if wizard.exec() != QDialog.Accepted:
            return

        profile = wizard.resulting_profile()
        self._active_calibration = profile
        try:
            save_calibration_profile(profile, self._calibration_path)
            self.statusBar().showMessage(
                f"Calibration saved and active: hole {profile.hole_radius_bias_mm:+.3f}mm, "
                f"peg {profile.peg_radius_bias_mm:+.3f}mm",
                5000,
            )
        except OSError as exc:
            QMessageBox.warning(
                self,
                "Calibration active but not saved",
                f"Using this calibration for the rest of this session, but couldn't save it to disk "
                f"for next time: {exc}",
            )
        self._refresh_calibration_label()

    def clear_calibration(self) -> None:
        self._active_calibration = None
        self._calibration_path.unlink(missing_ok=True)
        self._refresh_calibration_label()
        self.statusBar().showMessage("Calibration cleared; exports will use generic material defaults.", 3000)

    def _refresh_calibration_label(self) -> None:
        if self._active_calibration is None:
            self.calibration_label.setText(" Calibration: none (generic defaults) ")
        else:
            c = self._active_calibration
            suffix = f" ({c.label})" if c.label else ""
            self.calibration_label.setText(f" Calibration: active{suffix} ")

    # -- Housekeeping -----------------------------------------------

    def clear_all(self) -> None:
        self.viewer.clear_parts()
        self._parts.clear()
        self._part_features.clear()
        self._pairs.clear()
        self._pending = None
        self._pair_color_cycle = itertools.cycle(feature_colors.PAIR_COLORS)
        self.part_list.clear()
        self.pairs_list.clear()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.viewer.shutdown()
        super().closeEvent(event)
