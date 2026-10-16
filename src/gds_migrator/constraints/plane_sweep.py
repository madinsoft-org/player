"""Generate axis constraints by sweeping rectangles or polygon boundary segments.

Polygon spacing candidates are found from their individual boundary-segment
projections; polygon bounding boxes are not used for spacing interactions.
"""

from __future__ import annotations

import hashlib
import json
from bisect import bisect_right
import math
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from gds_migrator.geometry.transforms import Point

from gds_migrator.constraints.graph import ConstraintGraph
from gds_migrator.technology.rules import TechnologyRules

if TYPE_CHECKING:
    from gds_migrator.hierarchy.model import Polygon


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


@dataclass(frozen=True)
class Segment:
    """A polygon boundary segment with solver variables at both endpoints."""

    id: str
    shape_id: str
    layer: int
    datatype: int
    start: Point
    end: Point
    start_x_node: str
    start_y_node: str
    end_x_node: str
    end_y_node: str


def _rectangle_segments(rectangles: list[Rectangle]) -> list[Segment]:
    result = []
    for rect in rectangles:
        corners = (
            (Point(rect.xmin, rect.ymin), f"{rect.id}:lo", f"{rect.id}:lo"),
            (Point(rect.xmax, rect.ymin), f"{rect.id}:hi", f"{rect.id}:lo"),
            (Point(rect.xmax, rect.ymax), f"{rect.id}:hi", f"{rect.id}:hi"),
            (Point(rect.xmin, rect.ymax), f"{rect.id}:lo", f"{rect.id}:hi"),
        )
        for index in range(4):
            start, end = corners[index], corners[(index + 1) % 4]
            result.append(
                Segment(
                    id=f"{rect.id}:segment{index}",
                    shape_id=rect.id,
                    layer=rect.layer,
                    datatype=rect.datatype,
                    start=start[0],
                    end=end[0],
                    start_x_node=start[1],
                    start_y_node=start[2],
                    end_x_node=end[1],
                    end_y_node=end[2],
                )
            )
    return result


def _polygon_segments(polygons: list[Polygon]) -> list[Segment]:
    result = []
    for polygon in polygons:
        for index, start in enumerate(polygon.points):
            end = polygon.points[(index + 1) % len(polygon.points)]
            result.append(
                Segment(
                    id=f"{polygon.id}:segment{index}",
                    shape_id=polygon.id,
                    layer=polygon.layer,
                    datatype=polygon.datatype,
                    start=start,
                    end=end,
                    start_x_node=f"{polygon.id}:p{index}:x",
                    start_y_node=f"{polygon.id}:p{index}:y",
                    end_x_node=f"{polygon.id}:p{(index + 1) % len(polygon.points)}:x",
                    end_y_node=f"{polygon.id}:p{(index + 1) % len(polygon.points)}:y",
                )
            )
    return result


def prepare_polygons_for_sweep(
    polygons: list[Polygon],
    rectangles: list[Rectangle] | None = None,
) -> list[Polygon]:
    """Split polygon edges at shared scanline levels so edge gaps are compared pointwise."""
    rectangles = rectangles or []
    all_x_levels = sorted({
        point.x for polygon in polygons for point in polygon.points
    } | {
        coordinate for rect in rectangles for coordinate in (rect.xmin, rect.xmax)
    })
    all_y_levels = sorted({
        point.y for polygon in polygons for point in polygon.points
    } | {
        coordinate for rect in rectangles for coordinate in (rect.ymin, rect.ymax)
    })
    all_x_levels.extend(
        (first + second) / 2
        for first, second in zip(all_x_levels, all_x_levels[1:])
    )
    all_y_levels.extend(
        (first + second) / 2
        for first, second in zip(all_y_levels, all_y_levels[1:])
    )
    all_x_levels.sort()
    all_y_levels.sort()
    prepared = []
    for polygon in polygons:
        points: list[Point] = []
        for index, start in enumerate(polygon.points):
            end = polygon.points[(index + 1) % len(polygon.points)]
            points.append(start)
            parameters = {0.0, 1.0}
            for levels, first, last in (
                (all_x_levels, start.x, end.x),
                (all_y_levels, start.y, end.y),
            ):
                if first == last:
                    continue
                low, high = sorted((first, last))
                begin = bisect_right(levels, low)
                finish = bisect_right(levels, high - 1e-12)
                for level in levels[begin:finish]:
                    parameter = (level - first) / (last - first)
                    if 0 < parameter < 1:
                        parameters.add(parameter)
            points.extend(
                Point(
                    start.x + parameter * (end.x - start.x),
                    start.y + parameter * (end.y - start.y),
                )
                for parameter in sorted(parameters)
                if parameter not in (0.0, 1.0)
            )
        prepared.append(replace(polygon, points=tuple(points)))
    return prepared


