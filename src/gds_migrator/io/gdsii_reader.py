"""Read a GDSII library into the migrator's internal `Cell` model."""

from __future__ import annotations

import math
from pathlib import Path

import gdstk

from gds_migrator.geometry.transforms import Point, Transform
from gds_migrator.hierarchy.model import Cell, Instance, Polygon


def read_library(path: Path) -> dict[str, Cell]:
    """Read a GDSII file and return a mapping of cell name to `Cell`."""
    library = gdstk.read_gds(str(path))
    cells: dict[str, Cell] = {}
    for gdstk_cell in library.cells:
        polygons = [
            Polygon(
                id=f"{gdstk_cell.name}:poly{index}",
                layer=polygon.layer,
                points=tuple(
                    Point(float(point[0]), float(point[1]))
                    for point in polygon.points
                ),
                datatype=polygon.datatype,
            )
            for index, polygon in enumerate(gdstk_cell.polygons)
        ]
        instances = [
            Instance(
                id=f"{gdstk_cell.name}:ref{index}",
                cell_name=reference.cell.name,
                transform=Transform(
                    origin=Point(float(reference.origin[0]), float(reference.origin[1])),
                    rotation=math.degrees(float(reference.rotation)),
                    mirror_x=bool(reference.x_reflection),
                    magnification=float(reference.magnification),
                ),
            )
            for index, reference in enumerate(gdstk_cell.references)
        ]
        cells[gdstk_cell.name] = Cell(
            name=gdstk_cell.name,
            polygons=polygons,
            instances=instances,
        )
    return cells
