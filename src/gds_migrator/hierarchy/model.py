"""Core hierarchy model: a cell is a set of own shapes plus placed instances."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, Sequence

from gds_migrator.constraints.plane_sweep import Rectangle
from gds_migrator.geometry.transforms import Point, Transform


@dataclass(frozen=True)
class Instance:
    """A placement of a child cell inside a parent cell.

    Attributes:
        id: Identifier unique within the parent cell (used as a constraint
            graph node prefix).
        cell_name: Name of the referenced cell.
        transform: Placement transform mapping the child's local frame to
            the parent's frame.
    """

    id: str
    cell_name: str
    transform: Transform


@dataclass(frozen=True)
class Polygon:
    """A polygon outline retained from GDSII alongside its constraint bounds."""

    id: str
    layer: int
    points: tuple[Point, ...]
    datatype: int = 0


class HierarchyNode(Protocol):
    """Anything with placed instances, satisfied by both `Cell` and `MigratedCell`."""

    instances: Sequence[Instance]


@dataclass
class Cell:
    """A GDSII cell: its own flattened shapes plus references to child cells."""

    name: str
    rectangles: list[Rectangle] = field(default_factory=list)
    instances: list[Instance] = field(default_factory=list)
    polygons: list[Polygon] = field(default_factory=list)
