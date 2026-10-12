"""Per-axis constraint solving orchestration (X then Y), plus an optional
simplex fallback for simultaneous/diagonal 2D constraints.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from gds_migrator.constraints.graph import ConstraintGraph, InfeasibleConstraintsError
from gds_migrator.constraints.plane_sweep import Rectangle, generate_axis_constraints
from gds_migrator.technology.rules import TechnologyRules


def solve_layout(
    rectangles: list[Rectangle],
    tech: TechnologyRules,
    rigid_widths: dict[str, float] | None = None,
    rigid_heights: dict[str, float] | None = None,
    excluded_constraint_ids: set[str] | None = None,
) -> list[Rectangle]:
    """Migrate a flat set of rectangles to satisfy ``tech``, solving X then Y.

    Args:
        rectangles: Shapes to migrate.
        tech: Target technology design rules.
        rigid_widths: Optional per-rectangle forced width (X axis), used to
            keep an already-migrated child instance's footprint rigid.
        rigid_heights: Optional per-rectangle forced height (Y axis), same
            purpose as ``rigid_widths``.
        excluded_constraint_ids: Optional IDs of generated constraints to
            omit for this solve.
    """
    x_positions = _solve_axis(
        rectangles,
        tech,
        axis="x",
        rigid_sizes=rigid_widths,
        excluded_constraint_ids=excluded_constraint_ids,
    )
    y_positions = _solve_axis(
        rectangles,
        tech,
        axis="y",
        rigid_sizes=rigid_heights,
        excluded_constraint_ids=excluded_constraint_ids,
    )

    migrated = []
    for rect in rectangles:
        migrated.append(
            replace(
                rect,
                xmin=tech.snap_to_grid(x_positions[f"{rect.id}:lo"]),
                xmax=tech.snap_to_grid(x_positions[f"{rect.id}:hi"]),
                ymin=tech.snap_to_grid(y_positions[f"{rect.id}:lo"]),
                ymax=tech.snap_to_grid(y_positions[f"{rect.id}:hi"]),
            )
        )
    return migrated


def _solve_axis(
    rectangles: list[Rectangle],
    tech: TechnologyRules,
    axis: str,
    rigid_sizes: dict[str, float] | None,
    excluded_constraint_ids: set[str] | None,
) -> dict[str, float]:
    graph = generate_axis_constraints(rectangles, tech, axis, rigid_sizes=rigid_sizes)
    initial_positions: dict[str, float] = {}
    for rect in rectangles:
        lo, hi = (rect.xmin, rect.xmax) if axis == "x" else (rect.ymin, rect.ymax)
        initial_positions[f"{rect.id}:lo"] = lo
        initial_positions[f"{rect.id}:hi"] = hi
    return graph.solve(initial_positions, excluded_constraint_ids=excluded_constraint_ids)


@dataclass(frozen=True)
class LinearConstraint:
    """A linear inequality ``sum(coefficients[v] * value[v]) >= minimum``."""

    coefficients: dict[str, float]
    minimum: float


def solve_simplex(
    variables: list[str],
    constraints: list[LinearConstraint],
    initial_positions: dict[str, float],
) -> dict[str, float]:
    """Solve simultaneous / diagonal 2D constraints via a HiGHS-backed LP.

    This is the fallback path mentioned in the design brief for cases the
    per-axis longest-path solve cannot express (e.g. a constraint coupling
    ``x`` and ``y`` of two different shapes simultaneously). It minimizes
    the total displacement (``sum(variables)``) subject to every
    constraint being satisfied and every variable staying at or above its
    initial position.

    Requires ``scipy`` (install the ``simplex`` extra).
    """
    try:
        import numpy as np
        from scipy.optimize import linprog
    except ImportError as exc:  # pragma: no cover - exercised only when scipy is absent
        raise ImportError(
            "solve_simplex requires scipy; install it via the 'simplex' extra "
            "(pip install gds-migrator[simplex])"
        ) from exc

    index = {name: position for position, name in enumerate(variables)}
    objective = np.ones(len(variables))

    inequality_matrix = []
    inequality_bounds = []
    for constraint in constraints:
        row = np.zeros(len(variables))
        for name, coefficient in constraint.coefficients.items():
            row[index[name]] = -coefficient
        inequality_matrix.append(row)
        inequality_bounds.append(-constraint.minimum)

    bounds = [(initial_positions.get(name, 0.0), None) for name in variables]
    result = linprog(
        objective,
        A_ub=inequality_matrix or None,
        b_ub=inequality_bounds or None,
        bounds=bounds,
        method="highs",
    )
    if not result.success:
        raise InfeasibleConstraintsError(f"simplex solve failed: {result.message}")
    return {name: float(result.x[index[name]]) for name in variables}
