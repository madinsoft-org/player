"""Segment-level constraint sweep: place every boundary segment just tight.

Unlike :func:`gds_migrator.constraints.solver.solve_layout_with_polygons`,
which moves polygon vertices independently and never below their original
coordinate, the sweep moves whole boundary segments. For each axis:

1. Every constraint of the plane-sweep graph links two boundary segments
   (its ``start_segment`` and ``end_segment`` metadata).
2. Consecutive segments of one shape are linked by an internal constraint so
   the edge joining them keeps at least the layer minimum width (or one grid
   step when the layer has no width rule); shapes never collapse or fold.
3. Facing segments of all shapes and layers keep their original order (see
   :func:`_ordering_edges`): no boundary crosses another, so polygons keep
   their shape, overlapping shapes stay overlapping and separate shapes stay
   separate. Same-layer facing segments keep the spacing rule at any
   distance.
4. Start segments -- those without any incoming constraint -- are placed at
   the lowest original coordinate, so the layout is compacted as much as the
   constraints allow.
5. Every other segment is placed at the maximum of ``position[upstream] +
   value`` over its incoming constraints. Passes are repeated until no
   segment moves.

Limitations: vertices that do not lie on a segment perpendicular to the axis
(diagonal edges) are translated with the diagonal segment they start, which
may distort non-Manhattan geometry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from gds_migrator.constraints.graph import InfeasibleConstraintsError
from gds_migrator.constraints.plane_sweep import (
    Rectangle,
    _boundary_segments,
    _polygon_segments,
    _rectangle_segments,
    generate_axis_constraints,
)
from gds_migrator.geometry.transforms import Point
from gds_migrator.technology.rules import TechnologyRules

if TYPE_CHECKING:
    from gds_migrator.hierarchy.model import Polygon

SegmentKey = tuple[str, frozenset[tuple[float, float]]]
SweepEdge = tuple[SegmentKey, SegmentKey, float]

_NODE_SHAPE = re.compile(r"^(?P<shape>.+):(?:lo|hi|p\d+:[xy])$")
_COORDINATE_DIGITS = 6
#: Upper bound on X-then-Y sweep rounds before giving up.
MAX_AXIS_ROUNDS = 20


def sweep_layout_with_polygons(
    rectangles: list[Rectangle],
    tech: TechnologyRules,
    polygons: list[Polygon] | None = None,
    rigid_widths: dict[str, float] | None = None,
    rigid_heights: dict[str, float] | None = None,
    excluded_constraint_ids: set[str] | None = None,
) -> tuple[list[Rectangle], list[Polygon]]:
    """Place every segment at the tightest position satisfying all constraints.

    Has the same signature as ``solve_layout_with_polygons`` so both can be
    used interchangeably by the hierarchical migrator. X then Y are swept,
    each on the geometry produced by the previous sweep, and the pair is
    repeated until no segment moves: moving shapes along one axis can bring
    new neighbours face to face along the other axis.

    Raises:
        InfeasibleConstraintsError: If an axis sweep does not converge
            (positive-weight cycle) or the X/Y alternation keeps moving
            segments after ``MAX_AXIS_ROUNDS`` rounds.
    """
    excluded = excluded_constraint_ids or set()
    current = (list(rectangles), list(polygons or []))
    for _ in range(MAX_AXIS_ROUNDS):
        changed = False
        for axis, rigid_sizes in (("x", rigid_widths), ("y", rigid_heights)):
            deltas = _sweep_axis(*current, tech, axis, rigid_sizes, excluded)
            moved = _apply_axis(*current, deltas, axis, tech) if deltas else current
            if moved != current:
                current = moved
                changed = True
        if not changed:
            return current
    raise InfeasibleConstraintsError(
        f"segment sweep still moving after {MAX_AXIS_ROUNDS} X/Y rounds"
    )


def _apply_axis(
    rectangles: list[Rectangle],
    polygons: list[Polygon],
    deltas: dict[SegmentKey, float],
    axis: str,
    tech: TechnologyRules,
) -> tuple[list[Rectangle], list[Polygon]]:
    """Move shape boundaries along ``axis`` by their segment displacement."""

    def moved(shape_id: str, segment: tuple[Point, Point]) -> float:
        return deltas.get(_segment_key(shape_id, segment), 0.0)

    migrated_rectangles = []
    for rect in rectangles:
        lower_left, upper_left = Point(rect.xmin, rect.ymin), Point(rect.xmin, rect.ymax)
        lower_right, upper_right = Point(rect.xmax, rect.ymin), Point(rect.xmax, rect.ymax)
        if axis == "x":
            migrated_rectangles.append(replace(
                rect,
                xmin=tech.snap_to_grid(rect.xmin + moved(rect.id, (lower_left, upper_left))),
                xmax=tech.snap_to_grid(rect.xmax + moved(rect.id, (lower_right, upper_right))),
            ))
        else:
            migrated_rectangles.append(replace(
                rect,
                ymin=tech.snap_to_grid(rect.ymin + moved(rect.id, (lower_left, lower_right))),
                ymax=tech.snap_to_grid(rect.ymax + moved(rect.id, (upper_left, upper_right))),
            ))

    migrated_polygons = []
    for polygon in polygons:
        if len(polygon.points) < 3:
            migrated_polygons.append(polygon)
            continue
        owners = _vertex_segments(polygon, axis)
        points = []
        for index, point in enumerate(polygon.points):
            shift = moved(polygon.id, owners[index])
            points.append(
                Point(tech.snap_to_grid(point.x + shift), point.y)
                if axis == "x"
                else Point(point.x, tech.snap_to_grid(point.y + shift))
            )
        migrated_polygons.append(replace(polygon, points=tuple(points)))
    return migrated_rectangles, migrated_polygons


def _sweep_axis(
    rectangles: list[Rectangle],
    polygons: list[Polygon],
    tech: TechnologyRules,
    axis: str,
    rigid_sizes: dict[str, float] | None,
    excluded: set[str],
) -> dict[SegmentKey, float]:
    """Return the displacement along ``axis`` of every segment that moved."""
    shape_segments = _shape_segments(rectangles, polygons)
    graph = generate_axis_constraints(
        rectangles, tech, axis, rigid_sizes=rigid_sizes, polygons=polygons,
    )
    edges: list[SweepEdge] = []
    excluded_pairs: set[frozenset[SegmentKey]] = set()
    for (upstream, downstream, value), constraint_id, metadata in zip(
        graph.edges, graph.edge_ids, graph.edge_metadata,
    ):
        if "start_segment" not in metadata:
            continue
        edge = (
            _segment_key(_node_shape(upstream), _points(metadata["start_segment"])),
            _segment_key(_node_shape(downstream), _points(metadata["end_segment"])),
            value,
        )
        if constraint_id in excluded:
            excluded_pairs.add(frozenset(edge[:2]))
        else:
            edges.append(edge)
    rigid = set(rigid_sizes or {})
    for shape_id, (layer, datatype, segments) in shape_segments.items():
        if shape_id not in rigid:
            edges.extend(_internal_edges(shape_id, segments, axis, tech, layer, datatype))
    boundaries = _perpendicular_boundaries(rectangles, polygons, shape_segments, axis)
    edges.extend(_ordering_edges(boundaries, edges, tech, excluded_pairs))
    original = {boundary.key: boundary.position for boundary in boundaries}
    original.update(
        (key, _axis_position(key, axis))
        for edge in edges
        for key in edge[:2]
        if key not in original
    )
    if not original:
        return {}
    positions = sweep_segments(original, edges, source_position=min(original.values()))
    return {
        key: positions[key] - original[key]
        for key in positions
        if positions[key] != original[key]
    }


def sweep_segments(
    original_positions: dict[SegmentKey, float],
    edges: list[SweepEdge],
    source_position: float | None = None,
) -> dict[SegmentKey, float]:
    """Place segments at the maximum of their incoming constraints.

    Segments without incoming constraints (start segments) are placed at
    ``source_position``, or keep ``original_positions`` when it is ``None``;
    all others are recomputed each pass, in original-position order, until a
    pass moves nothing.
    """
    incoming: dict[SegmentKey, list[tuple[SegmentKey, float]]] = {}
    for upstream, downstream, value in edges:
        incoming.setdefault(downstream, []).append((upstream, value))
    positions = {
        key: position if key in incoming or source_position is None else source_position
        for key, position in original_positions.items()
    }
    order = sorted(incoming, key=lambda key: original_positions[key])
    for _ in range(len(positions) + 1):
        changed = False
        for key in order:
            target = max(positions[upstream] + value for upstream, value in incoming[key])
            if abs(target - positions[key]) > 1e-9:
                positions[key] = target
                changed = True
        if not changed:
            return positions
    raise InfeasibleConstraintsError(
        "segment sweep did not converge: the constraints contain a positive-weight cycle"
    )


@dataclass(frozen=True)
class _Boundary:
    """A boundary segment perpendicular to the swept axis."""

    key: SegmentKey
    layer: int
    datatype: int
    position: float
    lo: float
    hi: float
    #: +1 when the shape interior lies below ``position``, -1 when above.
    outward: int


def _perpendicular_boundaries(
    rectangles: list[Rectangle],
    polygons: list[Polygon],
    shape_segments: dict[str, tuple[int, int, list[tuple[Point, Point]]]],
    axis: str,
) -> list[_Boundary]:
    """Return every shape edge perpendicular to ``axis`` with its outward side."""
    orientations = {rect.id: 1.0 for rect in rectangles}
    orientations.update((polygon.id, _orientation(polygon.points)) for polygon in polygons)
    boundaries = []
    for shape_id, (layer, datatype, segments) in shape_segments.items():
        orientation = orientations.get(shape_id, 0.0)
        if orientation == 0.0:
            continue
        for start, end in segments:
            if _coordinate(start, axis) != _coordinate(end, axis):
                continue
            run = _orthogonal(end, axis) - _orthogonal(start, axis)
            if run == 0:
                continue
            # For a counter-clockwise boundary the interior lies to the left of
            # the drawing direction, so a vertical edge drawn upward faces +x
            # and a horizontal edge drawn rightward faces -y.
            facing = (1 if run > 0 else -1) * (1 if orientation > 0 else -1)
            boundaries.append(_Boundary(
                key=_segment_key(shape_id, (start, end)),
                layer=layer,
                datatype=datatype,
                position=_coordinate(start, axis),
                lo=min(_orthogonal(start, axis), _orthogonal(end, axis)),
                hi=max(_orthogonal(start, axis), _orthogonal(end, axis)),
                outward=facing if axis == "x" else -facing,
            ))
    return boundaries


def _ordering_edges(
    boundaries: list[_Boundary],
    rule_edges: list[SweepEdge],
    tech: TechnologyRules,
    excluded_pairs: set[frozenset[SegmentKey]] | None = None,
) -> list[SweepEdge]:
    """Keep the original order of facing boundaries so no edge crosses another.

    Boundaries are scanned in axis order; a skyline over the orthogonal axis
    keeps, for each orthogonal coordinate, the last boundary seen. Each new
    boundary is linked to the boundaries it sees, across all layers (no
    crossing) and within its own layer (spacing at any distance). Facing
    boundaries separated by empty space keep the spacing rule unless the user
    excluded that constraint; boundaries originally apart stay at least one
    grid step apart; coincident ones stay coincident unless a design rule
    orders them.
    """
    ruled_pairs = {frozenset((upstream, downstream)) for upstream, downstream, _ in rule_edges}
    excluded_pairs = excluded_pairs or set()
    # Outward-positive boundaries first on ties: they end the shape before
    # the touching shape that starts at the same coordinate.
    ordered = sorted(boundaries, key=lambda boundary: (boundary.position, -boundary.outward))
    global_skyline: list[tuple[float, float, _Boundary]] = []
    layer_skylines: dict[tuple[int, int], list[tuple[float, float, _Boundary]]] = {}
    edges: list[SweepEdge] = []
    seen: set[tuple[SegmentKey, SegmentKey]] = set()
    for boundary in ordered:
        layer_skyline = layer_skylines.setdefault((boundary.layer, boundary.datatype), [])
        visible = _skyline_insert(global_skyline, boundary) + _skyline_insert(layer_skyline, boundary)
        for previous in visible:
            pair = (previous.key, boundary.key)
            if pair in seen or previous.key == boundary.key:
                continue
            seen.add(pair)
            edges.extend(_ordering_pair(previous, boundary, ruled_pairs, excluded_pairs, tech))
    return edges


def _ordering_pair(
    first: _Boundary,
    second: _Boundary,
    ruled_pairs: set[frozenset[SegmentKey]],
    excluded_pairs: set[frozenset[SegmentKey]],
    tech: TechnologyRules,
) -> list[SweepEdge]:
    """Return the order constraints between two facing boundaries."""
    pair = frozenset((first.key, second.key))
    ruled = pair in ruled_pairs
    distance = second.position - first.position
    if distance == 0 and first.outward == second.outward:
        return [] if ruled else [(first.key, second.key, 0.0), (second.key, first.key, 0.0)]
    if ruled:
        # The design rule already orders this pair, possibly reversing an
        # original layout that violates it.
        return []
    value = tech.grid if distance > 0 else 0.0
    if first.outward > 0 > second.outward and distance > 0 and pair not in excluded_pairs:
        value = max(value, tech.min_spacing_for(
            first.layer, second.layer, first.datatype, second.datatype,
        ))
    return [(first.key, second.key, value)]


def _skyline_insert(
    skyline: list[tuple[float, float, _Boundary]],
    boundary: _Boundary,
) -> list[_Boundary]:
    """Cover ``boundary``'s orthogonal span and return the boundaries it hides."""
    visible: list[_Boundary] = []
    kept: list[tuple[float, float, _Boundary]] = []
    for lo, hi, other in skyline:
        if hi <= boundary.lo or lo >= boundary.hi:
            kept.append((lo, hi, other))
            continue
        if other not in visible:
            visible.append(other)
        if lo < boundary.lo:
            kept.append((lo, boundary.lo, other))
        if hi > boundary.hi:
            kept.append((boundary.hi, hi, other))
    kept.append((boundary.lo, boundary.hi, boundary))
    kept.sort(key=lambda item: item[0])
    skyline[:] = kept
    return visible


