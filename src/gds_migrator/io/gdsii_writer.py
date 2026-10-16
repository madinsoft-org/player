"""Write migrated cells back out to a GDSII file, built on `gdstk`."""

from __future__ import annotations

import math
from pathlib import Path

import gdstk

from gds_migrator.hierarchy.migrator import MigratedCell
from gds_migrator.hierarchy.traversal import topological_order


def write_library(migrated: dict[str, MigratedCell], output_path: Path, unit: float = 1e-9, precision: float = 1e-12) -> None:
    """Write the migrated cell hierarchy to ``output_path`` as a GDSII file."""
    order = topological_order(migrated)
    library = gdstk.Library(unit=unit, precision=precision)
    gdstk_cells: dict[str, gdstk.Cell] = {}

    for name in order:
        migrated_cell = migrated[name]
        gdstk_cell = gdstk.Cell(name)
        polygon_ids = {polygon.id for polygon in migrated_cell.polygons}
        for rect in migrated_cell.rectangles:
            if rect.id in polygon_ids:
                continue
            gdstk_cell.add(
                gdstk.rectangle(
                    (rect.xmin, rect.ymin),
                    (rect.xmax, rect.ymax),
                    layer=rect.layer,
                    datatype=rect.datatype,
                )
            )
        for polygon in migrated_cell.polygons:
            gdstk_cell.add(
                gdstk.Polygon(
                    [(point.x, point.y) for point in polygon.points],
                    layer=polygon.layer,
                    datatype=polygon.datatype,
                )
            )
        for instance in migrated_cell.instances:
            child_cell = gdstk_cells[instance.cell_name]
            gdstk_cell.add(
                gdstk.Reference(
                    child_cell,
                    origin=(instance.transform.origin.x, instance.transform.origin.y),
                    rotation=math.radians(instance.transform.rotation),
                    x_reflection=instance.transform.mirror_x,
                    magnification=instance.transform.magnification,
                )
            )
        gdstk_cells[name] = gdstk_cell
        library.add(gdstk_cell)

    library.write_gds(str(output_path))
