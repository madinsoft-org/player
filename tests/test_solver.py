import pytest

from gds_migrator.constraints.plane_sweep import Rectangle
from gds_migrator.constraints.solver import LinearConstraint, solve_layout, solve_simplex
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


def test_solve_simplex_minimal_example() -> None:
    pytest.importorskip("scipy.optimize")

    constraints = [LinearConstraint(coefficients={"y": 1.0, "x": -1.0}, minimum=10.0)]
    result = solve_simplex(["x", "y"], constraints, {"x": 0.0, "y": 0.0})

    assert result["y"] - result["x"] >= 10.0 - 1e-6
