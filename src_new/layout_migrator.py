"""Hierarchical GDSII migration driven by source and target DRC files."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import gdstk

from extract_rules import DrcRule, parse_klayout_drc_file


@dataclass(frozen=True)
class MigrationPlan:
    """Uniform conservative scale selected from matching source/target rules."""

    scale: float
    constraints: tuple[str, ...]


def build_migration_plan(
    source_rules: list[DrcRule], target_rules: list[DrcRule]
) -> MigrationPlan:
    source_by_key = {
        (rule.rule_id, rule.operation): rule
        for rule in source_rules
        if rule.value is not None and rule.value > 0
    }
    ratios: list[tuple[float, str]] = []
    for target in target_rules:
        source = source_by_key.get((target.rule_id, target.operation))
        if source is None or target.value is None or source.value is None:
            continue
        ratio = target.value / source.value
        if ratio > 1:
            ratios.append((ratio, target.rule_id))
    scale = max((ratio for ratio, _ in ratios), default=1.0)
    return MigrationPlan(scale=scale, constraints=tuple(rule_id for _, rule_id in ratios))


def _scale_element(element: object, scale: float) -> None:
    scale_method = getattr(element, "scale", None)
    if scale_method is not None:
        scale_method(scale)


def _scale_repetition(repetition: object, scale: float) -> gdstk.Repetition:
    columns = getattr(repetition, "columns", None)
    rows = getattr(repetition, "rows", None)
    x_offsets = getattr(repetition, "x_offsets", None)
    y_offsets = getattr(repetition, "y_offsets", None)
    if x_offsets is not None:
        return gdstk.Repetition(
            x_offsets=[value * scale for value in x_offsets],
        )
    if y_offsets is not None:
        return gdstk.Repetition(
            y_offsets=[value * scale for value in y_offsets],
        )

    offsets = getattr(repetition, "offsets", None)
    if offsets is not None:
        return gdstk.Repetition(
            offsets=[
                (x * scale, y * scale)
                for x, y in offsets
            ]
        )

    spacing = getattr(repetition, "spacing", None)
    if spacing is not None:
        return gdstk.Repetition(
            columns=columns,
            rows=rows,
            spacing=(spacing[0] * scale, spacing[1] * scale),
        )

    v1 = getattr(repetition, "v1", None)
    v2 = getattr(repetition, "v2", None)
    if v1 is not None and v2 is not None:
        return gdstk.Repetition(
            columns=columns,
            rows=rows,
            v1=(v1[0] * scale, v1[1] * scale),
            v2=(v2[0] * scale, v2[1] * scale),
        )

    raise ValueError("Unsupported repetition type")


def migrate_gds(source_path: str | Path, output_path: str | Path, scale: float) -> None:
    """Scale every cell locally and every reference origin, without flattening."""
    if scale <= 0:
        raise ValueError("scale must be greater than zero")
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(f"Source GDS file not found: {source}")

    library = gdstk.read_gds(source)
    for cell in library.cells:
        for collection_name in ("polygons", "paths", "labels"):
            for element in getattr(cell, collection_name):
                _scale_element(element, scale)
        for reference in cell.references:
            reference.origin = (
                reference.origin[0] * scale,
                reference.origin[1] * scale,
            )
            if reference.repetition is not None:
                reference.repetition = _scale_repetition(reference.repetition, scale)
    library.write_gds(output_path)


def migrate(
    source_gds: str | Path,
    target_gds: str | Path,
    source_drc: str | Path,
    target_drc: str | Path,
) -> MigrationPlan:
    """Build a DRC plan and write a hierarchy-preserving migrated GDS."""
    plan = build_migration_plan(
        parse_klayout_drc_file(source_drc),
        parse_klayout_drc_file(target_drc),
    )
    migrate_gds(source_gds, target_gds, plan.scale)
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-gds", required=True, type=Path)
    parser.add_argument("--source-drc", required=True, type=Path)
    parser.add_argument("--target-drc", required=True, type=Path)
    parser.add_argument("--output-gds", required=True, type=Path)
    args = parser.parse_args()
    plan = migrate(args.source_gds, args.output_gds, args.source_drc, args.target_drc)
    print(f"Migration completed: scale={plan.scale:.6g}, constraints={len(plan.constraints)}")


if __name__ == "__main__":
    main()