def _get_polygon_sweep_levels(
    polygons: list[Polygon],
    rectangles: list[Rectangle],
    axis: str,
) -> list[float]:
    levels = sorted({
        coordinate
        for polygon in polygons
        for point in polygon.points
        for coordinate in ((point.y,) if axis == "x" else (point.x,))
    } | {
        coordinate
        for rect in rectangles
        for coordinate in (
            (rect.ymin, rect.ymax) if axis == "x" else (rect.xmin, rect.xmax)
        )
    })
    return [
        (first + second) / 2
        for first, second in zip(levels, levels[1:])
        if first < second
    ]


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
    polygons: list[Polygon] | None = None,
    polygons_prepared: bool = False,
    polygon_sweep_levels: list[float] | None = None,
) -> ConstraintGraph:
    """Build the constraint graph for one axis.

    Args:
        rectangles: The flat set of shapes to constrain.
        tech: Technology design rules (min width, min spacing).
        axis: ``"x"`` or ``"y"``.
        rigid_sizes: Optional per-rectangle forced minimum size along
            ``axis`` (e.g. the fixed footprint of an already-migrated
            child instance), overriding the layer's ``min_width`` rule.
        polygons: Polygon outlines whose exact vertices and boundary segments
            participate in width and spacing constraint generation.
        polygons_prepared: Set when ``polygons`` have already been split at
            shared scanline levels by :func:`prepare_polygons_for_sweep`.
        polygon_sweep_levels: Interior scanline coordinates computed from
            the original geometry before its segments are split.
    """
    if axis not in ("x", "y"):
        raise ValueError(f"axis must be 'x' or 'y', got {axis!r}")
    rigid_sizes = rigid_sizes or {}

    graph = ConstraintGraph()
    polygons = polygons or []
    if polygons and not polygons_prepared:
        if polygon_sweep_levels is None:
            polygon_sweep_levels = _get_polygon_sweep_levels(polygons, rectangles, axis)
        polygons = prepare_polygons_for_sweep(polygons, rectangles)
    segment_mode = bool(polygons)
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
            if spacing > 0 and ortho_overlaps and not segment_mode:
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
    if polygons:
        for polygon in polygons:
            if len(polygon.points) < 2:
                continue
            axis_values = [point.x if axis == "x" else point.y for point in polygon.points]
            low_value = min(axis_values)
            high_value = max(axis_values)
            low_nodes = [
                f"{polygon.id}:p{index}:{axis}"
                for index, value in enumerate(axis_values)
                if value == low_value
            ]
            high_nodes = [
                f"{polygon.id}:p{index}:{axis}"
                for index, value in enumerate(axis_values)
                if value == high_value
            ]
            for node in [*low_nodes, *high_nodes]:
                graph.add_node(node)
            width = tech.min_width_for(polygon.layer, polygon.datatype)
            if width > 0:
                for low_node in low_nodes:
                    for high_node in high_nodes:
                        graph.add_constraint(
                            low_node,
                            high_node,
                            width,
                            constraint_id=_constraint_id(
                                axis,
                                "minimum_width",
                                polygon.id,
                                polygon.id,
                            ),
                            metadata={
                                "kind": "minimum_width",
                                "axis": axis,
                                "source_rect": polygon.id,
                                "target_rect": polygon.id,
                                "source_layer": polygon.layer,
                                "target_layer": polygon.layer,
                                "source_datatype": polygon.datatype,
                                "target_datatype": polygon.datatype,
                                "rule_value": width,
                                "rule_id": tech.rule_id_for_width(polygon.layer, polygon.datatype),
                            },
                        )

        segments = [*_rectangle_segments(rectangles), *_polygon_segments(polygons)]
        shape_segments: dict[str, Segment] = {}
        for segment in segments:
            shape_segments.setdefault(segment.shape_id, segment)
        sweep_levels = polygon_sweep_levels or []
        for orthogonal_coordinate in sweep_levels:
            cross_sections = _cross_section_intervals(
                segments,
                axis,
                orthogonal_coordinate,
            )
            shape_ids = sorted(cross_sections)
            for first_index, first_id in enumerate(shape_ids):
                first_shape = shape_segments[first_id]
                for second_id in shape_ids[first_index + 1 :]:
                    second_shape = shape_segments[second_id]
                    first_intervals = cross_sections[first_id]
                    second_intervals = cross_sections[second_id]

                    overlap = tech.min_overlap_for(
                        first_shape.layer,
                        second_shape.layer,
                        first_shape.datatype,
                        second_shape.datatype,
                    )
                    if overlap > 0:
                        for first_interval in first_intervals:
                            for second_interval in second_intervals:
                                if not (
                                    first_interval[0][0] < second_interval[1][0]
                                    and second_interval[0][0] < first_interval[1][0]
                                ):
                                    continue
                                _add_segment_constraint(
                                    graph,
                                    axis=axis,
                                    kind="minimum_overlap",
                                    upstream=first_interval[0][1],
                                    downstream=second_interval[1][1],
                                    value=overlap,
                                    source=first_shape,
                                    target=second_shape,
                                    rule_id=tech.rule_id_for_overlap(
                                        first_shape.layer,
                                        second_shape.layer,
                                        first_shape.datatype,
                                        second_shape.datatype,
                                    ),
                                )
                                _add_segment_constraint(
                                    graph,
                                    axis=axis,
                                    kind="minimum_overlap",
                                    upstream=second_interval[0][1],
                                    downstream=first_interval[1][1],
                                    value=overlap,
                                    source=second_shape,
                                    target=first_shape,
                                    rule_id=tech.rule_id_for_overlap(
                                        second_shape.layer,
                                        first_shape.layer,
                                        second_shape.datatype,
                                        first_shape.datatype,
                                    ),
                                )

                    for source_shape, target_shape in (
                        (first_shape, second_shape),
                        (second_shape, first_shape),
                    ):
                        extension = tech.min_extension_for(
                            source_shape.layer,
                            target_shape.layer,
                            source_shape.datatype,
                            target_shape.datatype,
                        )
                        if extension <= 0:
                            continue
                        source_intervals = cross_sections[source_shape.shape_id]
                        target_intervals = cross_sections[target_shape.shape_id]
                        for source_interval in source_intervals:
                            for target_interval in target_intervals:
                                if not (
                                    source_interval[0][0] < target_interval[1][0]
                                    and target_interval[0][0] < source_interval[1][0]
                                ):
                                    continue
                                source_length = (
                                    source_interval[1][0] - source_interval[0][0]
                                )
                                target_length = (
                                    target_interval[1][0] - target_interval[0][0]
                                )
                                reverse_extension = tech.min_extension_for(
                                    target_shape.layer,
                                    source_shape.layer,
                                    target_shape.datatype,
                                    source_shape.datatype,
                                )
                                if (
                                    reverse_extension > 0
                                    and source_length <= target_length
                                ):
                                    continue
                                _add_segment_constraint(
                                    graph,
                                    axis=axis,
                                    kind="minimum_extension",
                                    upstream=source_interval[0][1],
                                    downstream=target_interval[0][1],
                                    value=0,
                                    source=source_shape,
                                    target=target_shape,
                                    rule_id=tech.rule_id_for_extension(
                                        source_shape.layer,
                                        target_shape.layer,
                                        source_shape.datatype,
                                        target_shape.datatype,
                                    ),
                                )
                                _add_segment_constraint(
                                    graph,
                                    axis=axis,
                                    kind="minimum_extension",
                                    upstream=target_interval[1][1],
                                    downstream=source_interval[1][1],
                                    value=extension,
                                    source=source_shape,
                                    target=target_shape,
                                    rule_id=tech.rule_id_for_extension(
                                        source_shape.layer,
                                        target_shape.layer,
                                        source_shape.datatype,
                                        target_shape.datatype,
                                    ),
                                )

        max_spacing = max(tech.min_spacing.values(), default=0)
        ordered_segments = sorted(
            segments,
            key=lambda segment: min(
                segment.start.x if axis == "x" else segment.start.y,
                segment.end.x if axis == "x" else segment.end.y,
            ),
        )
        active_segments: list[Segment] = []
        added_constraints: set[tuple[str, str]] = set()
        for segment in ordered_segments:
            segment_lo, _ = _segment_axis_bounds(segment, axis)
            active_segments = [
                other
                for other in active_segments
                if _segment_axis_bounds(other, axis)[1] + max_spacing >= segment_lo
            ]
            for other in active_segments:
                if other.shape_id == segment.shape_id:
                    continue
                other_ortho = _segment_orthogonal_bounds(other, axis)
                segment_ortho = _segment_orthogonal_bounds(segment, axis)
                if not _overlaps(*other_ortho, *segment_ortho):
                    continue
                spacing = tech.min_spacing_for(
                    segment.layer,
                    other.layer,
                    segment.datatype,
                    other.datatype,
                )
                if spacing <= 0:
                    continue
                overlap_lo = max(other_ortho[0], segment_ortho[0])
                overlap_hi = min(other_ortho[1], segment_ortho[1])
                other_at_lo = _segment_at_orthogonal(other, axis, overlap_lo)
                other_at_hi = _segment_at_orthogonal(other, axis, overlap_hi)
                segment_at_lo = _segment_at_orthogonal(segment, axis, overlap_lo)
                segment_at_hi = _segment_at_orthogonal(segment, axis, overlap_hi)
                if None in (other_at_lo, other_at_hi, segment_at_lo, segment_at_hi):
                    continue
                assert other_at_lo is not None and other_at_hi is not None
                assert segment_at_lo is not None and segment_at_hi is not None
                gaps = (
                    segment_at_lo[0] - other_at_lo[0],
                    segment_at_hi[0] - other_at_hi[0],
                )
                if gaps[0] >= 0 and gaps[1] >= 0:
                    source, target = other, segment
                    node_pairs = (
                        (other_at_lo[1], segment_at_lo[1]),
                        (other_at_hi[1], segment_at_hi[1]),
                    )
                elif gaps[0] <= 0 and gaps[1] <= 0:
                    source, target = segment, other
                    node_pairs = (
                        (segment_at_lo[1], other_at_lo[1]),
                        (segment_at_hi[1], other_at_hi[1]),
                    )
                else:
                    continue
                for upstream, downstream in node_pairs:
                    endpoint_pair = (upstream, downstream)
                    if endpoint_pair in added_constraints:
                        continue
                    added_constraints.add(endpoint_pair)
                    constraint_id = _constraint_id(
                        axis,
                        "spacing",
                        source.shape_id,
                        target.shape_id,
                    )
                    graph.add_constraint(
                        upstream,
                        downstream,
                        spacing,
                        constraint_id=constraint_id,
                        metadata={
                            "kind": "minimum_spacing",
                            "axis": axis,
                            "source_rect": source.shape_id,
                            "target_rect": target.shape_id,
                            "source_layer": source.layer,
                            "target_layer": target.layer,
                            "source_datatype": source.datatype,
                            "target_datatype": target.datatype,
                            "rule_value": spacing,
                            "rule_id": tech.rule_id_for_spacing(
                                source.layer,
                                target.layer,
                                source.datatype,
                                target.datatype,
                            ),
                        },
                    )
            active_segments.append(segment)

    return graph


