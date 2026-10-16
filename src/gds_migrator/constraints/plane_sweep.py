"""Scanline / plane-sweep generation of the DRC spacing constraint graph.

For a given axis, rectangles are swept in increasing order of their lower
edge. Each time a rectangle enters the sweep, it is compared against the
currently active rectangles (those whose interval on the swept axis still
contains the sweep position): if their projections overlap on the
orthogonal axis and a spacing rule applies to their layer pair, a
minimum-spacing constraint is added between the left rectangle's trailing
edge and the right rectangle's leading edge.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from gds_migrator.constraints.graph import ConstraintGraph
from gds_migrator.technology.rules import TechnologyRules


@dataclass(frozen=True)
class Rectangle:
    """An axis-aligned bounding box on a single layer, identified by ``id``."""

    id: str
    layer: int
    xmin: float
    xmax: float
    ymin: float
    ymax: float
    datatype: int = 0


def _axis_bounds(rect: Rectangle, axis: str) -> tuple[float, float]:
    return (rect.xmin, rect.xmax) if axis == "x" else (rect.ymin, rect.ymax)


def _orthogonal_bounds(rect: Rectangle, axis: str) -> tuple[float, float]:
    return (rect.ymin, rect.ymax) if axis == "x" else (rect.xmin, rect.xmax)


def _overlaps(a_lo: float, a_hi: float, b_lo: float, b_hi: float) -> bool:
    return a_lo < b_hi and b_lo < a_hi


def _constraint_id(axis: str, kind: str, upstream: str, downstream: str) -> str:
    identity = json.dumps(
        [axis, kind, upstream, downstream],
        separators=(",", ":"),
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
    return f"{axis}:{kind}:{digest}"


def generate_axis_constraints(
    rectangles: list[Rectangle],
    tech: TechnologyRules,
    axis: str,
    rigid_sizes: dict[str, float] | None = None,
) -> ConstraintGraph:
    """Build the constraint graph for one axis.

    Args:
        rectangles: The flat set of shapes to constrain.
        tech: Technology design rules (min width, min spacing).
        axis: ``"x"`` or ``"y"``.
        rigid_sizes: Optional per-rectangle forced minimum size along
            ``axis`` (e.g. the fixed footprint of an already-migrated
            child instance), overriding the layer's ``min_width`` rule.
    """
    if axis not in ("x", "y"):
        raise ValueError(f"axis must be 'x' or 'y', got {axis!r}")
    rigid_sizes = rigid_sizes or {}

    graph = ConstraintGraph()
    for rect in rectangles:
        lo_node, hi_node = f"{rect.id}:lo", f"{rect.id}:hi"
        graph.add_node(lo_node)
        graph.add_node(hi_node)
        is_rigid = rect.id in rigid_sizes
        size = rigid_sizes.get(
            rect.id,
            tech.min_width_for(rect.layer, rect.datatype),
        )
        rule_id = tech.rule_id_for_width(rect.layer, rect.datatype) if not is_rigid else None
        if size > 0:
            kind = "fixed_footprint" if is_rigid else "minimum_width"
            graph.add_constraint(
                lo_node,
                hi_node,
                size,
                constraint_id=_constraint_id(axis, kind, lo_node, hi_node),
                metadata={
                    "kind": kind,
                    "axis": axis,
                    "source_rect": rect.id,
                    "target_rect": rect.id,
                    "source_layer": rect.layer,
                    "target_layer": rect.layer,
                    "source_datatype": rect.datatype,
                    "target_datatype": rect.datatype,
                    "rule_value": size,
                    "rule_id": rule_id,
                },
            )

    # Sweep rectangles in increasing order of their leading edge, keeping a
    # window of recently-seen rectangles whose trailing edge is still close
    # enough (within the largest rule value) to possibly constrain the
    # current one.
    max_spacing = max(tech.min_spacing.values(), default=0)
    max_overlap = max(tech.min_overlap.values(), default=0)
    max_extension = max(tech.min_extension.values(), default=0)
    max_rule = max(max_spacing, max_overlap, max_extension)
    ordered = sorted(rectangles, key=lambda rect: _axis_bounds(rect, axis)[0])
    active: list[Rectangle] = []
    for rect in ordered:
        rect_lo, rect_hi = _axis_bounds(rect, axis)
        active = [other for other in active if _axis_bounds(other, axis)[1] + max_rule >= rect_lo]

        ortho_lo, ortho_hi = _orthogonal_bounds(rect, axis)
        for other in active:
            other_lo, other_hi = _axis_bounds(other, axis)
            other_ortho_lo, other_ortho_hi = _orthogonal_bounds(other, axis)
            
            # Check orthogonal overlap (required for all constraint types)
            ortho_overlaps = _overlaps(ortho_lo, ortho_hi, other_ortho_lo, other_ortho_hi)
            
            # Spacing constraint: rectangles must be separated by at least spacing
            spacing = tech.min_spacing_for(
                rect.layer,
                other.layer,
                rect.datatype,
                other.datatype,
            )
            spacing_rule_id = tech.rule_id_for_spacing(
                rect.layer,
                other.layer,
                rect.datatype,
                other.datatype,
            ) if spacing > 0 else None
            if spacing > 0 and ortho_overlaps:
                if other_hi <= rect_lo:
                    upstream = f"{other.id}:hi"
                    downstream = f"{rect.id}:lo"
                    graph.add_constraint(
                        upstream,
                        downstream,
                        spacing,
                        constraint_id=_constraint_id(axis, "spacing", upstream, downstream),
                        metadata={
                            "kind": "minimum_spacing",
                            "axis": axis,
                            "source_rect": other.id,
                            "target_rect": rect.id,
                            "source_layer": other.layer,
                            "target_layer": rect.layer,
                            "source_datatype": other.datatype,
                            "target_datatype": rect.datatype,
                            "rule_value": spacing,
                            "rule_id": spacing_rule_id,
                        },
                    )
            
            # Overlap constraint: rectangles must overlap by at least overlap_value on the swept axis
            overlap = tech.min_overlap_for(
                rect.layer,
                other.layer,
                rect.datatype,
                other.datatype,
            )
            overlap_rule_id = tech.rule_id_for_overlap(
                rect.layer,
                other.layer,
                rect.datatype,
                other.datatype,
            ) if overlap > 0 else None
            if overlap > 0 and ortho_overlaps:
                # Check if rectangles overlap on the swept axis
                axis_overlaps = _overlaps(rect_lo, rect_hi, other_lo, other_hi)
                if axis_overlaps:
                    # Ensure rect extends far enough into other
                    upstream = f"{other.id}:lo"
                    downstream = f"{rect.id}:hi"
                    graph.add_constraint(
                        upstream,
                        downstream,
                        overlap,
                        constraint_id=_constraint_id(axis, "overlap", upstream, downstream),
                        metadata={
                            "kind": "minimum_overlap",
                            "axis": axis,
                            "source_rect": other.id,
                            "target_rect": rect.id,
                            "source_layer": other.layer,
                            "target_layer": rect.layer,
                            "source_datatype": other.datatype,
                            "target_datatype": rect.datatype,
                            "rule_value": overlap,
                            "rule_id": overlap_rule_id,
                        },
                    )
                    # Ensure other extends far enough into rect
                    upstream = f"{rect.id}:lo"
                    downstream = f"{other.id}:hi"
                    graph.add_constraint(
                        upstream,
                        downstream,
                        overlap,
                        constraint_id=_constraint_id(axis, "overlap", upstream, downstream),
                        metadata={
                            "kind": "minimum_overlap",
                            "axis": axis,
                            "source_rect": rect.id,
                            "target_rect": other.id,
                            "source_layer": rect.layer,
                            "target_layer": other.layer,
                            "source_datatype": rect.datatype,
                            "target_datatype": other.datatype,
                            "rule_value": overlap,
                            "rule_id": overlap_rule_id,
                        },
                    )
            
            # Extension constraints: rect must extend other or other must extend rect
            ext_rect_other = tech.min_extension_for(
                rect.layer,
                other.layer,
                rect.datatype,
                other.datatype,
            )
            ext_rect_other_rule_id = tech.rule_id_for_extension(
                rect.layer,
                other.layer,
                rect.datatype,
                other.datatype,
            ) if ext_rect_other > 0 else None
            
            ext_other_rect = tech.min_extension_for(
                other.layer,
                rect.layer,
                other.datatype,
                rect.datatype,
            )
            ext_other_rect_rule_id = tech.rule_id_for_extension(
                other.layer,
                rect.layer,
                other.datatype,
                rect.datatype,
            ) if ext_other_rect > 0 else None
            
            if (ext_rect_other > 0 or ext_other_rect > 0) and ortho_overlaps:
                if ext_rect_other > 0:
                    # rect must extend other: rect must start before or at other and end after other
                    # Constraint 1: other.lo - rect.lo >= 0 (rect starts before or at other)
                    upstream = f"{rect.id}:lo"
                    downstream = f"{other.id}:lo"
                    graph.add_constraint(
                        upstream,
                        downstream,
                        0,
                        constraint_id=_constraint_id(axis, "extension", upstream, downstream),
                        metadata={
                            "kind": "minimum_extension",
                            "axis": axis,
                            "source_rect": rect.id,
                            "target_rect": other.id,
                            "source_layer": rect.layer,
                            "target_layer": other.layer,
                            "source_datatype": rect.datatype,
                            "target_datatype": other.datatype,
                            "rule_value": ext_rect_other,
                            "rule_id": ext_rect_other_rule_id,
                            "extension_direction": "start",
                        },
                    )
                    # Constraint 2: rect.hi - other.hi >= ext_rect_other (rect extends beyond other)
                    upstream = f"{other.id}:hi"
                    downstream = f"{rect.id}:hi"
                    graph.add_constraint(
                        upstream,
                        downstream,
                        ext_rect_other,
                        constraint_id=_constraint_id(axis, "extension", upstream, downstream),
                        metadata={
                            "kind": "minimum_extension",
                            "axis": axis,
                            "source_rect": other.id,
                            "target_rect": rect.id,
                            "source_layer": other.layer,
                            "target_layer": rect.layer,
                            "source_datatype": other.datatype,
                            "target_datatype": rect.datatype,
                            "rule_value": ext_rect_other,
                            "rule_id": ext_rect_other_rule_id,
                            "extension_direction": "end",
                        },
                    )
                if ext_other_rect > 0:
                    # other must extend rect: other must start before or at rect and end after rect
                    # Constraint 1: rect.lo - other.lo >= 0 (other starts before or at rect)
                    upstream = f"{other.id}:lo"
                    downstream = f"{rect.id}:lo"
                    graph.add_constraint(
                        upstream,
                        downstream,
                        0,
                        constraint_id=_constraint_id(axis, "extension", upstream, downstream),
                        metadata={
                            "kind": "minimum_extension",
                            "axis": axis,
                            "source_rect": other.id,
                            "target_rect": rect.id,
                            "source_layer": other.layer,
                            "target_layer": rect.layer,
                            "source_datatype": other.datatype,
                            "target_datatype": rect.datatype,
                            "rule_value": ext_other_rect,
                            "rule_id": ext_other_rect_rule_id,
                            "extension_direction": "start",
                        },
                    )
                    # Constraint 2: other.hi - rect.hi >= ext_other_rect (other extends beyond rect)
                    upstream = f"{rect.id}:hi"
                    downstream = f"{other.id}:hi"
                    graph.add_constraint(
                        upstream,
                        downstream,
                        ext_other_rect,
                        constraint_id=_constraint_id(axis, "extension", upstream, downstream),
                        metadata={
                            "kind": "minimum_extension",
                            "axis": axis,
                            "source_rect": rect.id,
                            "target_rect": other.id,
                            "source_layer": rect.layer,
                            "target_layer": other.layer,
                            "source_datatype": rect.datatype,
                            "target_datatype": other.datatype,
                            "rule_value": ext_other_rect,
                            "rule_id": ext_other_rect_rule_id,
                            "extension_direction": "end",
                        },
                    )

        active.append(rect)
    return graph
