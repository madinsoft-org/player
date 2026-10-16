"""Web interface for inspecting, constraining, and migrating GDSII layouts."""

from __future__ import annotations

import json
import re
import tempfile
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from gds_migrator.constraints.graph import ConstraintGraph
from gds_migrator.constraints.plane_sweep import Rectangle, generate_axis_constraints
from gds_migrator.geometry.transforms import Point, Transform
from gds_migrator.hierarchy.migrator import MigratedCell, migrate_hierarchy
from gds_migrator.hierarchy.model import Cell
from gds_migrator.hierarchy.traversal import topological_order
from gds_migrator.io.gdsii_reader import read_library
from gds_migrator.io.gdsii_writer import write_library
from gds_migrator.technology.rules import TechnologyRules

MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_RENDER_SHAPES = 50_000
_LAYER_SOURCE = re.compile(r"^\s*(?:(.*?)\s+)?(-?\d+)\s*/\s*(\d+)(?:@\d+)?\s*$")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")


@dataclass
class ProjectSession:
    id: str
    temporary_directory: TemporaryDirectory[str]
    source_path: Path
    source_name: str
    cells: dict[str, Cell]
    technology: TechnologyRules
    layer_properties: dict[int | tuple[int, int], dict[str, str]] = field(default_factory=dict)
    excluded_constraint_ids: set[str] = field(default_factory=set)
    migrated: dict[str, MigratedCell] | None = None
    output_path: Path | None = None


class ConstraintToggle(BaseModel):
    cell: str
    excluded: bool


sessions: dict[str, ProjectSession] = {}
app = FastAPI(title="GDS Layout Migrator")
_static_directory = Path(__file__).with_name("static")
app.mount("/static", StaticFiles(directory=_static_directory), name="static")


