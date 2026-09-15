"""Phase 6: assemble a full compensation plan across every confirmed
pair, apply it to each affected part, and produce a human-readable
change report plus corrected STL files.

This is the module that actually chains Phases 3-5 together: confirmed
pairs (3) -> per-pair compensation (4) -> per-part applied geometry (5)
-> files + report (6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import trimesh

from interlock3d.core.calibration import CalibrationProfile, compute_calibrated_pair_compensation
from interlock3d.core.compensation import CompensationTable, compute_pair_compensation
from interlock3d.core.features import Feature
from interlock3d.core.geometry_modification import (
    MeshIntegrityReport,
    apply_feature_adjustments,
    check_mesh_integrity,
)
from interlock3d.core.pairing import FIT_TYPE_LABELS, FeaturePair


@dataclass(frozen=True)
class ChangeRecord:
    """One feature's adjustment, in human-readable terms, for the report."""

    part_name: str
    feature_type: str
    material_removed_mm: float
    original_radius_mm: float | None
    """Set for hole/peg only."""
    new_radius_mm: float | None
    """Set for hole/peg only."""
    paired_part_name: str
    paired_feature_type: str
    fit_type: str
    material: str
    calibration_applied: bool = False

    def describe(self) -> str:
        why = f"paired with {self.paired_part_name}'s {self.paired_feature_type}, {FIT_TYPE_LABELS[self.fit_type]}, {self.material}"
        if self.calibration_applied:
            why += ", calibrated"
        if self.feature_type == "hole":
            return (
                f"{self.part_name}: hole radius {self.original_radius_mm:.3f}mm -> "
                f"{self.new_radius_mm:.3f}mm (+{self.material_removed_mm:.3f}mm) -- {why}"
            )
        if self.feature_type == "peg":
            return (
                f"{self.part_name}: peg radius {self.original_radius_mm:.3f}mm -> "
                f"{self.new_radius_mm:.3f}mm (-{self.material_removed_mm:.3f}mm) -- {why}"
            )
        return f"{self.part_name}: flat face recessed {self.material_removed_mm:.3f}mm inward -- {why}"


@dataclass
class ExportPlan:
    material: str
    changes: list[ChangeRecord] = field(default_factory=list)
    modified_meshes: dict[str, trimesh.Trimesh] = field(default_factory=dict)
    """Every part passed in, whether or not it had any adjustments applied
    -- an unmodified part is included as an unchanged copy so the whole
    assembly can be exported together."""
    integrity: dict[str, MeshIntegrityReport] = field(default_factory=dict)
    """Only for parts that actually had at least one adjustment applied."""
    calibration: CalibrationProfile | None = None
    """The profile that was active when this plan was built, if any --
    purely informational (already folded into `changes`), kept here so
    the report header can name it."""

    @property
    def has_integrity_problems(self) -> bool:
        return any(not report.is_valid for report in self.integrity.values())

    def problem_parts(self) -> list[str]:
        return [name for name, report in self.integrity.items() if not report.is_valid]


