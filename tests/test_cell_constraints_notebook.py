"""Regression checks for the executable cell constraint example."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

import pytest


@pytest.fixture
def notebook_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> dict[str, Any]:
    display_module = pytest.importorskip("IPython.display")
    monkeypatch.setattr(display_module, "display", lambda *args: None)
    root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(root / "notebooks")
    notebook = json.loads(
        (root / "notebooks" / "cell_constraints.ipynb").read_text(encoding="utf-8")
    )
    state: dict[str, Any] = {}
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            exec(compile("".join(cell["source"]), cell["id"], "exec"), state)
            if cell["id"] == "export-config":
                state["export_directory"] = tmp_path / "exports"
    return state


def test_geometry_and_constraints(notebook_state: dict[str, Any]) -> None:
    state = notebook_state
    cell = state["cell"]
    assert [rect.id for rect in cell.rectangles] == [
        "cellbox", "contact", "metal1", "poly",
    ]
    assert [rect.layer for rect in cell.rectangles] == [-1, 25, 49, 46]
    box = state["cellbox"]
    for rect in state["shapes"]:
        assert box.xmin <= rect.xmin < rect.xmax <= box.xmax
        assert box.ymin <= rect.ymin < rect.ymax <= box.ymax
    for axis, graph in state["graphs"].items():
        assert len(graph.nodes) == 8
        assert len(graph.edges) == 13
        assert state["solved_positions"][axis] == state["initial_positions"][axis]
        containment = [
            edge for edge, metadata in zip(graph.edges, graph.edge_metadata)
            if metadata["kind"] == "cellbox_containment"
        ]
        assert set(containment) == {
            edge for rect in state["shapes"]
            for edge in (
                ("cellbox:lo", f"{rect.id}:lo", 0),
                (f"{rect.id}:hi", "cellbox:hi", 0),
            )
        }
        kinds = [metadata["kind"] for metadata in graph.edge_metadata]
        assert kinds.count("minimum_width") == 3
        assert kinds.count("minimum_overlap") == 4


def test_image_and_tree_include_every_link(notebook_state: dict[str, Any]) -> None:
    state = notebook_state
    svg = ET.fromstring(state["layout_svg"])
    namespace = {"svg": "http://www.w3.org/2000/svg"}
    links = svg.findall("svg:a[@id]", namespace)
    labels = svg.findall("svg:a/svg:text", namespace)
    assert len(links) == state["constraint_count"] == 26
    assert len(labels) == 26
    assert all(
        element.find("svg:text", namespace) is not None
        for element in list(svg)[-26:]
    )
    assert len({link.attrib["id"] for link in links}) == 26
    for link in links:
        entry = link.attrib["href"].removeprefix("#")
        assert f'id="{entry}"' in state["constraint_html"]
        assert f'href="#{link.attrib["id"]}"' in state["constraint_html"]
        assert link.find("svg:title", namespace) is not None
        assert link.find("svg:path", namespace) is not None
    for panel, (axis, graph) in enumerate(state["graphs"].items()):
        for index, metadata in enumerate(graph.edge_metadata):
            if "start" not in metadata:
                continue
            start = metadata["start"]
            expected = state["screen_point"]((start["x"], start["y"]), panel)
            circle = links[panel * 13 + index].find("svg:circle", namespace)
            assert circle is not None
            assert (float(circle.attrib["cx"]), float(circle.attrib["cy"])) == expected


@pytest.mark.parametrize(
    "bounds",
    [(0, 0, 0, 1), (1, 0, 0, 1), (0, 1, 1, 0), (float("nan"), 1, 0, 1)],
)
def test_invalid_bounds_raise(
    notebook_state: dict[str, Any],
    bounds: tuple[float, float, float, float],
) -> None:
    with pytest.raises(ValueError, match="positive width and height"):
        notebook_state["make_rectangle"]("contact", bounds)


def test_unknown_layer_raises(notebook_state: dict[str, Any]) -> None:
    with pytest.raises(KeyError, match="unknown"):
        notebook_state["make_rectangle"]("unknown", (0, 1, 0, 1))


def test_sweep_and_migration_are_independent(notebook_state: dict[str, Any]) -> None:
    state = notebook_state
    before = state["before_cells"][state["cell"].name]
    swept = state["swept_cell"]
    migrated = state["migrated_cell"]
    assert tuple(state["shapes"]) == before.rectangles == migrated.rectangles
    assert swept.rectangles != before.rectangles
    assert swept.xmax - swept.xmin < before.xmax - before.xmin
    for result in (swept, migrated):
        assert len(result.rectangles) == 3
        for rect in result.rectangles:
            assert rect.layer >= 0
            assert rect.xmin < rect.xmax and rect.ymin < rect.ymax
            for coordinate in (rect.xmin, rect.xmax, rect.ymin, rect.ymax):
                assert coordinate % state["tech"].grid == 0
        for axis in ("x", "y"):
            graph = state["generate_axis_constraints"](
                list(result.rectangles), state["tech"], axis,
            )
            positions = {
                f"{rect.id}:{side}": getattr(rect, f"{axis}{bound}")
                for rect in result.rectangles
                for side, bound in (("lo", "min"), ("hi", "max"))
            }
            assert graph.solve(positions) == positions


def test_gds_exports_preserve_layers_and_coordinates(
    notebook_state: dict[str, Any],
) -> None:
    import gdstk

    state = notebook_state
    assert set(state["export_paths"]) == {
        "before", "after_sweep", "after_migration",
    }
    for stage, path in state["export_paths"].items():
        assert path.is_file()
        library = gdstk.read_gds(str(path))
        assert library.unit == 1e-9
        assert library.precision == 1e-12
        assert len(library.cells) == 1
        result = state["results"][stage][state["cell"].name]
        assert library.cells[0].name == result.name
        polygons = library.cells[0].polygons
        assert len(polygons) == 3
        assert {(polygon.layer, polygon.datatype) for polygon in polygons} == {
            (25, 0), (49, 0), (46, 0),
        }
        by_layer = {polygon.layer: polygon for polygon in polygons}
        for rect in result.rectangles:
            bounds = by_layer[rect.layer].bounding_box()
            assert bounds == (
                (rect.xmin, rect.ymin), (rect.xmax, rect.ymax),
            )
