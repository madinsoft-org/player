import json
from pathlib import Path

from gds_migrator.technology.rules import TechnologyRules, read_klayout_layer_map
from gds_migrator.technology.skywater import load_sky130_minimal

ROOT = Path(__file__).resolve().parents[1]


def test_snap_to_grid_rounds_to_nearest_multiple() -> None:
    tech = TechnologyRules(name="test", grid=5)
    assert tech.snap_to_grid(12) == 10
    assert tech.snap_to_grid(13) == 15


def test_min_spacing_is_symmetric_in_layer_order() -> None:
    tech = TechnologyRules(name="test", grid=1, min_spacing={(1, 2): 30})
    assert tech.min_spacing_for(1, 2) == 30
    assert tech.min_spacing_for(2, 1) == 30


def test_from_json_round_trip(tmp_path: Path) -> None:
    data = {
        "name": "demo",
        "grid": 5,
        "min_width": {"67": 150},
        "min_spacing": [{"layers": [67, 67], "value": 210}],
    }
    path = tmp_path / "tech.json"
    path.write_text(json.dumps(data), encoding="utf-8")

    tech = TechnologyRules.from_json(path)

    assert tech.name == "demo"
    assert tech.grid == 5
    assert tech.min_width_for(67) == 150
    assert tech.min_spacing_for(67, 67) == 210


def test_from_gf180_simplified_uses_layer_and_datatype_mapping() -> None:
    tech = TechnologyRules.from_json(ROOT / "drc" / "gf180_simplified.json")

    assert tech.name == "gf180"
    assert tech.grid == 5
    assert tech.layers["dnwell"] == (12, 0)
    assert tech.layers["metal1_slot"] == (34, 3)
    assert tech.min_width_for(12, 0) > 0
    assert tech.min_spacing_for(12, 12, 0, 0) > 0


def test_read_klayout_layer_map_uses_separate_layer_names(tmp_path: Path) -> None:
    layer_path = tmp_path / "layers.lyp"
    layer_path.write_text(
        "<layer-properties>"
        "<properties><name>nwell</name><source>25/0@1</source></properties>"
        "<properties><name>metal1</name><source>44/0@1</source></properties>"
        "</layer-properties>",
        encoding="utf-8",
    )

    assert read_klayout_layer_map(layer_path) == {
        "nwell": (25, 0),
        "metal1": (44, 0),
    }


def test_from_gf180_simplified_keeps_strictest_minimum_per_gds_identity(
    tmp_path: Path,
) -> None:
    rules_path = tmp_path / "rules.json"
    rules_path.write_text(
        json.dumps([
            {
                "type": "width", "layer1": "metal1", "layer2": "metal1",
                "value": 0.23, "context": {"bound": "minimum"},
            },
            {
                "type": "width", "layer1": "metal1", "layer2": "metal1",
                "value": 0.30, "context": {"bound": "minimum"},
            },
            {
                "type": "width", "layer1": "metal1_dummy",
                "layer2": "metal1_dummy", "value": 0.40,
                "context": {"bound": "minimum"},
            },
            {
                "type": "width", "layer1": "metal1", "layer2": "metal1",
                "value": 4.0, "context": {"bound": "maximum"},
            },
            {
                "type": "spacing", "layer1": "metal1", "layer2": "metal2",
                "value": 0.14, "context": {"bound": "minimum"},
            },
            {
                "type": "spacing", "layer1": "metal2", "layer2": "metal1",
                "value": 0.20, "context": {"bound": "minimum"},
            },
        ]),
        encoding="utf-8",
    )
    lyp_path = tmp_path / "layers.lyp"
    lyp_path.write_text(
        "<layer-properties>"
        "<properties><source>metal1 34/0@1</source></properties>"
        "<properties><source>metal1_dummy 34/4@1</source></properties>"
        "<properties><source>metal2 36/0@1</source></properties>"
        "</layer-properties>",
        encoding="utf-8",
    )

    tech = TechnologyRules.from_json(rules_path, lyp_path)

    assert tech.min_width_for(34, 0) == 300
    assert tech.min_width_for(34, 4) == 400
    assert tech.min_spacing_for(34, 36, 0, 0) == 200
    assert tech.min_spacing_for(36, 34, 0, 0) == 200


def test_sky130_minimal_has_metal1_rules() -> None:
    tech = load_sky130_minimal()
    assert tech.name == "sky130"
    assert tech.min_width_for(67) > 0
