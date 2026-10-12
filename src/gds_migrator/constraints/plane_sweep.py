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
                },
            )

    # Sweep rectangles in increasing order of their leading edge, keeping a
    # window of recently-seen rectangles whose trailing edge is still close
    # enough (within the largest spacing rule) to possibly constrain the
    # current one.
    max_spacing = max(tech.min_spacing.values(), default=0)
    ordered = sorted(rectangles, key=lambda rect: _axis_bounds(rect, axis)[0])
    active: list[Rectangle] = []
    for rect in ordered:
        rect_lo, _ = _axis_bounds(rect, axis)
        active = [other for other in active if _axis_bounds(other, axis)[1] + max_spacing >= rect_lo]

        ortho_lo, ortho_hi = _orthogonal_bounds(rect, axis)
        for other in active:
            spacing = tech.min_spacing_for(
                rect.layer,
                other.layer,
                rect.datatype,
                other.datatype,
            )
            if spacing <= 0:
                continue
            other_ortho_lo, other_ortho_hi = _orthogonal_bounds(other, axis)
            if not _overlaps(ortho_lo, ortho_hi, other_ortho_lo, other_ortho_hi):
                continue
            other_hi = _axis_bounds(other, axis)[1]
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
                    },
                )

        active.append(rect)
    return graph
