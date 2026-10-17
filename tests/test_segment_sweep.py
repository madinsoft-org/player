import pytest

from gds_migrator.constraints.graph import InfeasibleConstraintsError
from gds_migrator.constraints.plane_sweep import (
    Rectangle,
    _get_polygon_sweep_levels,
    generate_axis_constraints,
    prepare_polygons_for_sweep,
)
from gds_migrator.constraints.segment_sweep import (
    sweep_layout_with_polygons,
    sweep_segments,
)
from gds_migrator.geometry.transforms import Point
from gds_migrator.hierarchy.migrator import migrate_hierarchy
from gds_migrator.hierarchy.model import Cell, Polygon
from gds_migrator.technology.rules import TechnologyRules


def _tech(
    min_width: dict[int, int] | None = None,
    min_spacing: dict[tuple[int, int], int] | None = None,
) -> TechnologyRules:
    return TechnologyRules(
        name="test",
        grid=1,
        min_width=min_width or {},
        min_spacing=min_spacing or {},
    )


def _key(name: str) -> tuple[str, frozenset[tuple[float, float]]]:
    return name, frozenset({(0.0, 0.0)})


def _violations(
    rectangles: list[Rectangle],
    polygons: list[Polygon],
    tech: TechnologyRules,
) -> list[tuple[str, str, float]]:
    prepared = prepare_polygons_for_sweep(polygons, rectangles)
    violations = []
    for axis in ("x", "y"):
        graph = generate_axis_constraints(
            rectangles, tech, axis, polygons=prepared, polygons_prepared=True,
            polygon_sweep_levels=_get_polygon_sweep_levels(polygons, rectangles, axis),
        )
        positions = {
            f"{polygon.id}:p{index}:{axis}": point.x if axis == "x" else point.y
            for polygon in prepared
            for index, point in enumerate(polygon.points)
        }
        for rect in rectangles:
            positions[f"{rect.id}:lo"] = rect.xmin if axis == "x" else rect.ymin
            positions[f"{rect.id}:hi"] = rect.xmax if axis == "x" else rect.ymax
        violations.extend(
            (upstream, downstream, value)
            for upstream, downstream, value in graph.edges
            if positions[downstream] - positions[upstream] < value - 1e-9
        )
    return violations


def _segment_lengths(polygon: Polygon) -> list[float]:
    points = polygon.points
    return [
        abs(points[index].x - points[index - 1].x) + abs(points[index].y - points[index - 1].y)
        for index in range(len(points))
    ]


def test_start_segments_keep_position_and_others_take_max_of_incoming() -> None:
    a, b, c, d = (_key(name) for name in "abcd")
    positions = sweep_segments(
        {a: 0, b: 50, c: 100, d: 7},
        [(a, b, 5), (d, b, 10), (b, c, 3)],
    )
    assert positions == {a: 0, b: 17, c: 20, d: 7}


def test_sweep_repeats_passes_until_nothing_moves() -> None:
    a, b, c = (_key(name) for name in "abc")
    # c is visited before b (smaller original position), so it needs a second pass.
    positions = sweep_segments({a: 0, b: 10, c: 5}, [(a, b, 4), (b, c, 4)])
    assert positions[c] == 8


def test_positive_cycle_is_reported() -> None:
    a, b, c = (_key(name) for name in "abc")
    with pytest.raises(InfeasibleConstraintsError):
        sweep_segments({a: 0, b: 1, c: 2}, [(a, b, 1), (b, c, 1), (c, b, 1)])


def test_sweep_compacts_gaps_and_shrinks_shapes_to_their_rules() -> None:
    left = Rectangle(id="left", layer=1, xmin=0, xmax=10, ymin=0, ymax=10)
    right = Rectangle(id="right", layer=1, xmin=15, xmax=25, ymin=0, ymax=10)
    tech = _tech(min_width={1: 4}, min_spacing={(1, 1): 10})

    rectangles, _ = sweep_layout_with_polygons([left, right], tech)

    by_id = {rect.id: rect for rect in rectangles}
    assert (by_id["left"].xmin, by_id["left"].xmax) == (0, 4)
    assert (by_id["right"].xmin, by_id["right"].xmax) == (14, 18)
    assert (by_id["left"].ymin, by_id["left"].ymax) == (0, 4)


def test_sweep_grows_shapes_narrower_than_their_width_rule() -> None:
    narrow = Rectangle(id="narrow", layer=1, xmin=0, xmax=2, ymin=0, ymax=2)

    [rect], _ = sweep_layout_with_polygons([narrow], _tech(min_width={1: 5}))

    assert (rect.xmin, rect.xmax, rect.ymin, rect.ymax) == (0, 5, 0, 5)


@pytest.mark.parametrize("reverse_winding", [False, True])
def test_sweep_keeps_polygon_edges_at_least_min_width(reverse_winding: bool) -> None:
    points = (
        Point(0, 0), Point(30, 0), Point(30, 10), Point(10, 10), Point(10, 30), Point(0, 30),
    )
    shape = Polygon(id="l", layer=1, points=points[::-1] if reverse_winding else points)
    neighbour = Polygon(
        id="n", layer=1, points=(Point(36, 0), Point(46, 0), Point(46, 30), Point(36, 30)),
    )
    tech = _tech(min_width={1: 4}, min_spacing={(1, 1): 6})

    _, polygons = sweep_layout_with_polygons([], tech, polygons=[shape, neighbour])

    for polygon in polygons:
        assert min(_segment_lengths(polygon)) >= 4
    assert not _violations([], polygons, tech)
    swept = {polygon.id: polygon for polygon in polygons}
    assert min(point.x for point in swept["n"].points) == 14


