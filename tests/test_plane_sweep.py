import pytest

from gds_migrator.constraints.plane_sweep import (
    Rectangle,
    generate_axis_constraints,
    prepare_polygons_for_sweep,
)
from gds_migrator.geometry.transforms import Point
from gds_migrator.hierarchy.model import Polygon
from gds_migrator.technology.rules import TechnologyRules


def _tech(min_spacing: int = 10, min_width: int = 0) -> TechnologyRules:
    return TechnologyRules(
        name="test",
        grid=1,
        min_width={1: min_width} if min_width else {},
        min_spacing={(1, 1): min_spacing},
    )


def test_overlapping_rectangles_get_a_spacing_constraint() -> None:
    # Two same-layer rectangles overlapping in Y, too close in X.
    left = Rectangle(id="left", layer=1, xmin=0.0, xmax=10.0, ymin=0.0, ymax=10.0)
    right = Rectangle(id="right", layer=1, xmin=12.0, xmax=20.0, ymin=0.0, ymax=10.0)

    graph = generate_axis_constraints([left, right], _tech(min_spacing=10), axis="x")
    result = graph.solve({"left:lo": 0.0, "left:hi": 10.0, "right:lo": 12.0, "right:hi": 20.0})

    assert result["right:lo"] - result["left:hi"] >= 10.0


def test_non_overlapping_orthogonal_projection_has_no_constraint() -> None:
    # Same X ordering but disjoint in Y -> no interaction.
    left = Rectangle(id="left", layer=1, xmin=0.0, xmax=10.0, ymin=0.0, ymax=10.0)
    right = Rectangle(id="right", layer=1, xmin=12.0, xmax=20.0, ymin=100.0, ymax=110.0)

    graph = generate_axis_constraints([left, right], _tech(min_spacing=10), axis="x")

    assert len(graph.edges) == 0


def test_min_width_constraint_is_added() -> None:
    rect = Rectangle(id="r", layer=1, xmin=0.0, xmax=5.0, ymin=0.0, ymax=5.0)
    graph = generate_axis_constraints([rect], _tech(min_width=8), axis="x")

    result = graph.solve({"r:lo": 0.0, "r:hi": 5.0})

    assert result["r:hi"] - result["r:lo"] == 8.0


def test_rigid_sizes_override_layer_rule() -> None:
    rect = Rectangle(id="r", layer=1, xmin=0.0, xmax=5.0, ymin=0.0, ymax=5.0)
    graph = generate_axis_constraints([rect], _tech(min_width=1), axis="x", rigid_sizes={"r": 5.0})

    result = graph.solve({"r:lo": 0.0, "r:hi": 5.0})

    assert result["r:hi"] - result["r:lo"] == 5.0
    assert graph.edge_metadata[0]["kind"] == "fixed_footprint"


def test_layer_datatype_rules_are_applied_to_matching_polygons() -> None:
    tech = TechnologyRules(
        name="gf180",
        grid=1,
        min_width={(34, 4): 8},
        min_spacing={((34, 4), (34, 4)): 10},
    )
    left = Rectangle(
        id="left", layer=34, xmin=0.0, xmax=5.0, ymin=0.0, ymax=10.0,
        datatype=4,
    )
    right = Rectangle(
        id="right", layer=34, xmin=7.0, xmax=12.0, ymin=0.0, ymax=10.0,
        datatype=4,
    )

    graph = generate_axis_constraints([left, right], tech, axis="x")
    result = graph.solve({
        "left:lo": 0.0, "left:hi": 5.0,
        "right:lo": 7.0, "right:hi": 12.0,
    })

    assert result["left:hi"] - result["left:lo"] >= 8
    assert result["right:lo"] - result["left:hi"] >= 10


def test_polygon_overlap_rules_create_constraints_from_cross_sections() -> None:
    first = Polygon(
        id="first",
        layer=1,
        points=(Point(0, 0), Point(10, 0), Point(10, 10), Point(0, 10)),
    )
    second = Polygon(
        id="second",
        layer=2,
        points=(Point(8, 0), Point(18, 0), Point(18, 10), Point(8, 10)),
    )
    tech = TechnologyRules(
        name="test",
        grid=1,
        min_overlap={((1, 0), (2, 0)): 3},
        rule_id_overlap={((1, 0), (2, 0)): "overlap-1"},
    )

    graph = generate_axis_constraints([], tech, "x", polygons=[first, second])

    overlap_edges = [
        (edge, metadata)
        for edge, metadata in zip(graph.edges, graph.edge_metadata)
        if metadata.get("kind") == "minimum_overlap"
    ]
    assert len(overlap_edges) == 2
    assert all(edge[2] == 3 for edge, _ in overlap_edges)
    assert all(metadata["rule_id"] == "overlap-1" for _, metadata in overlap_edges)