def _get_session(session_id: str) -> ProjectSession:
    project = sessions.get(session_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project session not found.")
    return project


async def _save_upload(upload: UploadFile, destination: Path) -> None:
    content = await upload.read(MAX_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=422, detail=f"{upload.filename or 'Uploaded file'} is empty.")
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Each uploaded file must be 100 MB or smaller.")
    destination.write_bytes(content)


def _matching_json_delimiter(text: str, opening_index: int) -> int:
    opening = text[opening_index]
    closing = "}" if opening == "{" else "]"
    depth = 0
    in_string = False
    escaped = False
    for index in range(opening_index, len(text)):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == opening:
            depth += 1
        elif character == closing:
            depth -= 1
            if depth == 0:
                return index
    return len(text)


def _json_key_line(text: str, key: str, start: int = 0, end: int | None = None) -> int:
    scope = text[start:end]
    match = re.search(rf'"{re.escape(key)}"\s*:', scope)
    if match is None:
        return text.count("\n", 0, start) + 1
    return text.count("\n", 0, start + match.start()) + 1


def _json_section_bounds(text: str, key: str) -> tuple[int, int]:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*([\[\{{])', text)
    if match is None:
        return 0, len(text)
    opening_index = text.find(match.group(1), match.start(), match.end())
    return opening_index, _matching_json_delimiter(text, opening_index) + 1


def _json_array_object_bounds(text: str, key: str) -> list[tuple[int, int]]:
    start, end = _json_section_bounds(text, key)
    if start == end or text[start] != "[":
        return []
    objects: list[tuple[int, int]] = []
    in_string = False
    escaped = False
    depth = 0
    object_start: int | None = None
    for index in range(start + 1, end - 1):
        character = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character == "{":
            if depth == 0:
                object_start = index
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0 and object_start is not None:
                objects.append((object_start, index + 1))
                object_start = None
    return objects


def _load_technology_rules(
    path: Path,
    layer_properties_path: Path | None = None,
) -> TechnologyRules:
    text = path.read_text(encoding="utf-8")
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Technology JSON error at line {error.lineno}, column {error.colno}: {error.msg}"
        ) from error

    def invalid(message: str, line: int) -> None:
        raise ValueError(f"Technology JSON error at line {line}: {message}")

    if isinstance(document, list):
        if layer_properties_path is not None and layer_properties_path.suffix.lower() != ".lyp":
            invalid("GF180 simplified rules require a KLayout .lyp file.", 1)
        if layer_properties_path is None and not path.with_name("gf180mcu.lyp").is_file():
            invalid("GF180 simplified rules require uploading a KLayout .lyp file.", 1)
        return TechnologyRules.from_json(path, layer_properties_path)

    if not isinstance(document, dict):
        invalid("The root value must be a JSON object or GF180 rule array.", 1)

    for required_key in ("name", "grid"):
        if required_key not in document:
            invalid(
                f"Required field {required_key!r} is missing.",
                _json_key_line(text, required_key),
            )

    if not isinstance(document["name"], str) or not document["name"].strip():
        invalid("Field 'name' must be a non-empty string.", _json_key_line(text, "name"))
    grid = document["grid"]
    if isinstance(grid, bool) or not isinstance(grid, int) or grid <= 0:
        invalid("Field 'grid' must be a positive integer.", _json_key_line(text, "grid"))

    widths = document.get("min_width", {})
    if not isinstance(widths, dict):
        invalid("Field 'min_width' must be an object.", _json_key_line(text, "min_width"))
    width_start, width_end = _json_section_bounds(text, "min_width")
    for layer, value in widths.items():
        try:
            int(layer)
        except (TypeError, ValueError):
            invalid(
                f"Invalid layer number {layer!r} in 'min_width'.",
                _json_key_line(text, str(layer), width_start, width_end),
            )
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            invalid(
                f"Minimum width for layer {layer!r} must be a non-negative integer.",
                _json_key_line(text, str(layer), width_start, width_end),
            )

    spacings = document.get("min_spacing", [])
    if not isinstance(spacings, list):
        invalid("Field 'min_spacing' must be an array.", _json_key_line(text, "min_spacing"))
    spacing_bounds = _json_array_object_bounds(text, "min_spacing")
    for index, entry in enumerate(spacings):
        entry_start, entry_end = spacing_bounds[index] if index < len(spacing_bounds) else (0, len(text))
        if not isinstance(entry, dict):
            invalid(
                f"Entry {index} in 'min_spacing' must be an object.",
                text.count("\n", 0, entry_start) + 1 if entry_start else _json_key_line(text, "min_spacing"),
            )
        layers = entry.get("layers")
        if (
            not isinstance(layers, list)
            or len(layers) != 2
            or any(isinstance(layer, bool) or not isinstance(layer, int) for layer in layers)
        ):
            invalid(
                f"Entry {index} must contain exactly two integer layer numbers.",
                _json_key_line(text, "layers", entry_start, entry_end),
            )
        value = entry.get("value")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            invalid(
                f"Spacing rule {index} must have a non-negative integer 'value'.",
                _json_key_line(text, "value", entry_start, entry_end),
            )

    return TechnologyRules.from_json(path, layer_properties_path)


def _parse_layer_properties(path: Path) -> dict[int | tuple[int, int], dict[str, str]]:
    if path.suffix.lower() == ".lyp":
        root = ElementTree.parse(path).getroot()
        properties: dict[int | tuple[int, int], dict[str, str]] = {}
        for entry in root.findall(".//properties"):
            source = entry.findtext("source", default="")
            match = _LAYER_SOURCE.fullmatch(source)
            if match is None:
                continue
            layer = int(match.group(2))
            datatype = int(match.group(3))
            color = entry.findtext("fill-color", default="#778899")
            if not _COLOR.fullmatch(color):
                raise ValueError(f"Invalid display color for layer {layer}: {color!r}.")
            name = entry.findtext("name", default="").strip() or match.group(1) or f"Layer {layer}"
            properties[(layer, datatype)] = {
                "name": name,
                "color": color,
            }
        return properties

    if path.suffix.lower() != ".json":
        raise ValueError("Layer properties must be a KLayout .lyp or JSON file.")

    document = json.loads(path.read_text(encoding="utf-8"))
    entries = document.get("layers", document) if isinstance(document, dict) else document
    if isinstance(entries, dict):
        entries = [
            {"layer": layer, **value} if isinstance(value, dict) else {"layer": layer, "name": value}
            for layer, value in entries.items()
        ]
    if not isinstance(entries, list):
        raise ValueError("Layer JSON must contain a list or a 'layers' object.")

    properties = {}
    for entry in entries:
        if not isinstance(entry, dict) or "layer" not in entry:
            raise ValueError("Each layer entry must be an object with a 'layer' number.")
        layer = int(entry["layer"])
        color = str(entry.get("color", "#778899"))
        if not _COLOR.fullmatch(color):
            raise ValueError(f"Invalid display color for layer {layer}: {color!r}.")
        properties[layer] = {
            "name": str(entry.get("name", f"Layer {layer}")),
            "color": color,
        }
    return properties


def _project_layers(project: ProjectSession) -> list[dict[str, Any]]:
    def gds_layer(identity: int | tuple[int, int]) -> int:
        return identity[0] if isinstance(identity, tuple) else identity

    layers = {gds_layer(identity) for identity in project.technology.min_width}
    for layer_a, layer_b in project.technology.min_spacing:
        layer_a_number = gds_layer(layer_a)
        layer_b_number = gds_layer(layer_b)
        if layer_a_number >= 0:
            layers.add(layer_a_number)
        if layer_b_number >= 0:
            layers.add(layer_b_number)
    gds_layer_datatypes = _gds_layer_datatypes(project)
    layers.update(gds_layer_datatypes)
    result = []
    for layer in sorted(layers):
        datatypes = sorted(gds_layer_datatypes.get(layer, set()))
        generic_properties = project.layer_properties.get(layer, {})
        datatype_properties = {
            datatype: project.layer_properties.get((layer, datatype), {})
            for datatype in datatypes
        }
        properties = datatype_properties.get(datatypes[0], {}) if datatypes else {}
        properties = properties or generic_properties
        if not properties and not datatypes:
            properties = next(
                (
                    value
                    for identity, value in sorted(project.layer_properties.items(), key=lambda item: repr(item[0]))
                    if isinstance(identity, tuple) and identity[0] == layer
                ),
                {},
            )
        result.append({
            "layer": layer,
            "name": properties.get("name", f"Layer {layer}"),
            "color": properties.get("color", "#778899"),
            "datatype_colors": {
                str(datatype): datatype_properties[datatype].get(
                    "color",
                    generic_properties.get("color", "#778899"),
                )
                for datatype in datatypes
            },
        })
    return result


def _gds_layer_datatypes(project: ProjectSession) -> dict[int, set[int]]:
    layer_datatypes: dict[int, set[int]] = {}
    for cell in project.cells.values():
        for rectangle in cell.rectangles:
            if rectangle.layer >= 0:
                layer_datatypes.setdefault(rectangle.layer, set()).add(rectangle.datatype)
    return layer_datatypes


def _gds_layers(project: ProjectSession) -> set[int]:
    """Get the set of layer numbers that are actually present in the loaded GDS file."""
    return set(_gds_layer_datatypes(project))


def _project_cell_bounds(
    cells: dict[str, Cell] | dict[str, MigratedCell],
    cell_name: str,
    stack: set[str] | None = None,
) -> tuple[float, float, float, float]:
    current_stack = set() if stack is None else stack
    if cell_name in current_stack:
        raise ValueError(f"Cycle detected while flattening cell {cell_name!r}.")
    current_stack.add(cell_name)
    cell = cells[cell_name]
    bounds: list[tuple[float, float, float, float]] = [
        (rectangle.xmin, rectangle.xmax, rectangle.ymin, rectangle.ymax)
        for rectangle in cell.rectangles
    ]
    for instance in cell.instances:
        child_bounds = _project_cell_bounds(cells, instance.cell_name, current_stack)
        corners = [
            instance.transform.apply(Point(x, y))
            for x, y in (
                (child_bounds[0], child_bounds[2]),
                (child_bounds[0], child_bounds[3]),
                (child_bounds[1], child_bounds[2]),
                (child_bounds[1], child_bounds[3]),
            )
        ]
        bounds.append(
            (
                min(point.x for point in corners),
                max(point.x for point in corners),
                min(point.y for point in corners),
                max(point.y for point in corners),
            )
        )
    current_stack.remove(cell_name)
    if not bounds:
        return 0.0, 0.0, 0.0, 0.0
    return (
        min(bound[0] for bound in bounds),
        max(bound[1] for bound in bounds),
        min(bound[2] for bound in bounds),
        max(bound[3] for bound in bounds),
    )


def _flatten_rectangles(
    cells: dict[str, Cell] | dict[str, MigratedCell],
    cell_name: str,
    transform: Transform = Transform(),
    path: tuple[str, ...] = (),
    stack: set[str] | None = None,
) -> list[dict[str, Any]]:
    current_stack = set() if stack is None else stack
    if cell_name in current_stack:
        raise ValueError(f"Cycle detected while flattening cell {cell_name!r}.")
    current_stack.add(cell_name)
    cell = cells[cell_name]
    current_path = (*path, cell_name)
    rectangles = []
    for rectangle in cell.rectangles:
        corners = [
            transform.apply(Point(x, y))
            for x, y in (
                (rectangle.xmin, rectangle.ymin),
                (rectangle.xmin, rectangle.ymax),
                (rectangle.xmax, rectangle.ymin),
                (rectangle.xmax, rectangle.ymax),
            )
        ]
        rectangles.append(
            {
                "id": "/".join(current_path) + "/" + rectangle.id,
                "layer": rectangle.layer,
                "datatype": rectangle.datatype,
                "xmin": min(point.x for point in corners),
                "xmax": max(point.x for point in corners),
                "ymin": min(point.y for point in corners),
                "ymax": max(point.y for point in corners),
            }
        )
        if len(rectangles) > MAX_RENDER_SHAPES:
            raise ValueError(f"Layout exceeds the {MAX_RENDER_SHAPES:,}-shape viewer limit.")
    for instance in cell.instances:
        rectangles.extend(
            _flatten_rectangles(
                cells,
                instance.cell_name,
                transform.compose(instance.transform),
                (*current_path, instance.id),
                current_stack,
            )
        )
        if len(rectangles) > MAX_RENDER_SHAPES:
            raise ValueError(f"Layout exceeds the {MAX_RENDER_SHAPES:,}-shape viewer limit.")
    current_stack.remove(cell_name)
    return rectangles


def _cell_constraint_rectangles(
    project: ProjectSession,
    cell_name: str,
) -> tuple[list[Rectangle], dict[str, float], dict[str, float]]:
    cell = project.cells[cell_name]
    rectangles = list(cell.rectangles)
    rigid_widths: dict[str, float] = {}
    rigid_heights: dict[str, float] = {}
    for instance in cell.instances:
        child_bounds = _project_cell_bounds(project.cells, instance.cell_name)
        corners = [
            instance.transform.apply(Point(x, y))
            for x, y in (
                (child_bounds[0], child_bounds[2]),
                (child_bounds[0], child_bounds[3]),
                (child_bounds[1], child_bounds[2]),
                (child_bounds[1], child_bounds[3]),
            )
        ]
        xmin = min(point.x for point in corners)
        xmax = max(point.x for point in corners)
        ymin = min(point.y for point in corners)
        ymax = max(point.y for point in corners)
        rectangles.append(
            Rectangle(
                id=instance.id,
                layer=-1,
                xmin=xmin,
                xmax=xmax,
                ymin=ymin,
                ymax=ymax,
            )
        )
        rigid_widths[instance.id] = xmax - xmin
        rigid_heights[instance.id] = ymax - ymin
    return rectangles, rigid_widths, rigid_heights


def _get_layer_name(project: ProjectSession, layer: int) -> str:
    """Get the name of a layer from layer properties."""
    if layer < 0:
        return "Instance"

    properties = project.layer_properties.get(layer, {})
    if properties.get("name"):
        return properties["name"]

    for identity, props in project.layer_properties.items():
        if isinstance(identity, tuple) and identity[0] == layer:
            if props.get("name"):
                return props["name"]

    return f"Layer {layer}"


def _constraint_edges(project: ProjectSession, cell_name: str) -> list[dict[str, Any]]:
    rectangles, rigid_widths, rigid_heights = _cell_constraint_rectangles(project, cell_name)
    rectangles_by_id = {rectangle.id: rectangle for rectangle in rectangles}
    edges: list[dict[str, Any]] = []
    for axis, rigid_sizes in (("x", rigid_widths), ("y", rigid_heights)):
        graph: ConstraintGraph = generate_axis_constraints(
            rectangles,
            project.technology,
            axis,
            rigid_sizes=rigid_sizes,
        )
        for index, (upstream, downstream, value) in enumerate(graph.edges):
            metadata = graph.edge_metadata[index]
            constraint_type = {
                "minimum_width": "width",
                "fixed_footprint": "width",
                "minimum_spacing": "spacing",
                "minimum_overlap": "overlap",
                "minimum_extension": "extension",
            }[metadata["kind"]]
            source = rectangles_by_id[metadata["source_rect"]]
            target = rectangles_by_id[metadata["target_rect"]]
            if metadata["kind"] == "minimum_width":
                start = (
                    ((source.xmin + source.xmax) / 2, source.ymin)
                    if axis == "y"
                    else (source.xmin, (source.ymin + source.ymax) / 2)
                )
                end = (
                    ((source.xmin + source.xmax) / 2, source.ymax)
                    if axis == "y"
                    else (source.xmax, (source.ymin + source.ymax) / 2)
                )
            elif axis == "x":
                start = (source.xmax, (source.ymin + source.ymax) / 2)
                end = (target.xmin, (target.ymin + target.ymax) / 2)
            else:
                start = ((source.xmin + source.xmax) / 2, source.ymax)
                end = ((target.xmin + target.xmax) / 2, target.ymin)

            source_layer = metadata["source_layer"]
            target_layer = metadata["target_layer"]

            edges.append(
                {
                    "id": graph.edge_ids[index],
                    "axis": axis,
                    "kind": metadata["kind"],
                    "type": constraint_type,
                    "upstream": upstream,
                    "downstream": downstream,
                    "value": value,
                    "source_rect": metadata["source_rect"],
                    "target_rect": metadata["target_rect"],
                    "source_layer": source_layer,
                    "target_layer": target_layer,
                    "source_layer_name": _get_layer_name(project, source_layer),
                    "target_layer_name": _get_layer_name(project, target_layer),
                    "removable": metadata["kind"] != "fixed_footprint",
                    "start": {"x": start[0], "y": start[1]},
                    "end": {"x": end[0], "y": end[1]},
                    "excluded": graph.edge_ids[index] in project.excluded_constraint_ids,
                }
            )
    return edges


@app.get("/")
def index() -> FileResponse:
    return FileResponse(_static_directory / "index.html")


@app.post("/api/sessions")
async def create_session(
    layout: UploadFile = File(...),
    technology: UploadFile = File(...),
    layers: UploadFile | None = File(default=None),
) -> dict[str, Any]:
    layout_suffix = Path(layout.filename or "").suffix.lower()
    if layout_suffix not in {".gds", ".gdsii"}:
        raise HTTPException(status_code=415, detail="Layout must be a GDSII (.gds or .gdsii) file.")
    if Path(technology.filename or "").suffix.lower() != ".json":
        raise HTTPException(status_code=415, detail="Technology rules must be a JSON file.")

    temporary_directory = tempfile.TemporaryDirectory(prefix="gds-migrator-")
    work_directory = Path(temporary_directory.name)
    source_path = work_directory / "source.gds"
    technology_path = work_directory / "technology.json"
    layer_path = None
    try:
        await _save_upload(layout, source_path)
        await _save_upload(technology, technology_path)
        if layers is not None and layers.filename:
            layer_suffix = Path(layers.filename).suffix.lower()
            if layer_suffix not in {".lyp", ".json"}:
                raise HTTPException(status_code=415, detail="Layer properties must be a KLayout .lyp or JSON file.")
            layer_path = work_directory / f"layers{layer_suffix}"
            await _save_upload(layers, layer_path)

        technology_layer_map_path = (
            layer_path
            if layer_path is not None and layer_path.suffix.lower() == ".lyp"
            else None
        )
        technology_rules = _load_technology_rules(
            technology_path,
            technology_layer_map_path,
        )
        cells = read_library(source_path)
        if not cells:
            raise ValueError("The uploaded GDSII file does not contain any cells.")
        order = topological_order(cells)
        layer_properties = _parse_layer_properties(layer_path) if layer_path else {}
        project_id = str(uuid4())
        project = ProjectSession(
            id=project_id,
            temporary_directory=temporary_directory,
            source_path=source_path,
            source_name=layout.filename or "layout.gds",
            cells=cells,
            technology=technology_rules,
            layer_properties=layer_properties,
        )
        sessions[project_id] = project
        referenced_cells = {instance.cell_name for cell in cells.values() for instance in cell.instances}
        root_cells = [name for name in cells if name not in referenced_cells]
        gds_layer_numbers = _gds_layers(project)
        return {
            "id": project_id,
            "source_name": project.source_name,
            "cells": order,
            "root_cells": root_cells,
            "technology": {"name": technology_rules.name, "grid": technology_rules.grid},
            "layers": _project_layers(project),
            "gds_layers": sorted(gds_layer_numbers),
        }
    except HTTPException:
        temporary_directory.cleanup()
        raise
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, ElementTree.ParseError) as error:
        temporary_directory.cleanup()
        raise HTTPException(status_code=422, detail=f"Could not load project: {error}") from error


@app.get("/api/sessions/{session_id}")
def get_project(session_id: str) -> dict[str, Any]:
    project = _get_session(session_id)
    referenced_cells = {instance.cell_name for cell in project.cells.values() for instance in cell.instances}
    root_cells = [name for name in project.cells if name not in referenced_cells]
    gds_layer_numbers = _gds_layers(project)
    return {
        "id": project.id,
        "source_name": project.source_name,
        "cells": list(project.cells),
        "root_cells": root_cells,
        "technology": {"name": project.technology.name, "grid": project.technology.grid},
        "layers": _project_layers(project),
        "gds_layers": sorted(gds_layer_numbers),
        "has_migration": project.migrated is not None,
    }


@app.get("/api/sessions/{session_id}/merge")
def merge_layer_polygons(session_id: str, cell: str, layer: int) -> dict[str, Any]:
    """Merge all polygons on a specific layer in a cell hierarchy.
    
    This performs a boolean union (OR) of all polygons on the specified layer,
    returning the resulting merged polygons.
    """
    import gdstk
    
    project = _get_session(session_id)
    if cell not in project.cells:
        raise HTTPException(status_code=404, detail=f"Unknown cell: {cell!r}.")
    
    # Re-read the GDS file to get actual polygons
    library = gdstk.read_gds(str(project.source_path))
    
    # Find the cell in the library
    gdstk_cell = next((c for c in library.cells if c.name == cell), None)
    if gdstk_cell is None:
        raise HTTPException(status_code=404, detail=f"Cell {cell!r} not found in GDS file.")
    
    # Get all polygons from this cell and its hierarchy on the specified layer
    # Use get_polygons with depth=None to get all hierarchy
    layer_polygons = [p for p in gdstk_cell.get_polygons(depth=None) if p.layer == layer]
    
    if not layer_polygons:
        return {
            "cell": cell,
            "layer": layer,
            "polygons": [],
            "message": f"No polygons found on layer {layer}",
            "count": 0,
        }
    
    # Perform boolean union on all polygons
    try:
        if len(layer_polygons) == 1:
            merged_polygons = layer_polygons
        else:
            # Start with first polygon
            result = layer_polygons[0]
            # Union with each subsequent polygon
            for i in range(1, len(layer_polygons)):
                result = gdstk.boolean(result, layer_polygons[i], "or")[0]
            merged_polygons = [result]
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=f"Could not merge polygons on layer {layer}: {str(e)}",
        )
    
    # Convert polygons to a serializable format
    polygons_data = []
    for poly in merged_polygons:
        polygons_data.append({
            "points": [[float(p[0]), float(p[1])] for p in poly.points],
            "layer": layer,
        })
    
    return {
        "cell": cell,
        "layer": layer,
        "polygons": polygons_data,
        "count": len(merged_polygons),
    }


@app.get("/api/sessions/{session_id}/layout")
def get_layout(session_id: str, cell: str, version: str = "before") -> dict[str, Any]:
    project = _get_session(session_id)
    if cell not in project.cells:
        raise HTTPException(status_code=404, detail=f"Unknown cell: {cell!r}.")
    if version not in {"before", "after"}:
        raise HTTPException(status_code=422, detail="Version must be 'before' or 'after'.")
    if version == "after":
        if project.migrated is None:
            raise HTTPException(status_code=409, detail="Run the migration before requesting the migrated layout.")
        cells: dict[str, Cell] | dict[str, MigratedCell] = project.migrated
    else:
        cells = project.cells
    try:
        rectangles = _flatten_rectangles(cells, cell)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {"cell": cell, "version": version, "rectangles": rectangles}


@app.get("/api/sessions/{session_id}/constraints")
def get_constraints(session_id: str, cell: str) -> dict[str, Any]:
    project = _get_session(session_id)
    if cell not in project.cells:
        raise HTTPException(status_code=404, detail=f"Unknown cell: {cell!r}.")
    return {"cell": cell, "edges": _constraint_edges(project, cell)}


@app.post("/api/sessions/{session_id}/constraints/{constraint_id}")
def toggle_constraint(session_id: str, constraint_id: str, toggle: ConstraintToggle) -> dict[str, Any]:
    project = _get_session(session_id)
    if toggle.cell not in project.cells:
        raise HTTPException(status_code=404, detail=f"Unknown cell: {toggle.cell!r}.")
    edges = _constraint_edges(project, toggle.cell)
    edge_by_id = {edge["id"]: edge for edge in edges}
    if constraint_id not in edge_by_id:
        raise HTTPException(status_code=404, detail="Constraint does not belong to the selected cell.")
    edge = edge_by_id[constraint_id]
    if not edge["removable"]:
        raise HTTPException(status_code=409, detail="Fixed instance footprints cannot be excluded.")
    if toggle.excluded:
        project.excluded_constraint_ids.add(constraint_id)
    else:
        project.excluded_constraint_ids.discard(constraint_id)
    project.migrated = None
    project.output_path = None
    return {"id": constraint_id, "excluded": toggle.excluded}


@app.post("/api/sessions/{session_id}/migrate")
def migrate_project(session_id: str) -> dict[str, Any]:
    project = _get_session(session_id)
    try:
        migrated = migrate_hierarchy(
            project.cells,
            project.technology,
            excluded_constraint_ids=project.excluded_constraint_ids,
        )
        output_path = Path(project.temporary_directory.name) / "migrated.gds"
        write_library(migrated, output_path)
    except (OSError, ValueError, RuntimeError) as error:
        raise HTTPException(status_code=422, detail=f"Migration failed: {error}") from error
    project.migrated = migrated
    project.output_path = output_path
    return {
        "cells_migrated": len(migrated),
        "technology": project.technology.name,
        "excluded_constraints": len(project.excluded_constraint_ids),
        "download_url": f"/api/sessions/{project.id}/download",
    }


@app.get("/api/sessions/{session_id}/download")
def download_migrated(session_id: str) -> FileResponse:
    project = _get_session(session_id)
    if project.output_path is None or not project.output_path.is_file():
        raise HTTPException(status_code=409, detail="Run the migration before downloading the result.")
    return FileResponse(project.output_path, media_type="application/octet-stream", filename="migrated.gds")


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str) -> dict[str, bool]:
    project = sessions.pop(session_id, None)
    if project is None:
        raise HTTPException(status_code=404, detail="Project session not found.")
    project.temporary_directory.cleanup()
    return {"deleted": True}


def run() -> None:
    """Run the development server."""
    import uvicorn

    uvicorn.run("gds_migrator.web.app:app", host="127.0.0.1", port=8000)
