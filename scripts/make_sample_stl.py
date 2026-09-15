"""Generate a couple of simple STL files for smoke-testing the viewer.

These are just quick shapes to confirm the load/render pipeline works end
to end. The precise-dimension test parts used to validate feature
detection (Phase 2) will be built separately.
"""

from __future__ import annotations

from pathlib import Path

import trimesh

OUT_DIR = Path(__file__).resolve().parent.parent / "test_data"


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)

    plate = trimesh.creation.box(extents=(40, 40, 5))
    plate.export(OUT_DIR / "sample_plate.stl")

    boss = trimesh.creation.cylinder(radius=6, height=15, sections=64)
    boss.export(OUT_DIR / "sample_boss.stl")

    print(f"Wrote sample STL files to {OUT_DIR}")


if __name__ == "__main__":
    main()