def _segment_axis_bounds(segment: Segment, axis: str) -> tuple[float, float]:
    values = (
        (segment.start.x, segment.end.x)
        if axis == "x"
        else (segment.start.y, segment.end.y)
    )
    return min(values), max(values)


def _segment_orthogonal_bounds(segment: Segment, axis: str) -> tuple[float, float]:
    return (
        (min(segment.start.y, segment.end.y), max(segment.start.y, segment.end.y))
        if axis == "x"
        else (min(segment.start.x, segment.end.x), max(segment.start.x, segment.end.x))
    )


def _segment_at_orthogonal(
    segment: Segment,
    axis: str,
    coordinate: float,
) -> tuple[float, str] | None:
    if axis == "x":
        start_orth, end_orth = segment.start.y, segment.end.y
        start_axis, end_axis = segment.start.x, segment.end.x
        start_node, end_node = segment.start_x_node, segment.end_x_node
    else:
        start_orth, end_orth = segment.start.x, segment.end.x
        start_axis, end_axis = segment.start.y, segment.end.y
        start_node, end_node = segment.start_y_node, segment.end_y_node

    if start_axis == end_axis and start_node == end_node:
        return start_axis, start_node
    if math.isclose(coordinate, start_orth, abs_tol=1e-9):
        return start_axis, start_node
    if math.isclose(coordinate, end_orth, abs_tol=1e-9):
        return end_axis, end_node
    return None


