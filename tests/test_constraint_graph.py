import pytest

from gds_migrator.constraints.graph import ConstraintGraph, InfeasibleConstraintsError


def test_single_constraint_pushes_downstream_node() -> None:
    graph = ConstraintGraph()
    graph.add_constraint("a", "b", 10.0)

    result = graph.solve({"a": 0.0, "b": 5.0})

    assert result["a"] == 0.0
    assert result["b"] == 10.0


def test_satisfied_constraint_keeps_initial_position() -> None:
    graph = ConstraintGraph()
    graph.add_constraint("a", "b", 10.0)

    result = graph.solve({"a": 0.0, "b": 20.0})

    assert result["b"] == 20.0


def test_chained_constraints_propagate() -> None:
    graph = ConstraintGraph()
    graph.add_constraint("a", "b", 5.0)
    graph.add_constraint("b", "c", 5.0)

    result = graph.solve({"a": 0.0, "b": 0.0, "c": 0.0})

    assert result == {"a": 0.0, "b": 5.0, "c": 10.0}


def test_positive_cycle_raises_infeasible_error() -> None:
    graph = ConstraintGraph()
    graph.add_constraint("a", "b", 5.0)
    graph.add_constraint("b", "a", 5.0)

    with pytest.raises(InfeasibleConstraintsError):
        graph.solve({"a": 0.0, "b": 0.0})


def test_missing_initial_position_raises_value_error() -> None:
    graph = ConstraintGraph()
    graph.add_constraint("a", "b", 5.0)

    with pytest.raises(ValueError):
        graph.solve({"a": 0.0})
