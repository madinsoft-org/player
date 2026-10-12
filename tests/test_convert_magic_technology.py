import json
from pathlib import Path

import pytest

from gds_migrator.technology.rules import TechnologyRules
from src_new.convert_magic_technology import (
    DRAWING_LAYERS,
    output_metadata,
    to_technology_rules,
)
from src_new.extract_magic_rules import parse_magic, write_catalog


def make_source(tmp_path: Path, technology: str) -> Path:
    lines = [
        "cifoutput", "style gdsii", "scalefactor 50 nanometers", "gridlimit 5",
    ]
    for item in DRAWING_LAYERS[technology]:
        lines.extend([
            f"layer {item.cif_layer} reviewed_types",
            f"calma {item.gds_layer} {item.datatype}",
        ])
    lines.extend([
        "end", "drc", "style drc variants (fast),(full)",
        "scalefactor 100", "variants (full)",
    ])
    for item in DRAWING_LAYERS[technology]:
        lines.extend([
            f'width {item.width_expression} 231 "Width ({item.width_reference})"',
            f'spacing {" ".join(item.spacing_expressions)} 231 '
            f'touching_ok "Space ({item.spacing_reference})"',
        ])
    lines.extend([
        'surround a b 100 absence_illegal "Enclosure (other.1)"', "end",
    ])
    path = tmp_path / f"{technology}.tech"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


@pytest.mark.parametrize("technology", ["sky130", "gf180"])
def test_conversion_loads_with_real_technology_loader(
    tmp_path: Path, technology: str,
) -> None:
    source = make_source(tmp_path, technology)
    raw = parse_magic(source.read_text(), technology, source.name)
    converted = to_technology_rules(raw, source)
    output = tmp_path / "rules.json"
    write_catalog(converted, output)
    loaded = TechnologyRules.from_json(output)
    assert loaded.name == technology
    assert loaded.grid == 5
    metal1 = 68 if technology == "sky130" else 34
    # 231 DRC units * 50 nm per Magic unit / 100 DRC units per Magic unit.
    assert loaded.min_width_for(metal1) == 116
    assert loaded.min_spacing_for(metal1, metal1) == 116
    assert converted["single_layer"] == raw["single_layer"]
    assert converted["between_layers"] == raw["between_layers"]
    assert any(not record["applied"] for record in converted["conversion"]["records"])
    if technology == "sky130":
        assert loaded.min_width_for(64) == 0
        assert loaded.min_spacing_for(64, 64) == 0
    assert json.loads(output.read_text())["min_width"][str(metal1)] == 116


def test_changed_gds_mapping_fails(tmp_path: Path) -> None:
    source = make_source(tmp_path, "sky130")
    source.write_text(source.read_text().replace("calma 68 20", "calma 67 20"))
    raw = parse_magic(source.read_text(), "sky130", source.name)
    with pytest.raises(ValueError, match="Unexpected GDS mapping"):
        to_technology_rules(raw, source)


def test_changed_rule_expression_fails(tmp_path: Path) -> None:
    source = make_source(tmp_path, "gf180")
    raw = parse_magic(source.read_text(), "gf180", source.name)
    raw["single_layer"]["changed"] = raw["single_layer"].pop("*m1,rm1")
    with pytest.raises(ValueError, match="Changed source expression"):
        to_technology_rules(raw, source)


def test_missing_reviewed_rule_fails(tmp_path: Path) -> None:
    source = make_source(tmp_path, "gf180")
    raw = parse_magic(source.read_text(), "gf180", source.name)
    del raw["single_layer"]["*m1,rm1"]
    with pytest.raises(ValueError, match="Missing reviewed rules"):
        to_technology_rules(raw, source)


def test_conditional_options_remain_unapplied(tmp_path: Path) -> None:
    source = make_source(tmp_path, "gf180")
    raw = parse_magic(source.read_text(), "gf180", source.name)
    raw["single_layer"]["*m1,rm1"][0]["options"] = ["angles"]
    converted = to_technology_rules(raw, source)
    assert "34" not in converted["min_width"]


def test_inactive_variants_remain_unapplied(tmp_path: Path) -> None:
    source = make_source(tmp_path, "gf180")
    raw = parse_magic(source.read_text(), "gf180", source.name)
    raw["single_layer"]["*m1,rm1"][0]["variants"] = ["fast"]
    converted = to_technology_rules(raw, source)
    assert "34" not in converted["min_width"]


@pytest.mark.parametrize("text", [
    "cifoutput\nstyle gdsii\nscalefactor 50\n gridlimit 5\nend",
    "cifoutput\nstyle gdsii\nscalefactor 0 nanometers\ngridlimit 5\nend",
    "cifoutput\nstyle gdsii\nscalefactor 50 nanometers\ngridlimit 0\nend",
    "drc\nend",
])
def test_unverified_physical_scale_is_rejected(text: str) -> None:
    with pytest.raises(ValueError):
        output_metadata(text)


def test_fractional_drc_scale_is_used(tmp_path: Path) -> None:
    source = make_source(tmp_path, "gf180")
    source.write_text(source.read_text().replace("scalefactor 100", "scalefactor 100 2"))
    raw = parse_magic(source.read_text(), "gf180", source.name)
    converted = to_technology_rules(raw, source)
    assert converted["min_width"]["34"] == 231
