"""End-to-end test of Phase 3 feature pairing, driven through the real VTK
picking machinery rather than by calling our Python callbacks directly.

This exists because PyVista's higher-level `enable_element_picking`
resolves the picked cell via `mesh.find_containing_cell(picked_point)`,
which turned out to be unreliable for plain triangulated surfaces (it can
return -1 for a point the picker itself just correctly hit). Calling our
callback directly would never have caught that; only driving an actual
pick through the real VTK picker does.

Requires a display (a real one, or Xvfb) since it creates real Qt widgets
and does real OpenGL rendering. Skips cleanly if that's not available
(e.g. `QT_QPA_PLATFORM=offscreen` still needs system EGL/xcb libs — see
README's "headless Linux" note) rather than failing the whole run.
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

# Constructing QApplication with no usable platform plugin (no DISPLAY, no
# Xvfb, no QT_QPA_PLATFORM override) is a *fatal* Qt abort, not a
# catchable Python exception -- a try/except around it doesn't help, the
# whole process dies. So check for a display before ever touching Qt, and
# skip cleanly instead. On Linux this needs `xvfb-run pytest ...` (or
# QT_QPA_PLATFORM=offscreen plus the system EGL/xcb libs noted in
# README's "headless Linux" section). Windows/Mac have a real windowing
# system without needing DISPLAY, so the check is Linux-only.
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
from PySide6.QtWidgets import QApplication  # noqa: E402

_qapp = QApplication.instance() or QApplication(sys.argv[:1])

from interlock3d.gui import feature_colors  # noqa: E402
from interlock3d.gui.main_window import MainWindow  # noqa: E402

generate_known_parts()


def _find_feature(window: MainWindow, part_name: str, feature_type: str):
    for i, f in enumerate(window._part_features[part_name]):
        if f.feature_type == feature_type:
            return i, f
    raise AssertionError(f"no {feature_type} feature found on {part_name}")


def _visible_point(window: MainWindow, part_name: str, feature) -> np.ndarray:
    """A point on the feature's surface facing the current camera. The
    feature's own `.center` is an axis point for cylinders -- often not on
    (or even facing) any actual visible surface -- so it's the wrong thing
    to click at."""
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
    assert result != 0, f"picker found nothing at world point {world_xyz} (display {dx},{dy})"

    window.viewer._on_picked(picker.GetPickPosition(), picker)


def _accept_active_modal() -> None:
    dialog = QApplication.activeModalWidget()
    assert dialog is not None, "expected a modal fit-type dialog to be open"
    dialog.accept()


@pytest.fixture
def two_part_window(tmp_path):
    # boss_cylinder.stl and plate_with_hole.stl are both centered near the
    # origin and would interpenetrate if loaded as-is -- offset the boss
    # so this test has an unambiguous scene, independent of the separate
    # question of whether the app should auto-arrange overlapping parts.
    boss = trimesh.load(KNOWN_DIR / "boss_cylinder.stl", force="mesh")
    boss.apply_translation([50, 0, 0])
    offset_boss_path = tmp_path / "boss_offset.stl"
    boss.export(offset_boss_path)

    window = MainWindow()
    window.resize(1300, 850)
    window.show()
    _qapp.processEvents()

    window._load_and_add(str(KNOWN_DIR / "plate_with_hole.stl"))
    window._load_and_add(str(offset_boss_path))
    window.viewer.reset_camera()
    _qapp.processEvents()

    yield window

    window.close()


def test_default_overlay_colors_match_feature_type(two_part_window) -> None:
    window = two_part_window
    hole_idx, _ = _find_feature(window, "plate_with_hole", "hole")
    rim_idx, _ = _find_feature(window, "plate_with_hole", "peg")

    hole_actor = window.viewer._feature_actors[("plate_with_hole", hole_idx)]
    rim_actor = window.viewer._feature_actors[("plate_with_hole", rim_idx)]

    assert hole_actor.prop.color.hex_rgb.lower() == feature_colors.default_color("hole").lower()
    assert rim_actor.prop.color.hex_rgb.lower() == feature_colors.default_color("peg").lower()


def test_click_selects_feature_and_highlights_it(two_part_window) -> None:
    window = two_part_window
    rim_idx, rim_feat = _find_feature(window, "plate_with_hole", "peg")

    _simulate_click(window, _visible_point(window, "plate_with_hole", rim_feat))
    _qapp.processEvents()

    assert window._pending is not None
    assert window._pending.part_name == "plate_with_hole"
    assert window._pending.feature_index == rim_idx

    rim_actor = window.viewer._feature_actors[("plate_with_hole", rim_idx)]
    assert rim_actor.prop.color.hex_rgb.lower() == feature_colors.SELECTED_COLOR.lower()


def test_clicking_selected_feature_again_deselects(two_part_window) -> None:
    window = two_part_window
    rim_idx, rim_feat = _find_feature(window, "plate_with_hole", "peg")
    point = _visible_point(window, "plate_with_hole", rim_feat)

    _simulate_click(window, point)
    assert window._pending is not None
    _simulate_click(window, point)

    assert window._pending is None
    rim_actor = window.viewer._feature_actors[("plate_with_hole", rim_idx)]
    assert rim_actor.prop.color.hex_rgb.lower() == feature_colors.default_color("peg").lower()


def test_two_features_on_different_parts_form_a_pair(two_part_window) -> None:
    boss_name = "boss_offset"
    window = two_part_window
    rim_idx, rim_feat = _find_feature(window, "plate_with_hole", "peg")
    peg_idx, peg_feat = _find_feature(window, boss_name, "peg")

    _simulate_click(window, _visible_point(window, "plate_with_hole", rim_feat))
    QTimer.singleShot(150, _accept_active_modal)
    _simulate_click(window, _visible_point(window, boss_name, peg_feat))
    _qapp.processEvents()

    assert window._pending is None
    assert len(window._pairs) == 1
    pair = window._pairs[0]
    assert pair.fit_type == "sliding"

    rim_actor = window.viewer._feature_actors[("plate_with_hole", rim_idx)]
    peg_actor = window.viewer._feature_actors[(boss_name, peg_idx)]
    assert rim_actor.prop.color.hex_rgb == peg_actor.prop.color.hex_rgb
    assert rim_actor.prop.color.hex_rgb.lower() not in (
        feature_colors.default_color("peg").lower(),
        feature_colors.SELECTED_COLOR.lower(),
    )
    assert window.pairs_list.count() == 1


def test_removing_a_pair_reverts_colors(two_part_window) -> None:
    boss_name = "boss_offset"
    window = two_part_window
    rim_idx, rim_feat = _find_feature(window, "plate_with_hole", "peg")
    peg_idx, peg_feat = _find_feature(window, boss_name, "peg")

    _simulate_click(window, _visible_point(window, "plate_with_hole", rim_feat))
    QTimer.singleShot(150, _accept_active_modal)
    _simulate_click(window, _visible_point(window, boss_name, peg_feat))
    _qapp.processEvents()
    assert len(window._pairs) == 1

    window.pairs_list.setCurrentRow(0)
    window._remove_selected_pair()

    assert len(window._pairs) == 0
    rim_actor = window.viewer._feature_actors[("plate_with_hole", rim_idx)]
    peg_actor = window.viewer._feature_actors[(boss_name, peg_idx)]
    assert rim_actor.prop.color.hex_rgb.lower() == feature_colors.default_color("peg").lower()
    assert peg_actor.prop.color.hex_rgb.lower() == feature_colors.default_color("peg").lower()


def test_second_click_on_same_part_is_rejected(two_part_window) -> None:
    window = two_part_window
    rim_idx, rim_feat = _find_feature(window, "plate_with_hole", "peg")
    flat_idx, flat_feat = _find_feature(window, "plate_with_hole", "flat")

    _simulate_click(window, _visible_point(window, "plate_with_hole", rim_feat))
    assert window._pending is not None

    _simulate_click(window, _visible_point(window, "plate_with_hole", flat_feat))
    _qapp.processEvents()

    assert len(window._pairs) == 0
    assert window._pending is not None, "pending selection should be kept, not silently dropped"
    assert window._pending.feature_index == rim_idx
