"""GUI tests for the Phase 7 calibration wizard, through the real
MainWindow. Every MainWindow here is constructed with an explicit
`calibration_path` pointed at pytest's tmp_path -- never the real
`~/.interlock3d/calibration.json` -- both to keep tests isolated from
each other and to avoid touching the real user's home directory.

Requires a display -- see test_pairing_gui.py's module docstring for why
this checks for one before importing Qt at all.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

_has_display = bool(
    os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or os.environ.get("QT_QPA_PLATFORM")
)
if sys.platform.startswith("linux") and not _has_display:
    pytest.skip(
        "No display available for GUI tests (need DISPLAY, Xvfb, or QT_QPA_PLATFORM set). "
        "Run under `xvfb-run pytest ...` -- see README.",
        allow_module_level=True,
    )

from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

_qapp = QApplication.instance() or QApplication(sys.argv[:1])

from interlock3d.core.calibration import load_calibration_profile  # noqa: E402
from interlock3d.gui.calibration_wizard import CalibrationWizard  # noqa: E402
from interlock3d.gui.main_window import MainWindow  # noqa: E402


def _advance_to_summary(wizard: CalibrationWizard, hole_diameter: float, peg_diameter: float, label: str = "") -> None:
    wizard.show()
    _qapp.processEvents()
    wizard.next()  # intro -> measurement
    wizard._measurement_page.hole_diameter_spin.setValue(hole_diameter)
    wizard._measurement_page.peg_diameter_spin.setValue(peg_diameter)
    wizard._measurement_page.label_edit.setText(label)
    wizard.next()  # measurement -> summary
    _qapp.processEvents()


# --- Wizard mechanics, in isolation -----------------------------------------


def test_measurement_page_prefills_nominal_diameter() -> None:
    wizard = CalibrationWizard()
    wizard._intro_page.radius_spin.setValue(6.0)
    wizard.show()
    wizard.next()
    assert wizard._measurement_page.hole_diameter_spin.value() == pytest.approx(12.0)
    assert wizard._measurement_page.peg_diameter_spin.value() == pytest.approx(12.0)
    wizard.close()


def test_measurement_page_preview_updates_live() -> None:
    wizard = CalibrationWizard()
    wizard._intro_page.radius_spin.setValue(5.0)
    wizard.show()
    wizard.next()

    wizard._measurement_page.hole_diameter_spin.setValue(9.7)  # 0.15mm undersized radius
    wizard._measurement_page.peg_diameter_spin.setValue(10.2)  # 0.10mm oversized radius
    text = wizard._measurement_page.preview_label.text()
    assert "+0.150mm" in text
    assert "+0.100mm" in text
    wizard.close()


def test_resulting_profile_reflects_entered_measurements() -> None:
    wizard = CalibrationWizard()
    _advance_to_summary(wizard, hole_diameter=9.7, peg_diameter=10.2, label="Ender 3, PLA")

    profile = wizard.resulting_profile()
    assert profile.nominal_radius_mm == pytest.approx(5.0)
    assert profile.hole_radius_bias_mm == pytest.approx(0.15, abs=1e-6)
    assert profile.peg_radius_bias_mm == pytest.approx(0.10, abs=1e-6)
    assert profile.label == "Ender 3, PLA"
    wizard.close()


def test_export_calibration_stl_writes_a_file(monkeypatch, tmp_path) -> None:
    wizard = CalibrationWizard()
    out_path = tmp_path / "calibration_test.stl"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *a, **k: (str(out_path), ""))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: QMessageBox.Ok))

    wizard._intro_page._export()

    assert out_path.exists()
    from interlock3d.core.mesh_loader import load_trimesh

    mesh = load_trimesh(out_path)
    assert mesh.is_watertight
    wizard.close()


# --- Through the real MainWindow --------------------------------------------


def test_no_calibration_by_default(tmp_path) -> None:
    window = MainWindow(calibration_path=tmp_path / "calibration.json")
    assert window._active_calibration is None
    assert "none" in window.calibration_label.text().lower()
    window.close()


def test_completing_wizard_saves_and_activates_calibration(monkeypatch, tmp_path) -> None:
    calibration_path = tmp_path / "calibration.json"
    window = MainWindow(calibration_path=calibration_path)

    captured_wizard = {}

    class FakeWizard(CalibrationWizard):
        def exec(self):
            _advance_to_summary(self, hole_diameter=9.7, peg_diameter=10.2, label="test printer")
            captured_wizard["wizard"] = self
            from PySide6.QtWidgets import QDialog

            return QDialog.Accepted

    monkeypatch.setattr("interlock3d.gui.main_window.CalibrationWizard", FakeWizard)

    window.open_calibration_wizard()

    assert window._active_calibration is not None
    assert window._active_calibration.hole_radius_bias_mm == pytest.approx(0.15, abs=1e-6)
    assert window._active_calibration.peg_radius_bias_mm == pytest.approx(0.10, abs=1e-6)
    assert "test printer" in window.calibration_label.text()

    # persisted to the injected path, not the real ~/.interlock3d/
    reloaded = load_calibration_profile(calibration_path)
    assert reloaded == window._active_calibration
    window.close()


def test_cancelling_wizard_leaves_calibration_unchanged(monkeypatch, tmp_path) -> None:
    calibration_path = tmp_path / "calibration.json"
    window = MainWindow(calibration_path=calibration_path)

    class CancelledWizard(CalibrationWizard):
        def exec(self):
            from PySide6.QtWidgets import QDialog

            return QDialog.Rejected

    monkeypatch.setattr("interlock3d.gui.main_window.CalibrationWizard", CancelledWizard)

    window.open_calibration_wizard()

    assert window._active_calibration is None
    assert not calibration_path.exists()
    window.close()


def test_clear_calibration_removes_saved_file(tmp_path) -> None:
    calibration_path = tmp_path / "calibration.json"
    from interlock3d.core.calibration import CalibrationMeasurement, compute_calibration_profile, save_calibration_profile

    profile = compute_calibration_profile(
        CalibrationMeasurement(nominal_radius_mm=5.0, measured_hole_diameter_mm=9.7, measured_peg_diameter_mm=10.2)
    )
    save_calibration_profile(profile, calibration_path)

    window = MainWindow(calibration_path=calibration_path)
    assert window._active_calibration is not None  # loaded on startup

    window.clear_calibration()

    assert window._active_calibration is None
    assert not calibration_path.exists()
    assert "none" in window.calibration_label.text().lower()
    window.close()


def test_export_uses_active_calibration(monkeypatch, tmp_path) -> None:
    """An active calibration should actually change what gets exported --
    confirmed by comparing the report text with and without it."""
    import trimesh

    from interlock3d.core.export import build_export_plan, format_report

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from known_parts import KNOWN_DIR, generate_known_parts

    generate_known_parts()

    boss = trimesh.load(KNOWN_DIR / "boss_cylinder.stl", force="mesh")
    boss.apply_translation([50, 0, 0])
    boss_path = tmp_path / "boss_offset.stl"
    boss.export(boss_path)

    calibration_path = tmp_path / "calibration.json"
    window = MainWindow(calibration_path=calibration_path)
    window._load_and_add(str(KNOWN_DIR / "plate_with_matched_hole.stl"))
    window._load_and_add(str(boss_path))

    hole_idx = next(
        i for i, f in enumerate(window._part_features["plate_with_matched_hole"]) if f.feature_type == "hole"
    )
    peg_idx = next(i for i, f in enumerate(window._part_features["boss_offset"]) if f.feature_type == "peg")

    from interlock3d.core.pairing import FeaturePair, FeatureRef

    pair = FeaturePair(
        FeatureRef("plate_with_matched_hole", hole_idx), FeatureRef("boss_offset", peg_idx), fit_type="sliding"
    )
    window._pairs.append(pair)

    uncalibrated_plan = build_export_plan(
        window._pairs, window._parts, window._part_features, material="PLA", table=window._compensation_table
    )

    from interlock3d.core.calibration import CalibrationMeasurement, compute_calibration_profile

    window._active_calibration = compute_calibration_profile(
        CalibrationMeasurement(nominal_radius_mm=6.0, measured_hole_diameter_mm=11.7, measured_peg_diameter_mm=12.2)
    )

    calibrated_plan = build_export_plan(
        window._pairs,
        window._parts,
        window._part_features,
        material="PLA",
        table=window._compensation_table,
        calibration=window._active_calibration,
    )

    uncalibrated_hole_change = next(c for c in uncalibrated_plan.changes if c.feature_type == "hole")
    calibrated_hole_change = next(c for c in calibrated_plan.changes if c.feature_type == "hole")
    assert calibrated_hole_change.material_removed_mm > uncalibrated_hole_change.material_removed_mm
    assert "calibrated" in format_report(calibrated_plan)
    assert "calibrated" not in format_report(uncalibrated_plan)
    window.close()
