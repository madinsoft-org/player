import csv
import json
from pathlib import Path

import pytest

from src_new.extract_csv_rules import (
    SOURCE_DIRECTORIES,
    extract_catalog,
    parse_value,
    write_catalog,
)


def make_csv(root: Path, technology: str, name: str, rows: list[list[str]]) -> Path:
    path = root / SOURCE_DIRECTORIES[technology] / name
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        csv.writer(stream).writerows(rows)
    return path


def test_sky130_preserves_flags_units_context_and_duplicate_ids(tmp_path: Path) -> None:
    rows = [
        ["Name", "Description", "Flags", "Value", "Unit"],
        ["(m1.-)", "Metal context", "", "", ""],
        ["(m1.1)", "Minimum width", "P AL", "0.140", "\u00b5m"],
        ["(m1.1)", "Exception\non another flow", "CU", "", ""],
        ["(m1.6)", "Minimum area", "", "0.083", "\u00b5m\u00b2"],
    ]
    make_csv(tmp_path, "sky130", "periphery/metal.csv", rows)
    make_csv(tmp_path, "sky130", "periphery/periphery.csv", [["aggregate"]])
    catalog = extract_catalog(tmp_path, "sky130")
    assert catalog.rule_count == 3
    assert catalog.rules[0].rule_id == "m1.1"
    assert catalog.rules[0].flags == ["P", "AL"]
    assert catalog.rules[0].values[0].number == 0.14
    assert catalog.rules[0].values[0].unit == "um"
    assert catalog.rules[0].context == ["Metal context"]
    assert catalog.rules[1].description == "Exception\non another flow"
    assert catalog.rules[2].values[0].unit == "um^2"
    table = next(table for table in catalog.tables if table.kind == "rule_table")
    assert [row.cells for row in table.rows] == rows
    assert catalog.rules[2].line == 6


def test_gf180_voltage_columns_and_continuations(tmp_path: Path) -> None:
    make_csv(tmp_path, "gf180", "rules.csv", [
        ["RULE NO.", "DESCRIPTION", "LAYOUT", ""],
        ["Layer", "Nwell", "3.3V", "5V/6V"],
        ["NW.2a", "Minimum spacing", "0.6", "0.74"],
        ["", "Conditional sub-rule", "0.6/0.5", "2 if width > 1"],
        ["NW.3*", "Forbidden overlap", "0", "NA"],
    ])
    catalog = extract_catalog(tmp_path, "gf180")
    assert catalog.rule_count == 3
    rule = catalog.rules[0]
    assert [value.column for value in rule.values] == ["3.3V", "5V/6V"]
    assert [value.number for value in rule.values] == [0.6, 0.74]
    assert rule.values[0].unit is None
    assert catalog.rules[1].continuation
    assert catalog.rules[1].rule_id == "NW.2a"
    assert all(value.number is None for value in catalog.rules[1].values)
    assert catalog.rules[2].rule_id == "NW.3"
    assert catalog.rules[2].values[0].number == 0


def test_non_coded_references_and_supporting_tables(tmp_path: Path) -> None:
    make_csv(tmp_path, "gf180", "70_not_coded_rules_187.csv", [
        [r"\* NW.7", "Guideline not checked by DRC"],
    ])
    matrix = [["", "Metal1", "Metal2"], ["Ratio", "400", "400"]]
    make_csv(tmp_path, "gf180", "antenna.csv", matrix)
    catalog = extract_catalog(tmp_path, "gf180")
    assert catalog.rules[0].rule_id == "NW.7"
    assert catalog.rules[0].kind == "non_coded_reference"
    table = next(table for table in catalog.tables if table.kind == "supporting_table")
    assert [row.cells for row in table.rows] == matrix
    output = tmp_path / "output" / "rules.json"
    write_catalog(catalog, output)
    first = output.read_bytes()
    write_catalog(extract_catalog(tmp_path, "gf180"), output)
    assert output.read_bytes() == first
    assert json.loads(first)["rule_count"] == 1


@pytest.mark.parametrize(("raw", "number", "unit"), [
    ("25%", 25, "%"),
    ("0.1444 um2", 0.1444, "um^2"),
    ("1mA/um", 1, "mA/um"),
    ("1e-3", 0.001, None),
    ("0.23 (n = 1)\n0.28 (2 <= n <= 5)", None, None),
    ("", None, None),
    ("NA", None, None),
])
def test_scalar_values(raw: str, number: float | None, unit: str | None) -> None:
    value = parse_value(raw, "Layout")
    assert value.raw == raw
    assert value.number == number
    assert value.unit == unit


def test_non_finite_number_is_rejected() -> None:
    with pytest.raises(ValueError, match="Non-finite"):
        parse_value("1e999", "Layout")


def test_invalid_csv_is_rejected(tmp_path: Path) -> None:
    path = make_csv(tmp_path, "gf180", "broken.csv", [["placeholder"]])
    path.write_text('"unclosed', encoding="utf-8")
    with pytest.raises(ValueError, match="broken.csv"):
        extract_catalog(tmp_path, "gf180")


def test_malformed_rule_is_rejected(tmp_path: Path) -> None:
    make_csv(tmp_path, "sky130", "rules.csv", [
        ["Name", "Description", "Flags", "Value", "Unit"],
        ["(m1.1)", "Missing columns"],
    ])
    with pytest.raises(ValueError, match="Expected five columns"):
        extract_catalog(tmp_path, "sky130")


@pytest.mark.parametrize("technology", ["sky130", "gf180"])
def test_missing_sources_are_rejected(tmp_path: Path, technology: str) -> None:
    with pytest.raises(FileNotFoundError):
        extract_catalog(tmp_path, technology)


def test_unsupported_technology_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        extract_catalog(tmp_path, "unknown")


def test_empty_sources_are_rejected(tmp_path: Path) -> None:
    (tmp_path / SOURCE_DIRECTORIES["sky130"]).mkdir()
    with pytest.raises(ValueError, match="No CSV"):
        extract_catalog(tmp_path, "sky130")
