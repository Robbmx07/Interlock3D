"""Tests for the Phase 4 compensation engine: loading the material/fit
profile table and computing per-feature adjustments for a confirmed pair.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from interlock3d.core.compensation import (
    CompensationError,
    CompensationTable,
    compute_pair_compensation,
)
from interlock3d.core.pairing import FeaturePair, FeatureRef, are_types_compatible


@pytest.fixture(scope="module")
def table() -> CompensationTable:
    return CompensationTable.load()


def test_all_three_materials_present(table: CompensationTable) -> None:
    assert table.materials() == ["ABS", "PETG", "PLA"]


def test_pla_matches_brief_rough_values(table: CompensationTable) -> None:
    # The brief's own starting numbers: press 0.05-0.1, sliding 0.2-0.3,
    # clearance 0.4-0.5 mm/side. PLA is the baseline these were modeled on.
    assert table.get_clearance_range_mm("PLA", "press") == (0.05, 0.10)
    assert table.get_clearance_range_mm("PLA", "sliding") == (0.20, 0.30)
    assert table.get_clearance_range_mm("PLA", "clearance") == (0.40, 0.50)

    for fit_type in ("press", "sliding", "clearance"):
        lo, hi = table.get_clearance_range_mm("PLA", fit_type)
        value = table.get_clearance_mm("PLA", fit_type)
        assert lo <= value <= hi


@pytest.mark.parametrize("material", ["PLA", "PETG", "ABS"])
def test_fit_types_ordered_press_lt_sliding_lt_clearance(table: CompensationTable, material: str) -> None:
    press = table.get_clearance_mm(material, "press")
    sliding = table.get_clearance_mm(material, "sliding")
    clearance = table.get_clearance_mm(material, "clearance")
    assert press < sliding < clearance


@pytest.mark.parametrize("fit_type", ["press", "sliding", "clearance"])
def test_petg_and_abs_widen_from_pla_baseline(table: CompensationTable, fit_type: str) -> None:
    pla = table.get_clearance_mm("PLA", fit_type)
    petg = table.get_clearance_mm("PETG", fit_type)
    abs_ = table.get_clearance_mm("ABS", fit_type)
    assert pla < petg < abs_


def test_unknown_material_raises(table: CompensationTable) -> None:
    with pytest.raises(CompensationError):
        table.get_clearance_mm("Nylon", "sliding")


def test_unknown_fit_type_raises(table: CompensationTable) -> None:
    with pytest.raises(CompensationError):
        table.get_clearance_mm("PLA", "interference")  # type: ignore[arg-type]


def test_hole_peg_pair_default_split(table: CompensationTable) -> None:
    pair = FeaturePair(FeatureRef("plate", 0), FeatureRef("boss", 0), fit_type="sliding")
    result = compute_pair_compensation(pair, "hole", "peg", "PLA", table=table)

    clearance = table.get_clearance_mm("PLA", "sliding")
    assert result.total_clearance_mm == pytest.approx(clearance)
    assert result.adjustment_a.material_removed_mm == pytest.approx(clearance / 2)
    assert result.adjustment_b.material_removed_mm == pytest.approx(clearance / 2)
    # both adjustments are always non-negative -- direction is implied by feature_type
    assert result.adjustment_a.material_removed_mm >= 0
    assert result.adjustment_b.material_removed_mm >= 0
    # the two adjustments sum to exactly the target total clearance
    assert (
        result.adjustment_a.material_removed_mm + result.adjustment_b.material_removed_mm
        == pytest.approx(clearance)
    )


def test_hole_peg_pair_order_independent(table: CompensationTable) -> None:
    pair_hp = FeaturePair(FeatureRef("plate", 0), FeatureRef("boss", 0), fit_type="press")
    pair_ph = FeaturePair(FeatureRef("boss", 0), FeatureRef("plate", 0), fit_type="press")

    result_hp = compute_pair_compensation(pair_hp, "hole", "peg", "ABS", table=table)
    result_ph = compute_pair_compensation(pair_ph, "peg", "hole", "ABS", table=table)

    # whichever ref is "a" or "b", the hole-typed adjustment and the
    # peg-typed adjustment should be identical between the two calls
    hole_adj_1 = result_hp.adjustment_a if result_hp.adjustment_a.feature_type == "hole" else result_hp.adjustment_b
    hole_adj_2 = result_ph.adjustment_a if result_ph.adjustment_a.feature_type == "hole" else result_ph.adjustment_b
    assert hole_adj_1.material_removed_mm == pytest.approx(hole_adj_2.material_removed_mm)


def test_custom_split_ratio_favors_hole(table: CompensationTable) -> None:
    pair = FeaturePair(FeatureRef("plate", 0), FeatureRef("boss", 0), fit_type="clearance")
    result = compute_pair_compensation(pair, "hole", "peg", "PETG", split_ratio=0.8, table=table)

    clearance = table.get_clearance_mm("PETG", "clearance")
    assert result.adjustment_a.material_removed_mm == pytest.approx(clearance * 0.8)
    assert result.adjustment_b.material_removed_mm == pytest.approx(clearance * 0.2)


def test_flat_flat_pair_always_splits_evenly_regardless_of_split_ratio(table: CompensationTable) -> None:
    pair = FeaturePair(FeatureRef("lid", 0), FeatureRef("box", 1), fit_type="sliding")
    result = compute_pair_compensation(pair, "flat", "flat", "PLA", split_ratio=0.9, table=table)

    clearance = table.get_clearance_mm("PLA", "sliding")
    assert result.adjustment_a.material_removed_mm == pytest.approx(clearance / 2)
    assert result.adjustment_b.material_removed_mm == pytest.approx(clearance / 2)


@pytest.mark.parametrize(
    "type_a,type_b",
    [("hole", "hole"), ("peg", "peg"), ("hole", "flat"), ("peg", "flat")],
)
def test_incompatible_feature_type_combinations_are_rejected(table: CompensationTable, type_a, type_b) -> None:
    assert not are_types_compatible(type_a, type_b)
    pair = FeaturePair(FeatureRef("a", 0), FeatureRef("b", 0), fit_type="sliding")
    with pytest.raises(CompensationError):
        compute_pair_compensation(pair, type_a, type_b, "PLA", table=table)


def test_invalid_split_ratio_raises(table: CompensationTable) -> None:
    pair = FeaturePair(FeatureRef("a", 0), FeatureRef("b", 0), fit_type="sliding")
    with pytest.raises(ValueError):
        compute_pair_compensation(pair, "hole", "peg", "PLA", split_ratio=1.5, table=table)