@pytest.mark.parametrize("axis", ["x", "y"])
@pytest.mark.parametrize("use_polygons", [False, True])
@pytest.mark.parametrize("reverse_winding", [False, True])
def test_overlap_pairs_boundaries_with_same_outward_normal(
    axis: str, use_polygons: bool, reverse_winding: bool,
) -> None:
    outer = Rectangle("outer", 2, 0, 20, 0, 20, datatype=4)
    inner = Rectangle("inner", 1, 5, 15, 5, 15)
    tech = TechnologyRules(
        name="test", grid=1,
        min_overlap={((2, 4), (1, 0)): 3},
        rule_id_overlap={((2, 4), (1, 0)): "outer-overlap"},
    )
    polygons = [
        Polygon(
            id=rect.id, layer=rect.layer, datatype=rect.datatype,
            points=tuple(reversed(points)) if reverse_winding else points,
        )
        for rect in (outer, inner)
        for points in [(
            Point(rect.xmin, rect.ymin), Point(rect.xmax, rect.ymin),
            Point(rect.xmax, rect.ymax), Point(rect.xmin, rect.ymax),
        )]
    ]
    graph = generate_axis_constraints(
        [] if use_polygons else [outer, inner], tech, axis,
        polygons=polygons if use_polygons else None,
    )
    metadata = [
        item for item in graph.edge_metadata
        if item["kind"] == "minimum_overlap"
    ]
    assert metadata
    coordinate = "x" if axis == "x" else "y"
    other_coordinate = "y" if axis == "x" else "x"
    assert {
        (item["start"][coordinate], item["end"][coordinate])
        for item in metadata
    } == {(0, 5), (15, 20)}
    for item in metadata:
        assert item["source_rect"] == "outer"
        assert item["target_rect"] == "inner"
        assert item["rule_id"] == "outer-overlap"
        assert item["start"][other_coordinate] == item["end"][other_coordinate]
    if use_polygons:
        positions = {
            f"{polygon.id}:p{index}:{axis}": getattr(point, coordinate)
            for polygon in prepare_polygons_for_sweep(polygons)
            for index, point in enumerate(polygon.points)
        }
    else:
        positions = {
            f"{rect.id}:{side}": value
            for rect in (outer, inner)
            for side, value in (
                ("lo", rect.xmin if axis == "x" else rect.ymin),
                ("hi", rect.xmax if axis == "x" else rect.ymax),
            )
        }
    assert {
        (positions[upstream], positions[downstream])
        for upstream, downstream, _ in graph.edges
    } == {(0, 5), (15, 20)}
    solved = graph.solve(positions)
    assert all(solved[end] - solved[start] >= 3 for start, end, _ in graph.edges)


@pytest.mark.parametrize("reverse_winding", [False, True])
def test_overlap_uses_local_concave_boundary_not_shape_bounds(
    reverse_winding: bool,
) -> None:
    points = (
        Point(0, 0), Point(20, 0), Point(20, 8),
        Point(8, 8), Point(8, 20), Point(0, 20),
    )
    outer = Polygon(
        id="outer", layer=1,
        points=tuple(reversed(points)) if reverse_winding else points,
    )
    inner = Polygon(
        id="inner", layer=2,
        points=(Point(2, 10), Point(6, 10), Point(6, 16), Point(2, 16)),
    )
    tech = TechnologyRules(
        name="test", grid=1, min_overlap={((1, 0), (2, 0)): 3},
    )
    graph = generate_axis_constraints([], tech, "x", polygons=[outer, inner])
    assert {
        (item["start"]["x"], item["end"]["x"])
        for item in graph.edge_metadata
    } == {(0, 2), (6, 8)}
    positions = {
        f"{polygon.id}:p{index}:x": point.x
        for polygon in prepare_polygons_for_sweep([outer, inner])
        for index, point in enumerate(polygon.points)
    }
    solved = graph.solve(positions)
    assert all(solved[end] - solved[start] >= 3 for start, end, _ in graph.edges)


@pytest.mark.parametrize("axis", ["x", "y"])
@pytest.mark.parametrize("offset", [(20, 0), (0, 20), (20, 20)])
def test_overlap_does_not_pair_disjoint_shapes(
    axis: str, offset: tuple[int, int],
) -> None:
    tech = TechnologyRules(
        name="test", grid=1, min_overlap={((1, 0), (2, 0)): 3},
    )
    rectangles = [
        Rectangle("first", 1, 0, 10, 0, 10),
        Rectangle("second", 2, offset[0], offset[0] + 10, offset[1], offset[1] + 10),
    ]
    graph = generate_axis_constraints(rectangles, tech, axis)
    assert not graph.edges


def test_polygon_extension_rules_create_directional_constraints() -> None:
    outer = Polygon(
        id="outer",
        layer=1,
        points=(Point(0, 0), Point(15, 0), Point(15, 10), Point(0, 10)),
    )
    inner = Polygon(
        id="inner",
        layer=2,
        points=(Point(5, 0), Point(10, 0), Point(10, 10), Point(5, 10)),
    )
    tech = TechnologyRules(
        name="test",
        grid=1,
        min_extension={((1, 0), (2, 0)): 2, ((2, 0), (1, 0)): 3},
        rule_id_extension={((1, 0), (2, 0)): "extension-1"},
    )

    graph = generate_axis_constraints([], tech, "x", polygons=[outer, inner])

    extension_edges = [
        (edge, metadata)
        for edge, metadata in zip(graph.edges, graph.edge_metadata)
        if metadata.get("kind") == "minimum_extension"
    ]
    assert len(extension_edges) == 2
    assert all(edge[2] in (0, 2) for edge, _ in extension_edges)
    assert {metadata["source_layer"] for _, metadata in extension_edges} == {1}
    assert all(metadata["rule_id"] == "extension-1" for _, metadata in extension_edges)