def test_sweep_stacks_shapes_without_violations_on_both_axes() -> None:
    anchor = Rectangle(id="anchor", layer=1, xmin=0, xmax=10, ymin=0, ymax=10)
    floor = Rectangle(id="floor", layer=2, xmin=12, xmax=22, ymin=0, ymax=10)
    mover = Rectangle(id="mover", layer=1, xmin=12, xmax=22, ymin=13, ymax=23)
    tech = _tech(min_width={1: 4, 2: 4}, min_spacing={(1, 1): 10, (1, 2): 3})

    rectangles, _ = sweep_layout_with_polygons(
        [anchor, floor, mover], tech,
        rigid_widths={"anchor": 10}, rigid_heights={"anchor": 10},
    )

    by_id = {rect.id: rect for rect in rectangles}
    # The mover no longer faces anything on X, so it slides to the left edge
    # and is then stacked above the anchor at the spacing rule.
    assert (by_id["mover"].xmin, by_id["mover"].ymin) == (0, 20)
    assert not _violations(rectangles, [], tech)


def test_excluded_constraints_are_ignored() -> None:
    left = Rectangle(id="left", layer=1, xmin=0, xmax=10, ymin=0, ymax=10)
    right = Rectangle(id="right", layer=1, xmin=15, xmax=25, ymin=0, ymax=10)
    tech = _tech(min_width={1: 4}, min_spacing={(1, 1): 10})
    graph = generate_axis_constraints([left, right], tech, "x")
    spacing_ids = {
        constraint_id
        for constraint_id, metadata in zip(graph.edge_ids, graph.edge_metadata)
        if metadata["kind"] == "minimum_spacing"
    }

    rectangles, _ = sweep_layout_with_polygons(
        [left, right], tech, excluded_constraint_ids=spacing_ids,
    )

    assert {rect.id: rect.xmin for rect in rectangles}["right"] == 5


def test_hierarchy_migration_accepts_the_sweep_solver() -> None:
    child = Cell(name="child", rectangles=[Rectangle(id="c", layer=1, xmin=0, xmax=10, ymin=0, ymax=10)])
    tech = _tech(min_width={1: 4})

    migrated = migrate_hierarchy(
        {"child": child}, tech, layout_solver=sweep_layout_with_polygons,
    )

    rect = migrated["child"].rectangles[0]
    assert (rect.xmin, rect.xmax, rect.ymin, rect.ymax) == (0, 4, 0, 4)


def test_sweep_compacts_shapes_farther_apart_than_any_rule() -> None:
    left = Rectangle(id="left", layer=1, xmin=0, xmax=10, ymin=0, ymax=10)
    right = Rectangle(id="right", layer=1, xmin=500, xmax=510, ymin=0, ymax=10)
    tech = _tech(min_width={1: 4}, min_spacing={(1, 1): 10})

    rectangles, _ = sweep_layout_with_polygons([left, right], tech)

    assert {rect.id: rect.xmin for rect in rectangles}["right"] == 14


def test_sweep_keeps_enclosed_shapes_inside_without_rules() -> None:
    metal = Rectangle(id="metal", layer=1, xmin=0, xmax=100, ymin=0, ymax=100)
    contact = Rectangle(id="contact", layer=2, xmin=40, xmax=60, ymin=40, ymax=60)
    tech = _tech(min_width={1: 4, 2: 2})

    rectangles, _ = sweep_layout_with_polygons([metal, contact], tech)

    by_id = {rect.id: rect for rect in rectangles}
    outer, inner = by_id["metal"], by_id["contact"]
    assert outer.xmin < inner.xmin < inner.xmax < outer.xmax
    assert outer.ymin < inner.ymin < inner.ymax < outer.ymax


def test_sweep_does_not_let_growing_shapes_cross_other_layers() -> None:
    wide = Rectangle(id="wide", layer=1, xmin=0, xmax=10, ymin=0, ymax=10)
    other = Rectangle(id="other", layer=2, xmin=12, xmax=22, ymin=0, ymax=10)
    tech = _tech(min_width={1: 30})

    rectangles, _ = sweep_layout_with_polygons([wide, other], tech)

    by_id = {rect.id: rect for rect in rectangles}
    assert by_id["wide"].xmax - by_id["wide"].xmin == 30
    assert by_id["other"].xmin > by_id["wide"].xmax


def test_sweep_keeps_polygon_notches_open() -> None:
    u_shape = Polygon(id="u", layer=1, points=(
        Point(0, 0), Point(50, 0), Point(50, 40), Point(40, 40),
        Point(40, 10), Point(10, 10), Point(10, 40), Point(0, 40),
    ))
    tech = _tech(min_width={1: 4})

    _, [polygon] = sweep_layout_with_polygons([], tech, polygons=[u_shape])

    xs = sorted({point.x for point in polygon.points})
    assert len(xs) == 4
    assert min(_segment_lengths(polygon)) >= 4
    assert not _violations([], [polygon], tech)
