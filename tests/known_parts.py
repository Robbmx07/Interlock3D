"""Hand-made test parts with known, deliberate mating features.

These are not arbitrary sample models — every dimension here is a number
we can check the detector's output against. `generate_known_parts` writes
the STLs to disk (for manual inspection in the Phase 1 viewer, or for
GUI-driven Phase 3 work later) and returns the expected feature list for
each, which both the pytest suite and `scripts/validate_phase2.py` check
detection results against.

Assemblies (`plate_with_boss`) are built by concatenating primitives, not
boolean-unioning them, so the result is not a single watertight solid —
that's fine for feature *detection*, which only needs identifiable
surface patches, not global manifoldness. Watertightness is a Phase 5
concern (geometry modification output), validated there instead.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import trimesh

KNOWN_DIR = Path(__file__).resolve().parent.parent / "test_data" / "known"


@dataclass
class ExpectedFeature:
    feature_type: str
    radius: float | None = None
    extent: float | None = None
    area: float | None = None
    radius_tol: float = 0.05
    extent_tol: float = 0.05
    area_tol_frac: float = 0.02

    def describe(self) -> str:
        bits = [self.feature_type]
        if self.radius is not None:
            bits.append(f"r={self.radius}")
        if self.extent is not None:
            bits.append(f"extent={self.extent}")
        if self.area is not None:
            bits.append(f"area={self.area:.1f}")
        return " ".join(bits)


@dataclass
class KnownPart:
    name: str
    path: Path
    description: str
    expected: list[ExpectedFeature] = field(default_factory=list)


def generate_known_parts() -> list[KnownPart]:
    KNOWN_DIR.mkdir(parents=True, exist_ok=True)
    parts = [
        _make_flat_plate(),
        _make_plate_with_hole(),
        _make_boss_cylinder(),
        _make_plate_with_boss(),
    ]
    for part in parts:
        part.path.parent.mkdir(parents=True, exist_ok=True)
    return parts


def _make_flat_plate() -> KnownPart:
    width, depth, thickness = 40.0, 40.0, 5.0
    mesh = trimesh.creation.box(extents=(width, depth, thickness))
    mesh.export(KNOWN_DIR / "flat_plate.stl")

    top_bottom_area = width * depth
    side_area = width * thickness  # square footprint -> both side pairs equal

    return KnownPart(
        name="flat_plate",
        path=KNOWN_DIR / "flat_plate.stl",
        description=f"{width}x{depth}x{thickness}mm box, 6 flat faces",
        expected=[
            ExpectedFeature("flat", area=top_bottom_area),
            ExpectedFeature("flat", area=top_bottom_area),
            ExpectedFeature("flat", area=side_area),
            ExpectedFeature("flat", area=side_area),
            ExpectedFeature("flat", area=side_area),
            ExpectedFeature("flat", area=side_area),
        ],
    )


def _make_plate_with_hole() -> KnownPart:
    r_hole, r_outer, thickness = 5.0, 20.0, 5.0
    mesh = trimesh.creation.annulus(r_min=r_hole, r_max=r_outer, height=thickness, sections=64)
    mesh.export(KNOWN_DIR / "plate_with_hole.stl")

    ring_area = math.pi * (r_outer**2 - r_hole**2)

    return KnownPart(
        name="plate_with_hole",
        path=KNOWN_DIR / "plate_with_hole.stl",
        description=f"disk r={r_outer}mm, thickness={thickness}mm, through-hole r={r_hole}mm (d={2*r_hole}mm)",
        expected=[
            ExpectedFeature("hole", radius=r_hole, extent=thickness),
            ExpectedFeature("peg", radius=r_outer, extent=thickness),  # outer rim: convex cylindrical
            ExpectedFeature("flat", area=ring_area),
            ExpectedFeature("flat", area=ring_area),
        ],
    )


def _make_boss_cylinder() -> KnownPart:
    radius, height = 6.0, 15.0
    mesh = trimesh.creation.cylinder(radius=radius, height=height, sections=64)
    mesh.export(KNOWN_DIR / "boss_cylinder.stl")

    cap_area = math.pi * radius**2

    return KnownPart(
        name="boss_cylinder",
        path=KNOWN_DIR / "boss_cylinder.stl",
        description=f"free-standing cylinder r={radius}mm, height={height}mm",
        expected=[
            ExpectedFeature("peg", radius=radius, extent=height),
            ExpectedFeature("flat", area=cap_area),
            ExpectedFeature("flat", area=cap_area),
        ],
    )


def _make_plate_with_boss() -> KnownPart:
    width, depth, thickness = 40.0, 40.0, 5.0
    boss_radius, boss_total_height, embed = 6.0, 16.0, 1.0

    plate = trimesh.creation.box(extents=(width, depth, thickness))
    boss = trimesh.creation.cylinder(radius=boss_radius, height=boss_total_height, sections=64)
    # Plate top is at z=+thickness/2; boss is generated centered on its own
    # origin (bottom at -boss_total_height/2). Sink it `embed` mm into the
    # plate so `boss_total_height - embed` protrudes above the surface.
    boss_bottom_target_z = thickness / 2 - embed
    dz = boss_bottom_target_z - (-boss_total_height / 2)
    boss.apply_translation([0, 0, dz])
    mesh = trimesh.util.concatenate([plate, boss])
    mesh.export(KNOWN_DIR / "plate_with_boss.stl")

    top_bottom_area = width * depth
    side_area = width * thickness
    cap_area = math.pi * boss_radius**2

    return KnownPart(
        name="plate_with_boss",
        path=KNOWN_DIR / "plate_with_boss.stl",
        description=(
            f"{width}x{depth}x{thickness}mm plate with a r={boss_radius}mm boss "
            f"({boss_total_height - embed}mm exposed, {embed}mm embedded, "
            f"{boss_total_height}mm total generated cylinder height)"
        ),
        expected=[
            ExpectedFeature("peg", radius=boss_radius, extent=boss_total_height),
            ExpectedFeature("flat", area=top_bottom_area),
            ExpectedFeature("flat", area=top_bottom_area),
            ExpectedFeature("flat", area=side_area),
            ExpectedFeature("flat", area=side_area),
            ExpectedFeature("flat", area=side_area),
            ExpectedFeature("flat", area=side_area),
            ExpectedFeature("flat", area=cap_area),
            ExpectedFeature("flat", area=cap_area),
        ],
    )
