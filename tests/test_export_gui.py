"""End-to-end test of Phase 6 export, driven through the real MainWindow:
a confirmed pair (via real VTK-driven clicks, same approach as
test_pairing_gui.py) -> Export button -> files actually written to disk
and readable back.

Requires a display -- see test_pairing_gui.py's module docstring for why
this checks for one before importing Qt at all.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import numpy as np
import pytest
import trimesh
import vtk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from known_parts import KNOWN_DIR, generate_known_parts

_has_display = bool(
    os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or os.environ.get("QT_QPA_PLATFORM")
)
if sys.platform.startswith("linux") and not _has_display:
    pytest.skip(
        "No display available for GUI tests (need DISPLAY, Xvfb, or QT_QPA_PLATFORM set). "
        "Run under `xvfb-run pytest ...` -- see README.",
        allow_module_level=True,
    )

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox  # noqa: E402

_qapp = QApplication.instance() or QApplication(sys.argv[:1])

import interlock3d.gui.main_window as main_window_module  # noqa: E402
from interlock3d.core.export import ExportPlan  # noqa: E402
from interlock3d.core.feature_detection import DetectionConfig, detect_features  # noqa: E402
from interlock3d.core.geometry_modification import MeshIntegrityReport  # noqa: E402
from interlock3d.core.mesh_loader import load_trimesh  # noqa: E402
from interlock3d.gui.main_window import MainWindow  # noqa: E402

generate_known_parts()


def _find_feature(window: MainWindow, part_name: str, feature_type: str):
    for i, f in enumerate(window._part_features[part_name]):
        if f.feature_type == feature_type:
            return i, f
    raise AssertionError(f"no {feature_type} feature found on {part_name}")


def _visible_point(window: MainWindow, part_name: str, feature) -> np.ndarray:
    mesh = window._parts[part_name]
    camera_pos = np.array(window.viewer.plotter.camera.position)
    centroids = mesh.triangles_center[feature.face_indices]
    normals = mesh.face_normals[feature.face_indices]
    to_camera = camera_pos - centroids
    to_camera /= np.linalg.norm(to_camera, axis=1, keepdims=True)
    facingness = np.sum(normals * to_camera, axis=1)
    return centroids[np.argmax(facingness)]


def _simulate_click(window: MainWindow, world_xyz: np.ndarray) -> None:
    plotter = window.viewer.plotter
    renderer = plotter.renderer
    plotter.render()
    renderer.SetWorldPoint(*world_xyz, 1.0)
    renderer.WorldToDisplay()
    dx, dy, _dz = renderer.GetDisplayPoint()
    picker = vtk.vtkCellPicker()
    picker.SetTolerance(0.01)
    result = picker.Pick(dx, dy, 0, renderer)
    assert result != 0, f"picker found nothing at world point {world_xyz}"
    window.viewer._on_picked(picker.GetPickPosition(), picker)


def _accept_active_modal() -> None:
    dialog = QApplication.activeModalWidget()
    assert dialog is not None, "expected a modal dialog to be open"
    dialog.accept()


@pytest.fixture
def window_with_matched_pair(tmp_path):
    # Offset the boss so it doesn't overlap plate_with_matched_hole (both
    # are centered near the origin as generated) -- same reasoning as
    # test_pairing_gui.py's two_part_window fixture.
    boss = trimesh.load(KNOWN_DIR / "boss_cylinder.stl", force="mesh")
    boss.apply_translation([50, 0, 0])
    offset_boss_path = tmp_path / "boss_offset.stl"
    boss.export(offset_boss_path)

    window = MainWindow()
    window.resize(1300, 850)
    window.show()
    _qapp.processEvents()

    window._load_and_add(str(KNOWN_DIR / "plate_with_matched_hole.stl"))
    window._load_and_add(str(offset_boss_path))
    window.viewer.reset_camera()
    _qapp.processEvents()

    hole_idx, hole_feat = _find_feature(window, "plate_with_matched_hole", "hole")
    peg_idx, peg_feat = _find_feature(window, "boss_offset", "peg")

    _simulate_click(window, _visible_point(window, "plate_with_matched_hole", hole_feat))
    QTimer.singleShot(150, _accept_active_modal)
    _simulate_click(window, _visible_point(window, "boss_offset", peg_feat))
    _qapp.processEvents()
    assert len(window._pairs) == 1, "fixture setup: expected the hole+peg pair to be confirmed"

    yield window

    window.close()


def test_export_with_no_parts_does_nothing(monkeypatch) -> None:
    window = MainWindow()
    called = {"dialog": False}
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: called.__setitem__("dialog", True) or "")

    window.export_corrected()

    assert not called["dialog"], "export with nothing loaded should never open a file dialog"
    window.close()


def test_export_with_no_pairs_writes_unmodified_files(monkeypatch, tmp_path) -> None:
    window = MainWindow()
    window._load_and_add(str(KNOWN_DIR / "flat_plate.stl"))

    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(tmp_path))
    QTimer.singleShot(150, _accept_active_modal)  # closes the ReportDialog

    window.export_corrected()

    out_file = tmp_path / "flat_plate_corrected.stl"
    assert out_file.exists()
    report_text = (tmp_path / "compensation_report.txt").read_text()
    assert "unmodified" in report_text.lower()

    reloaded = load_trimesh(out_file)
    assert reloaded.is_watertight
    window.close()


def test_export_with_confirmed_pair_writes_corrected_files(window_with_matched_pair, tmp_path) -> None:
    window = window_with_matched_pair
    export_dir = tmp_path / "export_out"

    hole_idx, hole = _find_feature(window, "plate_with_matched_hole", "hole")

    def fake_get_dir(*args, **kwargs):
        return str(export_dir)

    import interlock3d.gui.main_window as mw

    mw.QFileDialog.getExistingDirectory = staticmethod(fake_get_dir)
    QTimer.singleShot(150, _accept_active_modal)  # closes the ReportDialog

    window.export_corrected()

    hole_stl = export_dir / "plate_with_matched_hole_corrected.stl"
    peg_stl = export_dir / "boss_offset_corrected.stl"
    report_txt = export_dir / "compensation_report.txt"
    assert hole_stl.exists()
    assert peg_stl.exists()
    assert report_txt.exists()

    report = report_txt.read_text()
    assert "hole radius" in report
    assert "peg radius" in report
    assert "PLA" in report

    modified_mesh = load_trimesh(hole_stl)
    new_features = detect_features(modified_mesh, DetectionConfig())
    new_hole = next(f for f in new_features if f.feature_type == "hole")
    assert new_hole.radius > hole.radius, "exported hole should be larger than the original"


def test_export_blocked_on_integrity_problem_never_opens_file_dialog(monkeypatch, window_with_matched_pair) -> None:
    """A geometry problem must block export before any file dialog opens
    -- simulated by monkeypatching build_export_plan to return a plan that
    reports an integrity problem, since the real known-part fixtures don't
    naturally trigger one at a real material's clearance values."""
    window = window_with_matched_pair

    bad_integrity = MeshIntegrityReport(
        is_watertight=True,
        is_winding_consistent=True,
        has_degenerate_faces=False,
        min_face_area=0.1,
        volume=-5.0,
        has_negative_volume=True,
    )
    fake_plan = ExportPlan(
        material="PLA",
        changes=[],
        modified_meshes={},
        integrity={"plate_with_matched_hole": bad_integrity},
    )
    assert fake_plan.has_integrity_problems  # sanity: the real dataclass logic flags this

    monkeypatch.setattr(main_window_module, "build_export_plan", lambda *a, **k: fake_plan)

    dialog_opened = {"value": False}
    monkeypatch.setattr(
        QFileDialog, "getExistingDirectory", lambda *a, **k: dialog_opened.__setitem__("value", True) or ""
    )

    critical_calls = []
    monkeypatch.setattr(
        QMessageBox, "critical", staticmethod(lambda *a, **k: critical_calls.append(a) or QMessageBox.Ok)
    )

    window.export_corrected()

    assert not dialog_opened["value"], "must not prompt for an export folder when geometry is invalid"
    assert len(critical_calls) == 1, "expected exactly one error dialog"
