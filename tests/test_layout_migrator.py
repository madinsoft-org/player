import json

import gdstk

from src_new.layout_migrator import migrate


def test_migrate_uses_target_rules_and_preserves_hierarchy(tmp_path) -> None:
    source_path = tmp_path / "source.gds"
    output_path = tmp_path / "migrated.gds"
    technology_path = tmp_path / "technology.json"

    library = gdstk.Library(unit=1e-9, precision=1e-12)
    leaf = gdstk.Cell("LEAF")
    leaf.add(
        gdstk.rectangle((0, 0), (5, 10), layer=1),
        gdstk.rectangle((7, 0), (12, 10), layer=1, datatype=4),
    )
    top = gdstk.Cell("TOP")
    top.add(
        gdstk.Reference(leaf, origin=(0, 0)),
        gdstk.Reference(leaf, origin=(20.5, 0)),
    )
    library.add(leaf, top)
    library.write_gds(source_path)
    technology_path.write_text(
        json.dumps(
            {
                "name": "target",
                "grid": 1,
                "min_spacing": [
                    {"layers": [1, 1], "value": 10},
                    {"layers": [-1, -1], "value": 10},
                ],
            }
        ),
        encoding="utf-8",
    )

    assert migrate(source_path, output_path, technology_path) == 2

    migrated = gdstk.read_gds(output_path)
    cells = {cell.name: cell for cell in migrated.cells}
    migrated_leaf = cells["LEAF"]
    migrated_top = cells["TOP"]

    assert len(migrated_top.references) == 2
    assert all(reference.cell.name == "LEAF" for reference in migrated_top.references)

    polygons = sorted(migrated_leaf.polygons, key=lambda polygon: polygon.points[:, 0].min())
    assert {polygon.datatype for polygon in polygons} == {0, 4}
    left_edge = polygons[0].points[:, 0].max()
    right_edge = polygons[1].points[:, 0].min()
    assert right_edge - left_edge >= 10

    cell_width = max(
        polygon.points[:, 0].max()
        for polygon in migrated_leaf.polygons
    ) - min(polygon.points[:, 0].min() for polygon in migrated_leaf.polygons)
    instance_gap = (
        migrated_top.references[1].origin[0]
        - migrated_top.references[0].origin[0]
        - cell_width
    )
    assert instance_gap >= 10
