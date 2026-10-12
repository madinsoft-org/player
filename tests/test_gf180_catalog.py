import csv
import json
from pathlib import Path

import pytest

from gds_migrator.technology import gf180_catalog
from gds_migrator.technology.gf180_catalog import (
    METALS,
    TABLES,
    Row,
    SimpleRule,
    Value,
    bound,
    build_catalog,
    exclusion,
    expanded_relations,
    parse_values,
    read_rows,
    write_catalog,
)


ROOT = Path(__file__).resolve().parents[1] / "drc"


def make_row(
    name: str = "CO.1", desc: str = "Min/max contact size",
    raw: str = "0.22", parent: str = "",
) -> Row:
    return Row(
        name, name, desc, parent or desc, ("LAYOUT RULE",), (raw,),
        Path("test.csv"), 2, 2,
    )


@pytest.fixture(scope="module")
def catalog() -> list[SimpleRule]:
    return build_catalog(ROOT)[0]


def selected(catalog: list[SimpleRule], name: str) -> list[SimpleRule]:
    return [rule for rule in catalog if rule["id"] == name]


def test_contact_example(catalog: list[SimpleRule]) -> None:
    contact, = selected(catalog, "CO.1")
    assert {key: contact[key] for key in (
        "id", "desc", "layer1", "layer2", "type", "value",
    )} == {
        "id": "CO.1", "desc": "Min/max contact size",
        "layer1": "contact", "layer2": "contact",
        "type": "width", "value": 0.22,
    }
    assert contact["context"]["bound"] == "exact"
    references = str(contact["source"]["references"])
    assert "contact.rb" in references
    assert "gf180mcuA.tech" in references


@pytest.mark.parametrize(("name", "first", "second", "kind", "value"), [
    ("CO.3", "poly2", "contact", "overlap", 0.07),
    ("CO.5a", "nplus", "contact", "overlap", 0.1),
    ("CO.5b", "pplus", "contact", "overlap", 0.1),
    ("CO.7", "contact", "poly2", "spacing", 0.15),
    ("CO.8", "contact", "comp", "spacing", 0.17),
    ("LPW.3", "dnwell", "lvpwell", "enclosure", 2.5),
    ("MIM.7", "cap_mk", "fusetop", "enclosure", 0),
    ("MC.1", "mcell_feol_mk", "mcell_feol_mk", "width", 0.4),
    ("S.CO.6_ii_LV", "metal1", "contact", "overlap", 0.02),
    ("SK.16", "comp", "comp", "width", 0.62),
    ("MDP.11", "mvpsd", "comp", "enclosure", 0.8),
    ("PRES.9", "res_mk", "res_mk", "spacing", 20),
])
def test_reviewed_layer_pairs(
    catalog: list[SimpleRule], name: str, first: str, second: str,
    kind: str, value: float,
) -> None:
    rules = selected(catalog, name)
    assert rules
    assert all(
        (rule["layer1"], rule["layer2"], rule["type"], rule["value"])
        == (first, second, kind, value) for rule in rules
    )


def test_all_metals_are_expanded_without_threshold_leakage(catalog: list[SimpleRule]) -> None:
    for name in ("Mn.1", "Mn.2a", "Mn.2b"):
        rules = selected(catalog, name)
        assert len(rules) == 5
        assert [rule["layer1"] for rule in rules] == list(METALS[:5])
        for n, rule in enumerate(rules, 1):
            assert rule["layer1"] == rule["layer2"]
            expected = 0.3 if name == "Mn.2b" else (0.23 if n == 1 else 0.28)
            assert rule["value"] == expected
            assert rule["context"]["metal_index"] == n


def test_contact_variants_remain_distinct(catalog: list[SimpleRule]) -> None:
    rules = selected(catalog, "CO.6")
    assert [rule["value"] for rule in rules] == [0.005, 0.06, 0.06, 0.12]
    assert len({str(rule["context"]["variant"]) for rule in rules}) == 4
    assert "0.34" in str(rules[1]["context"]["variant"])
    assert "0.04" in str(rules[2]["context"]["variant"])
    assert rules[-1]["context"]["bound"] == "recommended"


def test_voltage_and_device_contexts(catalog: list[SimpleRule]) -> None:
    rules = selected(catalog, "PL.2")
    assert [(r["context"]["value_column"], r["value"]) for r in rules] == [
        ("3.3V", 0.28), ("5V", 0.6), ("5V", 0.5), ("6V", 0.7), ("6V", 0.55),
    ]
    assert [r["context"].get("variant") for r in rules[1:]] == [
        "NMOS", "PMOS", "NMOS", "PMOS",
    ]
    assert all(r["context"]["measurement"] == "length" for r in rules)
    otp = selected(catalog, "O.SB.5b")
    assert [r["value"] for r in otp] == [0.1, 0]
    assert all(r["context"]["device_region"] == "otp_mk" for r in otp)


