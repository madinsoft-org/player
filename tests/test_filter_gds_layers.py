import sys
from pathlib import Path
import gdstk
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.filter_gds_layers import filter_gds_layers, load_layer_map, resolve_requested_layers


def test_filter_numeric_layers(tmp_path: Path) -> None:
    input_gds = tmp_path / "input.gds"
    output_gds = tmp_path / "output.gds"

    lib = gdstk.Library()
    cell = gdstk.Cell("TOP")
    cell.add(
        gdstk.rectangle((0, 0), (10, 10), layer=49, datatype=0),
        gdstk.rectangle((10, 0), (20, 10), layer=46, datatype=0),
        gdstk.rectangle((20, 0), (30, 10), layer=25, datatype=0),
    )
    lib.add(cell)
    lib.write_gds(str(input_gds))

    # Filter only layer 49
    filter_gds_layers(input_gds, output_gds, [49])

    out_lib = gdstk.read_gds(str(output_gds))
    out_cell = out_lib["TOP"]
    assert len(out_cell.polygons) == 1
    assert out_cell.polygons[0].layer == 49


def test_filter_with_dict_layer_map(tmp_path: Path) -> None:
    input_gds = tmp_path / "input.gds"
    output_gds = tmp_path / "output.gds"

    lib = gdstk.Library()
    cell = gdstk.Cell("TOP")
    cell.add(
        gdstk.rectangle((0, 0), (10, 10), layer=49, datatype=0),
        gdstk.rectangle((10, 0), (20, 10), layer=46, datatype=0),
        gdstk.rectangle((20, 0), (30, 10), layer=25, datatype=0),
    )
    lib.add(cell)
    lib.write_gds(str(input_gds))

    layer_map = {
        "metal1": (49, 0),
        "poly": (46, 0),
        "contact": (25, 0),
    }

    filter_gds_layers(input_gds, output_gds, ["metal1", "poly"], layer_map=layer_map)

    out_lib = gdstk.read_gds(str(output_gds))
    layers_present = {(p.layer, p.datatype) for p in out_lib["TOP"].polygons}
    assert layers_present == {(49, 0), (46, 0)}


def test_filter_with_lyp_file(tmp_path: Path) -> None:
    input_gds = Path("data/nand.gds")
    output_gds = tmp_path / "nand_filtered.gds"
    lyp_file = Path("data/scmos_180nm_mosis.lyp")

    filter_gds_layers(input_gds, output_gds, ["metal1", "poly"], layer_map=lyp_file)

    out_lib = gdstk.read_gds(str(output_gds))
    layers_present = {(p.layer, p.datatype) for cell in out_lib.cells for p in cell.polygons}
    assert layers_present == {(49, 0), (46, 0)}


def test_filter_preserves_hierarchy(tmp_path: Path) -> None:
    input_gds = tmp_path / "hier.gds"
    output_gds = tmp_path / "hier_filtered.gds"

    lib = gdstk.Library()
    subcell = gdstk.Cell("SUB")
    subcell.add(
        gdstk.rectangle((0, 0), (5, 5), layer=49, datatype=0),
        gdstk.rectangle((5, 0), (10, 5), layer=25, datatype=0),
    )
    top = gdstk.Cell("TOP")
    top.add(gdstk.Reference(subcell, origin=(10, 10)))
    top.add(gdstk.rectangle((0, 0), (2, 2), layer=25, datatype=0))
    lib.add(subcell, top)
    lib.write_gds(str(input_gds))

    filter_gds_layers(input_gds, output_gds, ["49"])

    out_lib = gdstk.read_gds(str(output_gds))
    cell_names = {c.name for c in out_lib.cells}
    assert "TOP" in cell_names
    assert "SUB" in cell_names
    top_out = out_lib["TOP"]
    sub_out = out_lib["SUB"]

    # In TOP, layer 25 rectangle was filtered out, but Reference remains
    assert len(top_out.polygons) == 0
    assert len(top_out.references) == 1
    assert top_out.references[0].cell.name == "SUB"

    # In SUB, only layer 49 rectangle remains
    assert len(sub_out.polygons) == 1
    assert sub_out.polygons[0].layer == 49


def test_unknown_layer_raises_value_error(tmp_path: Path) -> None:
    input_gds = tmp_path / "dummy.gds"
    output_gds = tmp_path / "dummy_out.gds"

    lib = gdstk.Library()
    lib.add(gdstk.Cell("TOP"))
    lib.write_gds(str(input_gds))

    layer_map = {"metal1": (49, 0)}
    with pytest.raises(ValueError, match="n'ont pas pu être résolus : unknown_layer"):
        filter_gds_layers(input_gds, output_gds, ["unknown_layer"], layer_map=layer_map)