def _orientation(points: tuple[Point, ...]) -> float:
    """Return the signed area (positive for counter-clockwise outlines)."""
    count = len(points)
    return sum(
        points[index].x * points[(index + 1) % count].y
        - points[(index + 1) % count].x * points[index].y
        for index in range(count)
    ) / 2


def _internal_edges(
    shape_id: str,
    segments: list[tuple[Point, Point]],
    axis: str,
    tech: TechnologyRules,
    layer: int,
    datatype: int,
) -> list[SweepEdge]:
    """Keep every edge parallel to ``axis`` at least ``min_width`` long."""
    minimum = max(tech.min_width_for(layer, datatype), tech.grid)
    edges: list[SweepEdge] = []
    count = len(segments)
    for index, (start, end) in enumerate(segments):
        if _coordinate(start, axis) == _coordinate(end, axis):
            continue
        if _orthogonal(start, axis) != _orthogonal(end, axis):
            continue
        before = _segment_key(shape_id, segments[index - 1])
        after = _segment_key(shape_id, segments[(index + 1) % count])
        if _coordinate(start, axis) > _coordinate(end, axis):
            before, after = after, before
        edges.append((before, after, minimum))
    return edges


def _shape_segments(
    rectangles: list[Rectangle],
    polygons: list[Polygon],
) -> dict[str, tuple[int, int, list[tuple[Point, Point]]]]:
    """Return each shape's merged boundary edges in drawing order."""
    result: dict[str, tuple[int, int, list[tuple[Point, Point]]]] = {}
    for rect in rectangles:
        segments = _rectangle_segments([rect])
        result[rect.id] = (rect.layer, rect.datatype, [(s.start, s.end) for s in segments])
    for polygon in polygons:
        if len(polygon.points) < 3:
            continue
        boundaries = _boundary_segments(_polygon_segments([polygon]))
        merged: list[tuple[Point, Point]] = []
        for index in range(len(polygon.points)):
            segment = boundaries[f"{polygon.id}:segment{index}"]
            if segment not in merged:
                merged.append(segment)
        result[polygon.id] = (polygon.layer, polygon.datatype, merged)
    return result


