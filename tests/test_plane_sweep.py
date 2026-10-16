from gds_migrator.constraints.plane_sweep import Rectangle, generate_axis_constraints
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
