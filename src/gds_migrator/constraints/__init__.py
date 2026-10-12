"""Automatic generation and resolution of geometric spacing constraints."""

from gds_migrator.constraints.graph import ConstraintGraph, InfeasibleConstraintsError
from gds_migrator.constraints.plane_sweep import Rectangle, generate_axis_constraints
from gds_migrator.constraints.solver import solve_layout

__all__ = [
    "ConstraintGraph",
    "InfeasibleConstraintsError",
    "Rectangle",
    "generate_axis_constraints",
    "solve_layout",
]
