"""Bottom-up hierarchical migration preserving cell-to-cell abutment.

Each cell is migrated only after all of its child cells have been migrated.
A child instance is treated as a rigid macro block: its already-solved
internal layout (and therefore any abutment/pin alignment inside it) is
never altered, only translated as a whole. The instance's outer bounding
box is fed into the same plane-sweep + constraint-graph solve used for the
cell's own shapes, so boundary/spacing rules between adjacent instances are
resolved consistently with the rest of the design, and the resulting
translation is propagated to the instance's placement transform.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from gds_migrator.constraints.plane_sweep import Rectangle
from gds_migrator.constraints.solver import solve_layout
from gds_migrator.geometry.transforms import Point
from gds_migrator.hierarchy.model import Cell, Instance, Polygon
from gds_migrator.hierarchy.traversal import topological_order
from gds_migrator.technology.rules import TechnologyRules

#: Sentinel GDSII layer for synthetic instance-boundary rectangles; must not
#: collide with any real design layer. Its width is always forced via
#: `rigid_widths`/`rigid_heights`, but a `min_spacing` rule keyed on
#: `(INSTANCE_BOUNDARY_LAYER, INSTANCE_BOUNDARY_LAYER)` must be present in
#: the technology to enforce a minimum cell-to-cell (instance-to-instance)
#: spacing between sibling instances.
INSTANCE_BOUNDARY_LAYER = -1


@dataclass(frozen=True)
class MigratedCell:
    """The result of migrating one cell: its own shapes plus placed instances."""

    name: str
    rectangles: tuple[Rectangle, ...] = field(default_factory=tuple)
    instances: tuple[Instance, ...] = field(default_factory=tuple)
    xmin: float = 0.0
    xmax: float = 0.0
    ymin: float = 0.0
    ymax: float = 0.0
    polygons: tuple[Polygon, ...] = field(default_factory=tuple)


def migrate_hierarchy(
    cells: dict[str, Cell],
    tech: TechnologyRules,
    excluded_constraint_ids: set[str] | None = None,
) -> dict[str, MigratedCell]:
    """Migrate every cell in ``cells`` to satisfy ``tech``, bottom-up."""
    order = topological_order(cells)
    migrated: dict[str, MigratedCell] = {}
    for name in order:
        migrated[name] = _migrate_cell(cells[name], migrated, tech, excluded_constraint_ids)
    return migrated


def _instance_bbox(instance: Instance, child: MigratedCell) -> tuple[float, float, float, float]:
    corners = [
        instance.transform.apply(Point(child.xmin, child.ymin)),
        instance.transform.apply(Point(child.xmax, child.ymin)),
        instance.transform.apply(Point(child.xmax, child.ymax)),
        instance.transform.apply(Point(child.xmin, child.ymax)),
    ]
    xs = [corner.x for corner in corners]
    ys = [corner.y for corner in corners]
    return min(xs), max(xs), min(ys), max(ys)


def _migrate_cell(
    cell: Cell,
    migrated: dict[str, MigratedCell],
    tech: TechnologyRules,
    excluded_constraint_ids: set[str] | None = None,
) -> MigratedCell:
    own_ids = {rect.id for rect in cell.rectangles}
    macro_rects: list[Rectangle] = []
    for instance in cell.instances:
        if instance.id in own_ids:
            raise ValueError(f"instance id {instance.id!r} collides with a shape id in cell {cell.name!r}")
        child = migrated[instance.cell_name]
        xmin, xmax, ymin, ymax = _instance_bbox(instance, child)
        macro_rects.append(
            Rectangle(id=instance.id, layer=INSTANCE_BOUNDARY_LAYER, xmin=xmin, xmax=xmax, ymin=ymin, ymax=ymax)
        )

    all_rects = [*cell.rectangles, *macro_rects]
    rigid_widths = {rect.id: rect.xmax - rect.xmin for rect in macro_rects}
    rigid_heights = {rect.id: rect.ymax - rect.ymin for rect in macro_rects}
    migrated_rects = solve_layout(
        all_rects,
        tech,
        rigid_widths=rigid_widths,
        rigid_heights=rigid_heights,
        excluded_constraint_ids=excluded_constraint_ids,
    )
    migrated_by_id = {rect.id: rect for rect in migrated_rects}

    new_own_rectangles = tuple(migrated_by_id[rect.id] for rect in cell.rectangles)
    original_rectangles = {rect.id: rect for rect in cell.rectangles}
    migrated_polygons = tuple(
        _migrate_polygon(polygon, original_rectangles[polygon.id], migrated_by_id[polygon.id])
        for polygon in cell.polygons
    )

    new_instances = []
    for instance in cell.instances:
        original = next(r for r in macro_rects if r.id == instance.id)
        moved = migrated_by_id[instance.id]
        delta = Point(moved.xmin - original.xmin, moved.ymin - original.ymin)
        new_transform = replace(instance.transform, origin=instance.transform.origin + delta)
        new_instances.append(replace(instance, transform=new_transform))

    if not all_rects:
        xmin = xmax = ymin = ymax = 0.0
    else:
        xmin = min(rect.xmin for rect in migrated_rects)
        xmax = max(rect.xmax for rect in migrated_rects)
        ymin = min(rect.ymin for rect in migrated_rects)
        ymax = max(rect.ymax for rect in migrated_rects)

    return MigratedCell(
        name=cell.name,
        rectangles=new_own_rectangles,
        polygons=migrated_polygons,
        instances=tuple(new_instances),
        xmin=xmin,
        xmax=xmax,
        ymin=ymin,
        ymax=ymax,
    )


def _migrate_polygon(polygon: Polygon, original: Rectangle, migrated: Rectangle) -> Polygon:
    """Apply the solved bounding-box scale and translation to a polygon outline."""
    original_width = original.xmax - original.xmin
    original_height = original.ymax - original.ymin
    migrated_width = migrated.xmax - migrated.xmin
    migrated_height = migrated.ymax - migrated.ymin

    def map_coordinate(value: float, old_min: float, old_size: float, new_min: float, new_size: float) -> float:
        if old_size == 0:
            return value + new_min - old_min
        return new_min + (value - old_min) * new_size / old_size

    points = tuple(
        Point(
            map_coordinate(point.x, original.xmin, original_width, migrated.xmin, migrated_width),
            map_coordinate(point.y, original.ymin, original_height, migrated.ymin, migrated_height),
        )
        for point in polygon.points
    )
    return replace(polygon, points=points)