def test_implant_conditions_keep_order(catalog: list[SimpleRule]) -> None:
    rules = selected(catalog, "NP.3b")
    assert [r["value"] for r in rules] == [0.16, 0.08]
    assert "< 0.43" in str(rules[0]["context"]["variant"])
    assert ">= 0.43" in str(rules[1]["context"]["variant"])
    assert all(r["layer1"] == "pplus" for r in selected(catalog, "PP.1"))


def test_via_levels_and_upper_top_metal(catalog: list[SimpleRule]) -> None:
    lower = selected(catalog, "Vn.3")
    assert len(lower) == 20
    for n in range(1, 6):
        rules = [r for r in lower if r["layer2"] == f"via{n}"]
        assert [r["value"] for r in rules] == [0 if n == 1 else 0.01, 0.06, 0.06, 0.12]
        assert all(r["layer1"] == f"metal{n}" for r in rules)
    upper = selected(catalog, "Vn.4")
    assert len(upper) == 36
    top = [r for r in upper if r["layer1"] == "metaltop"]
    assert {r["context"]["metal_stack"] for r in top} == {2, 3, 4, 5, 6}
    assert not any(r["layer1"] == "metal6" for r in catalog)


def test_top_metal_thickness_and_mim_options(catalog: list[SimpleRule]) -> None:
    assert [r["value"] for r in selected(catalog, "MT.1")] == [0.36, 0.44]
    assert [r["value"] for r in selected(catalog, "MT30.1")] == [1.8, 2.2]
    assert all(r["context"]["metaltop_thickness_um"] == 3 for r in selected(catalog, "MT30.5"))
    assert {r["layer1"] for r in selected(catalog, "MIMTM.2")} == {
        "metal3", "metal4", "metal5",
    }
    assert all(r["context"]["mim_option"] == "B" for r in selected(catalog, "MIMTM.2"))


def test_slot_continuations_and_recovered_id(catalog: list[SimpleRule]) -> None:
    rules = selected(catalog, "MSLOT.3")
    assert len(rules) == 12
    assert {(r["value"], r["context"]["bound"]) for r in rules} == {
        (10, "minimum"), (250, "maximum"),
    }
    assert all(r["context"]["measurement"] == "length" for r in rules)
    assert {r["value"] for r in selected(catalog, "MSLOT.8")} == {0.2}
    recovered = selected(catalog, "MSLOT.9")
    assert recovered and {r["value"] for r in recovered} == {5}
    assert all("CSV omits" in str(r["context"]["id_correction"]) for r in recovered)
    assert all(r["type"] == "enclosure" for r in selected(catalog, "MSLOT.5"))


def test_embedded_dimensions_and_top_via_guideline(catalog: list[SimpleRule]) -> None:
    assert {(r["type"], r["value"]) for r in selected(catalog, "DPF.1")} == {
        ("width", 5.6), ("overlap", 0.3),
    }
    rules = [r for r in selected(catalog, "CUP.7") if r["value"] == 1.3]
    assert len(rules) == 3
    assert {r["context"]["metal_stack"] for r in rules} == {4, 5, 6}
    assert all(r["context"]["value_column"] == "DESCRIPTION" for r in rules)
    assert all(r["context"]["bound"] == "recommended" for r in rules)


def test_maximum_tap_distances_not_minimum(catalog: list[SimpleRule]) -> None:
    rules = selected(catalog, "LU.1")
    assert len(rules) == 6
    assert all(r["context"]["bound"] == "maximum" for r in rules)
    assert all("parent_description" in r["context"] for r in rules)


@pytest.mark.parametrize(("desc", "expected"), [
    ("Min/max contact size", "exact"),
    ("Min. Max. PLFUSE width", "exact"),
    ("Minimum & maximum Pplus overlap", "exact"),
    ("Max. width", "maximum"),
    ("Minimum width", "minimum"),
    ("Recommended width", "recommended"),
])
def test_bounds(desc: str, expected: str) -> None:
    assert bound(desc) == expected


@pytest.mark.parametrize("raw", ["0.25", "0.25um", "0.25 \u03bcm", "0.25\\*"])
def test_scalar_units_and_footnotes(raw: str) -> None:
    assert parse_values(make_row(), raw) == [Value(0.25)]


@pytest.mark.parametrize("raw", ["-1", "1e999", "0.23/0.28", "0.23 (unknown option)"])
def test_invalid_or_ambiguous_value_fails(raw: str) -> None:
    with pytest.raises(ValueError):
        parse_values(make_row(), raw)