def build_export_plan(
    pairs: list[FeaturePair],
    parts: dict[str, trimesh.Trimesh],
    features_by_part: dict[str, list[Feature]],
    material: str,
    split_ratio: float = 0.5,
    table: CompensationTable | None = None,
    calibration: CalibrationProfile | None = None,
) -> ExportPlan:
    table = table or CompensationTable.load()

    adjustments_by_part: dict[str, list[tuple[int, float]]] = {name: [] for name in parts}
    changes: list[ChangeRecord] = []

    for pair in pairs:
        feat_a = features_by_part[pair.a.part_name][pair.a.feature_index]
        feat_b = features_by_part[pair.b.part_name][pair.b.feature_index]

        # Calibration only measured circular features (the test part has
        # one hole, one peg) -- a flat+flat pair falls back to the
        # uncalibrated computation, same as if no profile were active.
        is_hole_peg = {feat_a.feature_type, feat_b.feature_type} == {"hole", "peg"}
        if calibration is not None and is_hole_peg:
            comp = compute_calibrated_pair_compensation(
                pair, feat_a.feature_type, feat_b.feature_type, material, calibration, split_ratio, table
            )
            applied_calibration = True
        else:
            comp = compute_pair_compensation(
                pair, feat_a.feature_type, feat_b.feature_type, material, split_ratio, table
            )
            applied_calibration = False

        for ref, feature, adjustment, other_ref, other_feature in (
            (pair.a, feat_a, comp.adjustment_a, pair.b, feat_b),
            (pair.b, feat_b, comp.adjustment_b, pair.a, feat_a),
        ):
            delta = adjustment.material_removed_mm
            adjustments_by_part[ref.part_name].append((ref.feature_index, delta))

            if feature.feature_type == "hole":
                original_radius, new_radius = feature.radius, feature.radius + delta
            elif feature.feature_type == "peg":
                original_radius, new_radius = feature.radius, feature.radius - delta
            else:
                original_radius = new_radius = None

            changes.append(
                ChangeRecord(
                    part_name=ref.part_name,
                    feature_type=feature.feature_type,
                    material_removed_mm=delta,
                    original_radius_mm=original_radius,
                    new_radius_mm=new_radius,
                    paired_part_name=other_ref.part_name,
                    paired_feature_type=other_feature.feature_type,
                    fit_type=pair.fit_type,
                    material=material,
                    calibration_applied=applied_calibration,
                )
            )

    modified_meshes: dict[str, trimesh.Trimesh] = {}
    integrity: dict[str, MeshIntegrityReport] = {}
    for name, mesh in parts.items():
        part_adjustments = adjustments_by_part[name]
        if part_adjustments:
            new_mesh = apply_feature_adjustments(mesh, features_by_part[name], part_adjustments)
            modified_meshes[name] = new_mesh
            integrity[name] = check_mesh_integrity(new_mesh)
        else:
            modified_meshes[name] = mesh.copy()

    return ExportPlan(
        material=material,
        changes=changes,
        modified_meshes=modified_meshes,
        integrity=integrity,
        calibration=calibration,
    )


def format_report(plan: ExportPlan) -> str:
    lines = ["Interlock3D Compensation Report", f"Material: {plan.material}"]
    if plan.calibration is not None:
        c = plan.calibration
        label = f" ({c.label})" if c.label else ""
        lines.append(
            f"Calibration: active{label} -- hole bias {c.hole_radius_bias_mm:+.3f}mm, "
            f"peg bias {c.peg_radius_bias_mm:+.3f}mm, from a {c.nominal_radius_mm:.1f}mm test print, "
            f"recorded {c.created_at}"
        )
    else:
        lines.append("Calibration: none (using generic material defaults)")
    lines.append("")

    if not plan.changes:
        lines.append("No feature pairs were confirmed -- all parts exported unmodified.")
        return "\n".join(lines)

    lines.append(f"{len(plan.changes)} feature adjustment(s) across {len(plan.integrity)} modified part(s):")
    lines.append("")
    for change in plan.changes:
        lines.append(f"- {change.describe()}")

    lines.append("")
    lines.append("Mesh integrity after modification:")
    for name, report in plan.integrity.items():
        status = "OK" if report.is_valid else "PROBLEM"
        lines.append(
            f"- {name}: {status} (watertight={report.is_watertight}, "
            f"winding_consistent={report.is_winding_consistent}, "
            f"degenerate_faces={report.has_degenerate_faces}, "
            f"negative_volume={report.has_negative_volume})"
        )

    return "\n".join(lines)


def export_plan(plan: ExportPlan, output_dir: str | Path, suffix: str = "_corrected") -> dict[str, Path]:
    """Write every part in `plan.modified_meshes` plus a report text file
    to `output_dir`. Every exported part gets `suffix` appended, even if
    unmodified -- so exporting to the same directory the originals live
    in can never silently overwrite an input file."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    written: dict[str, Path] = {}
    for name, mesh in plan.modified_meshes.items():
        out_path = output_dir / f"{name}{suffix}.stl"
        mesh.export(out_path)
        written[name] = out_path

    report_path = output_dir / "compensation_report.txt"
    report_path.write_text(format_report(plan))
    written["__report__"] = report_path

    return written
