"""Guided flow: generate a calibration test print, enter what the user
measured with calipers afterward, and derive a printer-specific
compensation profile (Phase 7)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWizard,
    QWizardPage,
)

from interlock3d.core.calibration import (
    CalibrationMeasurement,
    CalibrationProfile,
    compute_calibration_profile,
    generate_calibration_part,
)

_DEFAULT_NOMINAL_RADIUS_MM = 5.0


class _IntroPage(QWizardPage):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setTitle("1. Print a calibration test part")

        layout = QVBoxLayout(self)
        layout.addWidget(
            QLabel(
                "This generates a small test print with one through-hole and one\n"
                "free-standing peg, both the same nominal size. Print it, let it\n"
                "cool, then measure both with calipers on the next page.\n"
            )
        )

        layout.addWidget(QLabel("Nominal test radius:"))
        self.radius_spin = QDoubleSpinBox(self)
        self.radius_spin.setRange(1.0, 20.0)
        self.radius_spin.setDecimals(1)
        self.radius_spin.setSuffix(" mm")
        self.radius_spin.setValue(_DEFAULT_NOMINAL_RADIUS_MM)
        layout.addWidget(self.radius_spin)

        self.export_button = QPushButton("Export Calibration Test STL...", self)
        self.export_button.clicked.connect(self._export)
        layout.addWidget(self.export_button)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Save calibration test STL", "interlock3d_calibration.stl", "STL files (*.stl)"
        )
        if not path:
            return
        mesh = generate_calibration_part(nominal_radius_mm=self.radius_spin.value())
        mesh.export(path)
        self.status_label.setText(f"Wrote {path}")
        QMessageBox.information(self, "Exported", f"Wrote {path}\n\nPrint it, then click Next.")


class _MeasurementPage(QWizardPage):
    def __init__(self, intro_page: _IntroPage, parent=None) -> None:
        super().__init__(parent)
        self.setTitle("2. Enter your caliper measurements")
        self._intro_page = intro_page

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Measured hole diameter:"))
        self.hole_diameter_spin = QDoubleSpinBox(self)
        self.hole_diameter_spin.setRange(0.1, 200.0)
        self.hole_diameter_spin.setDecimals(3)
        self.hole_diameter_spin.setSuffix(" mm")
        self.hole_diameter_spin.valueChanged.connect(self._update_preview)
        layout.addWidget(self.hole_diameter_spin)

        layout.addWidget(QLabel("Measured peg diameter:"))
        self.peg_diameter_spin = QDoubleSpinBox(self)
        self.peg_diameter_spin.setRange(0.1, 200.0)
        self.peg_diameter_spin.setDecimals(3)
        self.peg_diameter_spin.setSuffix(" mm")
        self.peg_diameter_spin.valueChanged.connect(self._update_preview)
        layout.addWidget(self.peg_diameter_spin)

        layout.addWidget(QLabel("Label / notes (optional, e.g. printer + material):"))
        self.label_edit = QLineEdit(self)
        layout.addWidget(self.label_edit)

        self.preview_label = QLabel("")
        layout.addWidget(self.preview_label)

    def initializePage(self) -> None:
        nominal_diameter = self._intro_page.radius_spin.value() * 2
        self.hole_diameter_spin.setValue(nominal_diameter)
        self.peg_diameter_spin.setValue(nominal_diameter)
        self._update_preview()

    def _update_preview(self) -> None:
        measurement = self._measurement()
        hole_bias = measurement.hole_radius_bias_mm()
        peg_bias = measurement.peg_radius_bias_mm()
        self.preview_label.setText(
            f"Derived bias: hole {hole_bias:+.3f}mm/side, peg {peg_bias:+.3f}mm/side "
            f"(positive = printer needs more correction than the generic default assumes)"
        )

    def _measurement(self) -> CalibrationMeasurement:
        return CalibrationMeasurement(
            nominal_radius_mm=self._intro_page.radius_spin.value(),
            measured_hole_diameter_mm=self.hole_diameter_spin.value(),
            measured_peg_diameter_mm=self.peg_diameter_spin.value(),
        )


class _SummaryPage(QWizardPage):
    def __init__(self, measurement_page: _MeasurementPage, parent=None) -> None:
        super().__init__(parent)
        self.setTitle("3. Review and save")
        self._measurement_page = measurement_page

        layout = QVBoxLayout(self)
        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        layout.addWidget(self.summary_label)
        layout.addWidget(
            QLabel("Clicking Finish saves this profile and uses it automatically for future exports.")
        )

    def initializePage(self) -> None:
        measurement = self._measurement_page._measurement()
        profile = compute_calibration_profile(measurement, label=self._measurement_page.label_edit.text())
        self.summary_label.setText(
            f"Nominal test radius: {profile.nominal_radius_mm:.1f}mm\n"
            f"Hole radius bias: {profile.hole_radius_bias_mm:+.3f}mm\n"
            f"Peg radius bias: {profile.peg_radius_bias_mm:+.3f}mm\n"
            f"Label: {profile.label or '(none)'}"
        )


class CalibrationWizard(QWizard):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Calibrate Printer")

        self._intro_page = _IntroPage(self)
        self._measurement_page = _MeasurementPage(self._intro_page, self)
        self._summary_page = _SummaryPage(self._measurement_page, self)

        self.addPage(self._intro_page)
        self.addPage(self._measurement_page)
        self.addPage(self._summary_page)

    def resulting_profile(self) -> CalibrationProfile:
        """Only meaningful after the wizard has been accepted (Finish clicked)."""
        measurement = self._measurement_page._measurement()
        return compute_calibration_profile(measurement, label=self._measurement_page.label_edit.text())