def test_predicate_numbers_do_not_become_rule_values() -> None:
    row = make_row("Mn.1", "Width", "0.23 (n = 1)\n\n0.28 (2 <= n <= 5)")
    assert [v.number for v in parse_values(row, row.cells[0])] == [0.23, 0.28]
    assert exclusion(make_row("MIM.8b", "Maximum single MIM Cap area", "100*100um2"))
    assert exclusion(make_row("ANT.1", "Maximum ratio of Poly2 area", "200"))
    assert exclusion(make_row("HVNESD.5(b)", "Max. number of fingers", "18"))


def test_unmapped_relation_is_not_guessed() -> None:
    assert expanded_relations(make_row("UNKNOWN.1", "Minimum width")) == ()


def test_missing_tables_fail(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="No GF180 CSV"):
        read_rows(tmp_path)


def test_malformed_csv_fails(tmp_path: Path) -> None:
    path = tmp_path / TABLES / "test.csv"
    path.parent.mkdir(parents=True)
    path.write_text('RULE NO.,DESCRIPTION,VALUE\nCO.1,"unclosed', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid CSV"):
        read_rows(tmp_path)


def test_voltage_headers_and_continuations(tmp_path: Path) -> None:
    path = tmp_path / TABLES / "test.csv"
    path.parent.mkdir(parents=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        csv.writer(stream).writerows([
            ["RULE NO.", "DESCRIPTION", "LAYOUT", ""],
            ["Layer", "LPW = LVPWELL", "3.3V", "5V/6V"],
            ["LPW.1", "Width", "0.6", "0.74"],
            ["", "Special case", "0.7", "0.8"],
        ])
    rows, _ = read_rows(tmp_path)
    assert rows[0].columns == ("3.3V", "5V/6V")
    assert rows[1].id == "LPW.1"
    assert rows[1].parent == "Width"
    assert rows[1].desc == "Special case"
    assert rows[1].record == 4


def test_complete_schema_and_coverage(catalog: list[SimpleRule]) -> None:
    _, report = build_catalog(ROOT)
    assert report["source_row_count"] == 738
    assert report["represented_row_count"] == 506
    assert report["excluded_row_count"] == 232
    assert len(catalog) == 1062
    assert set(r["type"] for r in catalog) == {"width", "spacing", "overlap", "enclosure"}
    for rule in catalog:
        assert set(rule) == {"id", "desc", "layer1", "layer2", "type", "value", "context", "source"}
        assert isinstance(rule["value"], (int, float))
        assert rule["value"] >= 0
        assert rule["source"]["csv"]
        assert rule["context"]["unit"] == "um"
        if rule["type"] == "width":
            assert rule["layer1"] == rule["layer2"]
    assert not any(r["id"] in {"CO.9", "Mn.3", "MT.4", "ANT.1"} for r in catalog)


def test_persistent_output_is_reproducible(tmp_path: Path) -> None:
    output, report = tmp_path / "rules.json", tmp_path / "report.json"
    write_catalog(ROOT, output, report)
    first = output.read_bytes(), report.read_bytes()
    write_catalog(ROOT, output, report)
    assert first == (output.read_bytes(), report.read_bytes())
    assert isinstance(json.loads(first[0]), list)
    assert output.read_bytes() == (ROOT / "gf180_simplified.json").read_bytes()
    assert report.read_bytes() == (ROOT / "gf180_simplified_report.json").read_bytes()


def test_same_output_paths_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="different output paths"):
        write_catalog(ROOT, tmp_path / "rules.json", tmp_path / "rules.json")


def test_read_layer_map_preserves_gds_datatypes(tmp_path: Path) -> None:
    layer_path = (
        tmp_path / "gf180mcuA" / "libs.tech" / "klayout" / "tech" / "gf180mcu.lyp"
    )
    layer_path.parent.mkdir(parents=True)
    layer_path.write_text(
        "<layer-properties>"
        "<properties><source>metal1 34/0@1</source></properties>"
        "<properties><source>metal1_slot 34/3@1</source></properties>"
        "</layer-properties>",
        encoding="utf-8",
    )

    assert gf180_catalog.read_layer_map(tmp_path) == {
        "metal1": {"layer": 34, "datatype": 0},
        "metal1_slot": {"layer": 34, "datatype": 3},
    }


def test_unknown_numeric_rule_aborts_without_writing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gf180_catalog, "read_rows",
        lambda root: ([make_row("UNKNOWN.1", "Minimum width")], []),
    )
    output, report = tmp_path / "rules.json", tmp_path / "report.json"
    with pytest.raises(ValueError, match="No reviewed layer relation"):
        write_catalog(ROOT, output, report)
    assert not output.exists()
    assert not report.exists()
