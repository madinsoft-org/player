from gds_migrator.constraints.plane_sweep import Rectangle, generate_axis_constraints
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
