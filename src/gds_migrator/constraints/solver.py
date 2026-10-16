"""Per-axis constraint solving orchestration (X then Y), plus an optional
simplex fallback for simultaneous/diagonal 2D constraints.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from gds_migrator.constraints.graph import ConstraintGraph, InfeasibleConstraintsError
from gds_migrator.constraints.plane_sweep import (
    Rectangle,
    _get_polygon_sweep_levels,
    generate_axis_constraints,
    prepare_polygons_for_sweep,
)
from gds_migrator.geometry.transforms import Point
from gds_migrator.technology.rules import TechnologyRules

if TYPE_CHECKING:
    from gds_migrator.hierarchy.model import Polygon


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
    migrated_rectangles, _ = solve_layout_with_polygons(
        rectangles,
        tech,
        rigid_widths=rigid_widths,
        rigid_heights=rigid_heights,
        excluded_constraint_ids=excluded_constraint_ids,
    )
    return migrated_rectangles


def solve_layout_with_polygons(
    rectangles: list[Rectangle],
    tech: TechnologyRules,
    polygons: list[Polygon] | None = None,
    rigid_widths: dict[str, float] | None = None,
    rigid_heights: dict[str, float] | None = None,
    excluded_constraint_ids: set[str] | None = None,
) -> tuple[list[Rectangle], list[Polygon]]:
    """Solve a layout while preserving and moving polygon vertices."""
    from gds_migrator.hierarchy.model import Polygon

    source_polygons = polygons or []
    x_sweep_levels = _get_polygon_sweep_levels(source_polygons, rectangles, "x")
    y_sweep_levels = _get_polygon_sweep_levels(source_polygons, rectangles, "y")
    polygons = prepare_polygons_for_sweep(source_polygons, rectangles)
    x_positions = _solve_axis(
        rectangles,
        polygons,
        tech,
        axis="x",
        polygon_sweep_levels=x_sweep_levels,
        rigid_sizes=rigid_widths,
        excluded_constraint_ids=excluded_constraint_ids,
    )
    y_positions = _solve_axis(
        rectangles,
        polygons,
        tech,
        axis="y",
        polygon_sweep_levels=y_sweep_levels,
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
    migrated_polygons = []
    for polygon in polygons:
        points = _remove_collinear_points(tuple(
            Point(
                x_positions[f"{polygon.id}:p{index}:x"],
                y_positions[f"{polygon.id}:p{index}:y"],
            )
            for index in range(len(polygon.points))
        ))
        migrated_polygons.append(
            Polygon(
                id=polygon.id,
                layer=polygon.layer,
                datatype=polygon.datatype,
                points=tuple(
                    Point(tech.snap_to_grid(point.x), tech.snap_to_grid(point.y))
                    for point in points
                ),
            )
        )
    return migrated, migrated_polygons


def _solve_axis(
    rectangles: list[Rectangle],
    polygons: list[Polygon],
    tech: TechnologyRules,
    axis: str,
    polygon_sweep_levels: list[float],
    rigid_sizes: dict[str, float] | None,
    excluded_constraint_ids: set[str] | None,
) -> dict[str, float]:
    graph = generate_axis_constraints(
        rectangles,
        tech,
        axis,
        rigid_sizes=rigid_sizes,
        polygons=polygons,
        polygons_prepared=True,
        polygon_sweep_levels=polygon_sweep_levels,
    )
    initial_positions: dict[str, float] = {}
    for rect in rectangles:
        lo, hi = (rect.xmin, rect.xmax) if axis == "x" else (rect.ymin, rect.ymax)
        initial_positions[f"{rect.id}:lo"] = lo
        initial_positions[f"{rect.id}:hi"] = hi
    for polygon in polygons:
        for index, point in enumerate(polygon.points):
            initial_positions[f"{polygon.id}:p{index}:x"] = point.x
            initial_positions[f"{polygon.id}:p{index}:y"] = point.y
    return graph.solve(initial_positions, excluded_constraint_ids=excluded_constraint_ids)


def _remove_collinear_points(points: tuple[Point, ...]) -> tuple[Point, ...]:
    simplified = list(points)
    changed = True
    while changed and len(simplified) > 3:
        changed = False
        for index, current in enumerate(simplified):
            previous = simplified[index - 1]
            following = simplified[(index + 1) % len(simplified)]
            cross_product = (
                (current.x - previous.x) * (following.y - current.y)
                - (current.y - previous.y) * (following.x - current.x)
            )
            if abs(cross_product) <= 1e-9:
                simplified.pop(index)
                changed = True
                break
    return tuple(simplified)


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
