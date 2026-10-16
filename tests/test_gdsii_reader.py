import gdstk

from gds_migrator.constraints.plane_sweep import generate_axis_constraints
from gds_migrator.hierarchy.migrator import migrate_hierarchy
from gds_migrator.io.gdsii_reader import read_library
from gds_migrator.io.gdsii_writer import write_library
from gds_migrator.technology.rules import TechnologyRules


def test_reader_retains_polygon_outlines_and_constraint_bounds(tmp_path) -> None:
    source_path = tmp_path / "polygons.gds"
    library = gdstk.Library(unit=1e-9, precision=1e-12)
    cell = gdstk.Cell("TOP")
    cell.add(
        gdstk.Polygon([(0, 0), (10, 0), (8, 5), (0, 5)], layer=1, datatype=3),
        gdstk.Polygon([(12, 0), (22, 0), (20, 5), (12, 5)], layer=1, datatype=3),
    )
    library.add(cell)
    library.write_gds(source_path)

    cells = read_library(source_path)
    top = cells["TOP"]
    assert len(top.polygons) == 2
    assert [(point.x, point.y) for point in top.polygons[0].points] == [
        (0, 0),
        (10, 0),
        (8, 5),
        (0, 5),
    ]
    assert top.polygons[0].datatype == 3
    assert (top.rectangles[0].xmin, top.rectangles[0].xmax) == (0, 10)

    technology = TechnologyRules(
        name="test",
        grid=1,
        min_spacing={(1, 1): 5},
    )
    graph = generate_axis_constraints(top.rectangles, technology, axis="x")
    assert any(metadata["kind"] == "minimum_spacing" for metadata in graph.edge_metadata)


def test_migration_writer_emits_polygon_outlines_not_constraint_boxes(tmp_path) -> None:
    source_path = tmp_path / "source.gds"
    output_path = tmp_path / "migrated.gds"
    library = gdstk.Library(unit=1e-9, precision=1e-12)
    cell = gdstk.Cell("TOP")
    cell.add(gdstk.Polygon([(0, 0), (10, 0), (8, 5), (0, 5)], layer=2, datatype=4))
    library.add(cell)
    library.write_gds(source_path)

    cells = read_library(source_path)
    technology = TechnologyRules(name="test", grid=1)
    migrated = migrate_hierarchy(cells, technology)
    write_library(migrated, output_path)

    output = gdstk.read_gds(output_path)
    polygons = output["TOP"].polygons
    assert len(polygons) == 1
    assert polygons[0].layer == 2
    assert polygons[0].datatype == 4
    assert [(float(point[0]), float(point[1])) for point in polygons[0].points] == [
        (0, 0),
        (10, 0),
        (8, 5),
        (0, 5),
    ]