def test_unidirectional_extension_rule_is_kept_when_source_is_shorter() -> None:
    source = Polygon(
        id="source",
        layer=1,
        points=(Point(5, 0), Point(10, 0), Point(10, 10), Point(5, 10)),
    )
    target = Polygon(
        id="target",
        layer=2,
        points=(Point(0, 0), Point(15, 0), Point(15, 10), Point(0, 10)),
    )
    tech = TechnologyRules(
        name="test",
        grid=1,
        min_extension={((1, 0), (2, 0)): 2},
    )

    graph = generate_axis_constraints([], tech, "x", polygons=[source, target])

    assert sum(
        metadata.get("kind") == "minimum_extension"
        for metadata in graph.edge_metadata
    ) == 2


def _segment(metadata: dict, key: str) -> frozenset[tuple[float, float]]:
    return frozenset((point["x"], point["y"]) for point in metadata[key])


def _l_shape(reverse: bool) -> Polygon:
    points = (
        Point(0, 0), Point(10, 0), Point(10, 4), Point(4, 4), Point(4, 10), Point(0, 10),
    )
    return Polygon(id="l", layer=1, points=points[::-1] if reverse else points)


@pytest.mark.parametrize("reverse_winding", [False, True])
def test_polygon_constraints_anchor_on_segment_midpoints(reverse_winding: bool) -> None:
    neighbour = Polygon(
        id="n", layer=1, points=(Point(14, 0), Point(20, 0), Point(20, 10), Point(14, 10)),
    )
    graph = generate_axis_constraints(
        [], _tech(min_spacing=10, min_width=2), "x", polygons=[_l_shape(reverse_winding), neighbour],
    )

    l_edges = {
        frozenset({(10, 0), (10, 4)}),
        frozenset({(4, 4), (4, 10)}),
    }
    neighbour_left = frozenset({(14, 0), (14, 10)})
    spacing = [m for m in graph.edge_metadata if m["kind"] == "minimum_spacing"]
    assert spacing
    for metadata in spacing:
        assert _segment(metadata, "start_segment") in l_edges
        assert _segment(metadata, "end_segment") == neighbour_left
        assert metadata["end"] == {"x": 14, "y": 5}
    assert {_segment(m, "start_segment") for m in spacing} == l_edges

    l_width = [
        m for m in graph.edge_metadata
        if m["kind"] == "minimum_width" and m["source_rect"] == "l"
    ]
    assert l_width
    for metadata in l_width:
        assert _segment(metadata, "start_segment") == frozenset({(0, 0), (0, 10)})
        assert _segment(metadata, "end_segment") == frozenset({(10, 0), (10, 4)})
        assert metadata["start"] == {"x": 0, "y": 5}
        assert metadata["end"] == {"x": 10, "y": 2}


def test_spacing_only_links_facing_segments_across_the_gap() -> None:
    left = Polygon(id="a", layer=1, points=(Point(0, 0), Point(10, 0), Point(10, 10), Point(0, 10)))
    right = Polygon(id="b", layer=1, points=(Point(12, 0), Point(20, 0), Point(20, 10), Point(12, 10)))

    graph = generate_axis_constraints([], _tech(min_spacing=10), "y", polygons=[left, right])
    assert not [m for m in graph.edge_metadata if m["kind"] == "minimum_spacing"]

    graph = generate_axis_constraints([], _tech(min_spacing=10), "x", polygons=[left, right])
    spacing = [m for m in graph.edge_metadata if m["kind"] == "minimum_spacing"]
    assert spacing
    assert {(m["start"]["x"], m["end"]["x"]) for m in spacing} == {(10, 12)}


def test_rectangle_constraints_anchor_on_side_midpoints() -> None:
    left = Rectangle(id="left", layer=1, xmin=0.0, xmax=10.0, ymin=0.0, ymax=10.0)
    right = Rectangle(id="right", layer=1, xmin=12.0, xmax=20.0, ymin=2.0, ymax=6.0)

    graph = generate_axis_constraints([left, right], _tech(min_spacing=10, min_width=2), axis="x")

    by_kind = {
        (m["kind"], m["source_rect"]): m for m in graph.edge_metadata
    }
    spacing = by_kind[("minimum_spacing", "left")]
    assert spacing["start"] == {"x": 10.0, "y": 5.0}
    assert spacing["end"] == {"x": 12.0, "y": 4.0}
    assert _segment(spacing, "end_segment") == frozenset({(12.0, 2.0), (12.0, 6.0)})
    width = by_kind[("minimum_width", "right")]
    assert width["start"] == {"x": 12.0, "y": 4.0}
    assert width["end"] == {"x": 20.0, "y": 4.0}