def _cross_section_intervals(
    segments: list[Segment],
    axis: str,
    orthogonal_coordinate: float,
) -> dict[str, list[tuple[float, str]]]:
    crossings: dict[str, list[tuple[float, str]]] = {}
    for segment in segments:
        start_orth, end_orth = (
            (segment.start.y, segment.end.y)
            if axis == "x"
            else (segment.start.x, segment.end.x)
        )
        if start_orth == end_orth or not (
            min(start_orth, end_orth) <= orthogonal_coordinate <= max(start_orth, end_orth)
        ):
            continue
        crossing = _segment_at_orthogonal(segment, axis, orthogonal_coordinate)
        if crossing is not None:
            crossings.setdefault(segment.shape_id, []).append(crossing)

    intervals = {}
    for shape_id, shape_crossings in crossings.items():
        ordered = sorted(shape_crossings)
        unique = []
        for value, node in ordered:
            if unique and math.isclose(value, unique[-1][0], abs_tol=1e-9):
                continue
            unique.append((value, node))
        intervals[shape_id] = [
            (unique[index], unique[index + 1])
            for index in range(0, len(unique) - 1, 2)
        ]
    return intervals


def _add_segment_constraint(
    graph: ConstraintGraph,
    *,
    axis: str,
    kind: str,
    upstream: str,
    downstream: str,
    value: float,
    source: Segment,
    target: Segment,
    rule_id: str | None,
) -> None:
    graph.add_constraint(
        upstream,
        downstream,
        value,
        constraint_id=_constraint_id(axis, kind, upstream, downstream),
        metadata={
            "kind": kind,
            "axis": axis,
            "source_rect": source.shape_id,
            "target_rect": target.shape_id,
            "source_layer": source.layer,
            "target_layer": target.layer,
            "source_datatype": source.datatype,
            "target_datatype": target.datatype,
            "rule_value": value,
            "rule_id": rule_id,
        },
    )
