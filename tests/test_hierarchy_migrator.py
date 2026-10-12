from gds_migrator.constraints.plane_sweep import Rectangle
from gds_migrator.geometry.transforms import Point, Transform
from gds_migrator.hierarchy.migrator import INSTANCE_BOUNDARY_LAYER, migrate_hierarchy
from gds_migrator.hierarchy.model import Cell, Instance
from gds_migrator.technology.rules import TechnologyRules


def _tech(min_spacing: int = 10) -> TechnologyRules:
    return TechnologyRules(
        name="test",
        grid=1,
        min_spacing={
            (1, 1): min_spacing,
            (INSTANCE_BOUNDARY_LAYER, INSTANCE_BOUNDARY_LAYER): min_spacing,
        },
    )


def test_leaf_cell_migration_fixes_internal_spacing() -> None:
    leaf = Cell(
        name="LEAF",
        rectangles=[
            Rectangle(id="a", layer=1, xmin=0.0, xmax=10.0, ymin=0.0, ymax=10.0),
            Rectangle(id="b", layer=1, xmin=12.0, xmax=20.0, ymin=0.0, ymax=10.0),
        ],
    )
    migrated = migrate_hierarchy({"LEAF": leaf}, _tech(min_spacing=10))
    result = migrated["LEAF"]
    by_id = {rect.id: rect for rect in result.rectangles}

    assert by_id["b"].xmin - by_id["a"].xmax >= 10.0


def test_instance_abutment_is_preserved_across_hierarchy() -> None:
    # LEAF has an internal shape that must keep its position relative to
    # the cell origin once the cell is instantiated (rigid abutment).
    leaf = Cell(
        name="LEAF",
        rectangles=[Rectangle(id="shape", layer=1, xmin=0.0, xmax=10.0, ymin=0.0, ymax=10.0)],
    )
    # Two abutting instances of LEAF, placed too close together in the parent.
    top = Cell(
        name="TOP",
        instances=[
            Instance(id="inst0", cell_name="LEAF", transform=Transform(origin=Point(0.0, 0.0))),
            Instance(id="inst1", cell_name="LEAF", transform=Transform(origin=Point(10.5, 0.0))),
        ],
    )

    migrated = migrate_hierarchy({"LEAF": leaf, "TOP": top}, _tech(min_spacing=10))
    top_result = migrated["TOP"]
    by_id = {inst.id: inst for inst in top_result.instances}

    leaf_width = migrated["LEAF"].xmax - migrated["LEAF"].xmin
    inst0_right_edge = by_id["inst0"].transform.origin.x + leaf_width
    inst1_left_edge = by_id["inst1"].transform.origin.x

    assert inst1_left_edge - inst0_right_edge >= 10.0 - 1e-9
    # The first instance, already at the origin, is not moved.
    assert by_id["inst0"].transform.origin.x == 0.0


def test_rotation_and_mirror_are_preserved_through_migration() -> None:
    leaf = Cell(name="LEAF", rectangles=[Rectangle(id="s", layer=1, xmin=0.0, xmax=5.0, ymin=0.0, ymax=5.0)])
    top = Cell(
        name="TOP",
        instances=[
            Instance(
                id="inst0",
                cell_name="LEAF",
                transform=Transform(origin=Point(0.0, 0.0), rotation=90.0, mirror_x=True),
            )
        ],
    )

    migrated = migrate_hierarchy({"LEAF": leaf, "TOP": top}, _tech(min_spacing=0))
    instance = migrated["TOP"].instances[0]

    assert instance.transform.rotation == 90.0
    assert instance.transform.mirror_x is True