def _vertex_segments(polygon: Polygon, axis: str) -> list[tuple[Point, Point]]:
    """Return, per vertex, the merged segment whose move drives it on ``axis``.

    A vertex follows the incident segment perpendicular to ``axis``; vertices
    only on diagonal edges follow the edge starting at them.
    """
    boundaries = _boundary_segments(_polygon_segments([polygon]))
    count = len(polygon.points)
    owners = []
    for index in range(count):
        candidates = [
            boundaries[f"{polygon.id}:segment{(index - 1) % count}"],
            boundaries[f"{polygon.id}:segment{index}"],
        ]
        perpendicular = [
            segment for segment in candidates
            if _coordinate(segment[0], axis) == _coordinate(segment[1], axis)
        ]
        owners.append(perpendicular[0] if perpendicular else candidates[1])
    return owners


def _node_shape(node: str) -> str:
    match = _NODE_SHAPE.match(node)
    if match is None:
        raise ValueError(f"unrecognised constraint graph node: {node!r}")
    return match.group("shape")


def _points(segment: list[dict[str, float]]) -> tuple[Point, Point]:
    return Point(segment[0]["x"], segment[0]["y"]), Point(segment[1]["x"], segment[1]["y"])


def _segment_key(shape_id: str, segment: tuple[Point, Point]) -> SegmentKey:
    return shape_id, frozenset(
        (round(point.x, _COORDINATE_DIGITS), round(point.y, _COORDINATE_DIGITS))
        for point in segment
    )


def _axis_position(key: SegmentKey, axis: str) -> float:
    values = [point[0] if axis == "x" else point[1] for point in key[1]]
    return sum(values) / len(values)


def _coordinate(point: Point, axis: str) -> float:
    return point.x if axis == "x" else point.y


def _orthogonal(point: Point, axis: str) -> float:
    return point.y if axis == "x" else point.x
