import pytest

from gds_migrator.constraints.plane_sweep import Rectangle
from gds_migrator.constraints.solver import (
    LinearConstraint,
    solve_layout,
    solve_layout_with_polygons,
    solve_simplex,
)
from gds_migrator.geometry.transforms import Point
from gds_migrator.hierarchy.model import Polygon
from gds_migrator.technology.rules import TechnologyRules


def test_solve_layout_fixes_spacing_violation() -> None:
    tech = TechnologyRules(name="test", grid=1, min_spacing={(1, 1): 10})
    left = Rectangle(id="left", layer=1, xmin=0.0, xmax=10.0, ymin=0.0, ymax=10.0)
    right = Rectangle(id="right", layer=1, xmin=12.0, xmax=20.0, ymin=0.0, ymax=10.0)

    migrated = solve_layout([left, right], tech)
    by_id = {rect.id: rect for rect in migrated}

    assert by_id["right"].xmin - by_id["left"].xmax >= 10.0
    # Left rectangle, already compliant, is left untouched.
    assert by_id["left"].xmin == 0.0
    assert by_id["left"].xmax == 10.0


def test_solve_layout_snaps_to_grid() -> None:
    tech = TechnologyRules(name="test", grid=5, min_spacing={(1, 1): 7})
    left = Rectangle(id="left", layer=1, xmin=0.0, xmax=10.0, ymin=0.0, ymax=10.0)
    right = Rectangle(id="right", layer=1, xmin=11.0, xmax=20.0, ymin=0.0, ymax=10.0)

    migrated = solve_layout([left, right], tech)
    by_id = {rect.id: rect for rect in migrated}

    for rect in by_id.values():
        assert rect.xmin % 5 == 0
        assert rect.xmax % 5 == 0


def test_solve_layout_preserves_compliant_layout() -> None:
    tech = TechnologyRules(name="test", grid=1, min_spacing={(1, 1): 10})
    left = Rectangle(id="left", layer=1, xmin=0.0, xmax=10.0, ymin=0.0, ymax=10.0)
    right = Rectangle(id="right", layer=1, xmin=20.0, xmax=30.0, ymin=0.0, ymax=10.0)

    migrated = solve_layout([left, right], tech)
    by_id = {rect.id: rect for rect in migrated}

    assert by_id["right"].xmin == 20.0


@pytest.mark.parametrize("axis", ["x", "y"])
@pytest.mark.parametrize("reverse_winding", [False, True])
def test_polygon_solver_preserves_manhattan_boundaries(
    axis: str, reverse_winding: bool,
) -> None:
    def point(x: float, y: float) -> Point:
        return Point(x, y) if axis == "x" else Point(y, x)

    polygons = [
        Polygon("left", 1, tuple(point(x, y) for x, y in (
            (0, 2), (10, 2), (10, 8), (0, 8),
        ))),
        Polygon("right", 1, tuple(point(x, y) for x, y in (
            (12, 0), (22, 0), (22, 10), (12, 10),
        ))),
    ]
    if reverse_winding:
        polygons = [
            Polygon(polygon.id, polygon.layer, tuple(reversed(polygon.points)))
            for polygon in polygons
        ]
    tech = TechnologyRules(name="test", grid=1, min_spacing={(1, 1): 10})

    _, migrated = solve_layout_with_polygons([], tech, polygons)

    assert [len(polygon.points) for polygon in migrated] == [4, 4]
    for polygon in migrated:
        for start, end in zip(polygon.points, (*polygon.points[1:], polygon.points[0])):
            assert start.x == end.x or start.y == end.y
    left, right = migrated
    assert min(getattr(p, axis) for p in right.points) - max(
        getattr(p, axis) for p in left.points
    ) >= 10


def test_polygon_solver_keeps_compliant_boundaries_unchanged() -> None:
    polygon = Polygon("shape", 1, (
        Point(0, 0), Point(10, 0), Point(10, 10), Point(0, 10),
    ))
    tech = TechnologyRules(name="test", grid=1, min_width={1: 5})

    _, migrated = solve_layout_with_polygons([], tech, [polygon])

    assert migrated == [polygon]


@pytest.mark.parametrize("margin", [1, 5])
def test_solve_layout_enforces_same_facing_overlap_margins(margin: int) -> None:
    tech = TechnologyRules(
        name="test", grid=1, min_overlap={((2, 0), (1, 0)): 3},
    )
    outer = Rectangle("outer", 2, 0, 20, 0, 20)
    inner = Rectangle("inner", 1, margin, 20 - margin, margin, 20 - margin)
    migrated = {rect.id: rect for rect in solve_layout([outer, inner], tech)}

    assert migrated["inner"].xmin - migrated["outer"].xmin >= 3
    assert migrated["outer"].xmax - migrated["inner"].xmax >= 3
    assert migrated["inner"].ymin - migrated["outer"].ymin >= 3
    assert migrated["outer"].ymax - migrated["inner"].ymax >= 3
    if margin >= 3:
        assert migrated == {"outer": outer, "inner": inner}


def test_solve_simplex_minimal_example() -> None:
    pytest.importorskip("scipy.optimize")

    constraints = [LinearConstraint(coefficients={"y": 1.0, "x": -1.0}, minimum=10.0)]
    result = solve_simplex(["x", "y"], constraints, {"x": 0.0, "y": 0.0})

    assert result["y"] - result["x"] >= 10.0 - 1e-6
