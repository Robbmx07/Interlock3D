"""Highlight colors/opacities for detected-feature overlays.

Kept separate from viewer.py so MainWindow (which decides *when* a
feature is default/selected/paired) and MeshViewer (which just renders
whatever color/opacity it's told) share one definition of what each state
looks like.
"""

from __future__ import annotations

DEFAULT_COLOR = {
    "hole": "#E74C3C",
    "peg": "#2ECC71",
    "flat": "#5DADE2",
}
DEFAULT_OPACITY = {
    "hole": 0.7,
    "peg": 0.7,
    "flat": 0.22,
}

SELECTED_COLOR = "#FFD54F"
SELECTED_OPACITY = 0.9

PAIR_OPACITY = 0.88
PAIR_COLORS = [
    "#9B59B6",
    "#E67E22",
    "#1ABC9C",
    "#FF6FA5",
    "#8D6E63",
    "#5C6BC0",
    "#F1C40F",
    "#16A085",
]


def default_color(feature_type: str) -> str:
    return DEFAULT_COLOR.get(feature_type, "#AAAAAA")


def default_opacity(feature_type: str) -> float:
    return DEFAULT_OPACITY.get(feature_type, 0.3)
