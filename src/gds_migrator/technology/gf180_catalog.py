"""Export documented GF180 distances, without executing a DRC deck."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, TypedDict

from gds_migrator.technology.rules import read_klayout_layer_map


RuleType = Literal["width", "spacing", "overlap", "enclosure"]
JSONValue = str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
TABLES = Path("physical_verification_gf180") / "design_manual" / "tables_clear"
TECH = Path("gf180mcuA") / "libs.tech"
METALS = tuple(f"metal{n}" for n in range(1, 6)) + ("metaltop",)
VIAS = tuple(f"via{n}" for n in range(1, 6))
UBMS = ("ubmpperi", "ubmparray", "ubmeplate")
NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
SCALAR = re.compile(rf"({NUMBER})\s*(?:[u\u03bc\u00b5]m)?\s*")


class SimpleRule(TypedDict):
    id: str
    desc: str
    layer1: str
    layer2: str
    type: RuleType
    value: float
    context: dict[str, JSONValue]
    source: dict[str, JSONValue]


@dataclass(frozen=True)
class Row:
    id: str
    raw_id: str
    desc: str
    parent: str
    columns: tuple[str, ...]
    cells: tuple[str, ...]
    path: Path
    record: int
    line: int
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class Relation:
    type: RuleType
    layer1: str
    layer2: str
    context: dict[str, JSONValue] = field(default_factory=dict)


@dataclass(frozen=True)
class Value:
    number: float
    condition: str = ""
    levels: tuple[int, ...] = ()
    kind: RuleType | None = None


def clean(text: str) -> str:
    return " ".join(text.split())


def rule_id(text: str) -> str:
    text = re.sub(r"\s+", "", text.replace("\\", "").replace("*", ""))
    return text.split("LDMOS")[0]


def read_rows(root: Path) -> tuple[list[Row], list[dict[str, JSONValue]]]:
    paths = sorted((root / TABLES).glob("*.csv"))
    if not paths:
        raise FileNotFoundError(f"No GF180 CSV tables in {root / TABLES}")
    rows: list[Row] = []
    supporting: list[dict[str, JSONValue]] = []
    for path in paths:
        with path.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.reader(stream, strict=True)
            try:
                header = next(reader)
                if len(header) < 2 or "DESCRIPTION" not in clean(header[1]).upper():
                    supporting.append({
                        "path": str(path.relative_to(root)),
                        "reason": "Supporting table, not an identified design-rule table.",
                    })
                    continue
                columns = tuple(clean(cell) for cell in header[2:])
                current_id = raw_id = parent = ""
                notes: list[str] = []
                for record, cells in enumerate(reader, 2):
                    line = reader.line_num
                    if not cells or not any(cell.strip() for cell in cells):
                        continue
                    if len(cells) != len(header):
                        raise ValueError(f"{path}:{line}: Expected {len(header)} columns")
                    first, desc = cells[:2]
                    if first.strip().lower() == "layer" or (
                        not first.strip() and not desc.strip() and not current_id
                    ):
                        if desc.strip():
                            notes.append(clean(desc))
                        columns = tuple(
                            clean(cell) or previous
                            for cell, previous in zip(cells[2:], columns)
                        )
                        continue
                    if first.strip():
                        raw_id = first
                        current_id = rule_id(first)
                        parent = clean(desc)
                    if not current_id:
                        notes.append(clean(desc))
                        continue
                    exported_id = current_id
                    exported_parent = parent
                    if path.name == "68_ESD8_Metal_Slotting_183.csv" and (
                        clean(desc) == "Minimum distance to these layers"
                    ):
                        exported_id = "MSLOT.9"
                        exported_parent = (
                            "Slots to MetalTop under Pad, MIM bottom plate under "
                            "FuseTop, MetalTop under FuseWindow_D, Metal3 enclosed "
                            "by FuseTop, and MetalTop under UBM layers."
                        )
                    rows.append(Row(
                        exported_id, raw_id, clean(desc), exported_parent, columns,
                        tuple(cell.strip() for cell in cells[2:]),
                        path.relative_to(root), record, line, tuple(notes),
                    ))
            except (csv.Error, StopIteration) as error:
                raise ValueError(f"Invalid CSV table {path}: {error}") from error
    return rows, supporting


def relation_map() -> dict[str, tuple[Relation, ...]]:
    result: dict[str, tuple[Relation, ...]] = {}

    def add(ids: str, kind: RuleType, first: str, second: str = "") -> None:
        for name in ids.split():
            if name in result:
                raise ValueError(f"Duplicate relation definition: {name}")
            result[name] = (Relation(kind, first, second or first),)

    add("DN.1", "width", "dnwell")
    add("DN.2a DN.2b", "spacing", "dnwell")
    add("LPW.1 LPW.Note.1", "width", "lvpwell")
    add("LPW.2a LPW.2b", "spacing", "lvpwell")
    add("LPW.3", "enclosure", "dnwell", "lvpwell")
    add("LPW.11", "spacing", "lvpwell", "dnwell")
    add("NW.1a NW.1b", "width", "nwell")
    add("NW.2a NW.2b", "spacing", "nwell")
    add("NW.3", "spacing", "nwell", "dnwell")
    add("NW.4", "spacing", "nwell", "lvpwell")
    add("NW.5", "enclosure", "dnwell", "nwell")
    add("DF.1a DF.1b DF.1c DF.2a DF.2b DF.11", "width", "comp")
    add("DF.3a DF.3b DF.3c DF.13 DF.14", "spacing", "comp")
    add("DF.4a DF.7", "spacing", "lvpwell", "comp")
    add("DF.4b DF.4e", "overlap", "dnwell", "comp")
    add("DF.4c DF.4d", "overlap", "nwell", "comp")
    add("DF.5 DF.8", "overlap", "lvpwell", "comp")
    add("DF.6", "overlap", "comp", "poly2")
    add("DF.16 DF.17", "spacing", "nwell", "comp")
    add("DF.18 DF.19", "spacing", "dnwell", "comp")
    add("DV.1", "enclosure", "dualgate", "dnwell")
    add("DV.2", "spacing", "dualgate")
    add("DV.3", "spacing", "dualgate", "comp")
    add("DV.5", "width", "dualgate")
    add("DV.6", "enclosure", "dualgate", "comp")
    add("DV.8", "enclosure", "dualgate", "poly2")
    add("PL.1 PL.1a PL.2 PL.7", "width", "poly2")
    add("PL.3a PL.3b", "spacing", "poly2")
    add("PL.4", "overlap", "poly2", "comp")
    add("PL.5a PL.5b", "spacing", "poly2", "comp")
    add("PL.12", "enclosure", "v5_xtor", "comp")
    for prefix, implant in (("NP", "nplus"), ("PP", "pplus")):
        add(f"{prefix}.1", "width", implant)
        add(f"{prefix}.2", "spacing", implant)
        add(" ".join(f"{prefix}.3{s}" for s in "abcde"), "spacing", implant, "comp")
        add(f"{prefix}.4a {prefix}.4b {prefix}.7", "spacing", implant, "poly2")
        add(f"{prefix}.5a {prefix}.9", "overlap", implant, "poly2")
        add(
            " ".join(f"{prefix}.{s}" for s in ("5b", "5c", "5d", "6", "10")),
            "overlap", implant, "comp",
        )
    add("SB.1", "width", "sab")
    add("SB.2", "spacing", "sab")
    add("SB.3", "spacing", "sab", "comp")
    add("SB.4", "spacing", "sab", "contact")
    add("SB.5a SB.5b", "spacing", "sab", "poly2")
    add("SB.6 SB.11", "overlap", "sab", "comp")
    add("SB.7", "overlap", "comp", "sab")
    add("SB.9 SB.12", "overlap", "sab", "poly2")
    add("SB.10", "overlap", "poly2", "sab")
    add("SB.14a SB.14b", "spacing", "poly2")
    for name in ("SB.15a", "SB.15b"):
        result[name] = tuple(Relation("spacing", "poly2", p) for p in ("nplus", "pplus"))
    add("ESD.1", "width", "esd")
    add("ESD.2", "spacing", "esd")
    add("ESD.3a ESD.3b", "spacing", "esd", "comp")
    add("ESD.4a ESD.4b", "overlap", "esd", "comp")
    add("ESD.6", "overlap", "esd", "poly2")
    result["ESD.8"] = tuple(Relation("spacing", "esd", p) for p in ("nplus", "pplus"))
    add("ESD.PL", "width", "poly2")
    add("ESD.10", "overlap", "lvs_io", "comp")
    add("CO.1", "width", "contact")
    add("CO.2a CO.2b", "spacing", "contact")
    add("CO.3", "overlap", "poly2", "contact")
    add("CO.4", "overlap", "comp", "contact")
    add("CO.5a", "overlap", "nplus", "contact")
    add("CO.5b", "overlap", "pplus", "contact")
    add("CO.6 CO.6_ii", "overlap", "metal1", "contact")
    add("CO.7", "spacing", "contact", "poly2")
    add("CO.8", "spacing", "contact", "comp")
    add("MC.1", "width", "mcell_feol_mk")
    add("MC.2", "spacing", "mcell_feol_mk")
    add("PAD.1 TM.1a PM.2", "width", "pad")
    add("PAD.2", "spacing", "pad")
    add("PAD.4 PM.4", "overlap", "metaltop", "pad")
    for number, metal, via in (
        (7, 5, 4), (8, 4, 4), (9, 4, 3), (10, 3, 3),
        (11, 3, 2), (12, 2, 2), (13, 2, 1), (14, 1, 1),
    ):
        add(f"PAD.{number}", "overlap", f"metal{metal}", f"via{via}")
    add("PAD.15 PAD.16", "spacing", "pad", "guard_ring_mk")
    add("PAD.17", "spacing", "pad", "comp")
    add("PAD.18", "spacing", "pad", "poly2")
    add("PAD.19b", "spacing", "pad", "metaltop")
    add("PAD.20", "spacing", "metaltop")
    add("TM.3a", "width", "metaltop")
    add("PM.1", "overlap", "probe_mk", "pad")
    result["TM.2a"] = tuple(Relation("width", p, p) for p in UBMS)
    result["TM.3b"] = tuple(Relation("overlap", "metaltop", p) for p in UBMS)
    result["PM.5"] = tuple(Relation("spacing", "pad", p) for p in UBMS)
    for prefix, implant in (("PRES", "pplus"), ("LRES", "nplus")):
        add(f"{prefix}.1", "width", "poly2")
        add(f"{prefix}.2 {prefix}.4", "spacing", "poly2")
        add(f"{prefix}.3", "spacing", "poly2", "comp")
        add(f"{prefix}.5", "overlap", implant, "poly2")
        add(f"{prefix}.6", "overlap", "sab", "poly2")
        add(f"{prefix}.7", "spacing", "sab", "contact")
        add(f"{prefix}.9", "spacing", "res_mk")
    add("HRES.1", "spacing", "resistor")
    add("HRES.2", "width", "poly2")
    add("HRES.3", "spacing", "poly2")
    add("HRES.4", "overlap", "resistor", "poly2")
    add("HRES.5", "spacing", "resistor", "poly2")
    add("HRES.6", "spacing", "resistor", "comp")
    add("HRES.7", "overlap", "pplus", "contact")
    add("HRES.8", "spacing", "sab", "contact")
    add("HRES.9", "overlap", "sab", "poly2")
    add("HRES.10", "overlap", "pplus", "sab")
    add("HRES.12", "spacing", "res_mk")
    add("NAT.1", "overlap", "nat", "comp")
    add("NAT.2", "spacing", "nat", "comp")
    add("NAT.3", "spacing", "nat", "nwell")
    add("NAT.4 NAT.5", "width", "poly2")
    add("NAT.7", "spacing", "nat")
    add("NAT.8", "overlap", "dualgate", "nat")
    add("NAT.9", "spacing", "nat", "poly2")
    add("BJT.1", "overlap", "drc_bjt", "dnwell")
    add("BJT.2", "overlap", "drc_bjt", "comp")
    add("BJT.3", "spacing", "drc_bjt", "comp")
    result["DE.2"] = tuple(Relation("width", p, p) for p in ("ndmy", "pmndmy"))
    add("DE.3", "width", "ndmy")
    add("DE.4", "spacing", "ndmy")
    add("LVS_BJT.1", "enclosure", "lvs_bjt", "comp")
    add("EF.01", "spacing", "poly2", "plfuse")
    add("EF.02 EF.03", "width", "plfuse")
    add("EF.04a", "overlap", "plfuse", "poly2")
    add("EF.05", "overlap", "lvs_source", "poly2")
    add("EF.06 EF.07 EF.08 EF.09 EF.21", "width", "poly2")
    add("EF.10 EF.11", "spacing", "poly2")
    add("EF.12 EF.13", "spacing", "contact", "plfuse")
    add("EF.14", "enclosure", "efuse_mk", "lvs_source")
    add("EF.17", "spacing", "efuse_mk")
    result["EF.19"] = tuple(Relation("spacing", "plfuse", p) for p in METALS[:2])
    result["EF.20"] = tuple(
        Relation("spacing", "plfuse", p) for p in ("comp", "nplus", "esd", "sab", "resistor")
    )
    add("EF.22a EF.22b", "overlap", "poly2", "plfuse")
    add("MDN.1", "width", "mvsd")
    add("MDN.2a MDN.2b", "spacing", "mvsd")
    add("MDN.3a MDN.3b MDN.10a MDP.1 MDP.1a MDP.9a", "width", "poly2")
    add("MDN.4a MDN.4b MDN.13a MDN.15a MDP.2 MDP.13a MDP.16a", "width", "comp")
    add("MDN.5ai MDN.5aii", "spacing", "comp", "mvsd")
    add("MDN.5b MDN.5c MDP.3b MDP.3c", "spacing", "comp")
    add("MDN.6a MDP.5a", "enclosure", "dualgate", "comp")
    add("MDN.7a MDP.6a", "enclosure", "ldmos_xtor", "dualgate")
    add("MDN.8a MDN.8b", "spacing", "mvsd", "nwell")
    add("MDN.9", "spacing", "mvsd", "comp")
    add("MDN.10b MDN.10c MDP.9b MDP.9c", "overlap", "poly2", "comp")
    add("MDN.10d MDN.10ei MDN.10eii MDP.9d MDP.9ei MDP.9eii",
        "spacing", "poly2", "comp")
    add("MDN.11", "overlap", "mvsd", "comp")
    add("MDN.12", "enclosure", "mvsd", "comp")
    add("MDN.14", "spacing", "mvsd", "dnwell")
    add("MDN.15b MDP.16b", "enclosure", "comp", "contact")
    add("MDP.3ai MDP.3aii", "spacing", "comp", "mvpsd")
    add("MDP.4a MDP.4b", "spacing", "comp", "dnwell")
    add("MDP.7", "spacing", "ldmos_xtor", "nwell")
    add("MDP.8", "spacing", "ldmos_xtor", "comp")
    add("MDP.10", "overlap", "mvpsd", "comp")
    add("MDP.10a MDP.10b", "spacing", "mvpsd")
    add("MDP.11", "enclosure", "mvpsd", "comp")
    add("MDP.12", "enclosure", "dnwell", "comp")
    add("MDP.15", "spacing", "dnwell")
    add("SK.1", "width", "schottky_diode")
    add("SK.2", "spacing", "schottky_diode")
    add("SK.3", "enclosure", "schottky_diode", "comp")
    add("SK.5", "spacing", "comp")
    add("SK.6", "spacing", "comp", "nplus")
    add("SK.7", "spacing", "comp", "pplus")
    add("SK.9", "enclosure", "dnwell", "comp")
    add("SK.10", "spacing", "schottky_diode", "comp")
    add("SK.11", "spacing", "schottky_diode", "lvpwell")
    add("SK.15", "spacing", "comp", "nwell")
    add("SK.16", "width", "comp")
    add("M1.1", "width", "metal1")
    add("GR.1", "overlap", "guard_ring_mk", "comp")
    add("GR.3", "overlap", "pplus", "comp")
    add("GR.6", "width", "comp")
    add("GR.7", "spacing", "contact")
    add("DCF.1c", "width", "comp_dummy")
    add("DCF.2a DCF.2b", "spacing", "comp_dummy")
    add("DCF.4", "spacing", "comp_dummy", "comp")
    add("DCF.5", "spacing", "comp_dummy", "poly2")
    for suffix, layer in (("a", "nwell"), ("b", "dnwell"), ("c", "lvpwell"), ("d", "dualgate")):
        add(f"DCF.6{suffix}", "spacing", "comp_dummy", layer)
        add(f"DPF.6{suffix}", "spacing", "poly2_dummy", layer)
    add("DCF.8a", "spacing", "comp_dummy", "res_mk")
    add("DCF.9", "spacing", "comp_dummy", "pad")
    add("DCF.11a", "spacing", "comp_dummy", "ndmy")
    add("DCF.12", "spacing", "comp_dummy", "ind_mk")
    add("DPF.2a DPF.2b", "spacing", "poly2_dummy")
    result["DPF.1"] = (
        Relation("overlap", "poly2_dummy", "comp_dummy"),
        Relation("width", "poly2_dummy", "poly2_dummy"),
    )
    add("DPF.4", "spacing", "poly2_dummy", "comp")
    add("DPF.5", "spacing", "poly2_dummy", "poly2")
    add("DPF.8", "spacing", "poly2_dummy", "res_mk")
    add("DPF.9", "spacing", "poly2_dummy", "pad")
    add("DPF.11", "spacing", "poly2_dummy", "ndmy")
    add("DPF.12", "spacing", "poly2_dummy", "metal1")
    add("DPF.13", "spacing", "poly2_dummy", "metal2")
    add("DPF.14", "spacing", "poly2_dummy", "ind_mk")
    add("DPF.16", "spacing", "poly2_dummy", "mtpmark")
    add("DPF.19", "spacing", "poly2_dummy", "pmndmy")
    add("LU.1 LU.2 LU.3 LU.4", "spacing", "comp")
    add("IO.0", "overlap", "latchup_mk", "comp")
    add("IO.2", "width", "nwell")
    add("IO.4", "width", "comp")
    return result


RELATIONS = relation_map()
EXCLUDED_IDS = {
    "TM.1": "Symbolic pad dimension A; no scalar distance.",
    "TM.2": "Symbolic dimension A + 20; no scalar distance.",
    "TM.3": "Symbolic dimension A + 32; no scalar distance.",
    "TM.4": "Centre-to-die-edge distance, not edge spacing.",
    "TM.5": "Pitch formula, not a scalar edge spacing.",
    "PM.3": "Centre-to-centre pitch, not edge spacing.",
    "DCF.1a": "Threshold triggering dummy insertion, not a spacing constraint.",
    "DCF.3": "Array staggering offset, not edge spacing.",
    "DPF.3": "Array staggering offset, not edge spacing.",
    "DM.9": "Pattern offset, not edge spacing.",
    "DM.10": "Pattern offset, not edge spacing.",
    "DCF.8b": "Dimension triggering dummy-row insertion, not a width constraint.",
    "DCF.11b": "Dimension triggering dummy-row insertion, not a width constraint.",
    "DCF.13": "Dimension triggering dummy-row insertion, not a width constraint.",
    "DCF.7a": "Scribe-line boundary has no identified mask in these sources.",
    "DCF.7b": "Frame boundary has no identified mask in these sources.",
    "DCF.7c": "Frame-cell boundary has no identified mask in these sources.",
    "DCF.7d": "Test-pattern boundary has no identified mask in these sources.",
    "DPF.7": "Scribe-line boundary has no identified mask in these sources.",
}


def base_id(row: Row) -> str:
    name = re.sub(r"^(?:O|Y|EE|S)\.", "", row.id)
    name = re.sub(r"_(?:LV|MV)$", "", name)
    if name == "Note.1" and row.path.name.startswith("12_"):
        return "LPW.Note.1"
    return name


def exclusion(row: Row) -> str | None:
    name = base_id(row)
    if name in EXCLUDED_IDS:
        return EXCLUDED_IDS[name]
    desc = row.desc.lower()
    if any(re.search(r"[u\u03bc\u00b5]m(?:2|\u00b2)", cell) for cell in row.cells):
        return "Area, not a distance."
    if re.search(r"area|density|ratio|coverage", desc):
        if name.startswith("ANT.") or re.search(
            r"(?:minimum|min\.?|maximum|max\.?)\s+(?:single |field |mim cap |"
            r"mim |nplus |pplus |mcell |metaltop |metaln |comp )?area"
            r"|current density|global density|coverage over|size \(um2\)", desc
        ):
            return "Area, density, current or antenna ratio, not a distance."
    if "number of" in desc or "exact number" in desc:
        return "Object count, not a distance."
    if "total finger width" in desc:
        return "Aggregate finger width, not a single-layer geometric width."
    return None


def finite_number(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Invalid non-negative finite distance: {raw}")
    return value


def parse_values(row: Row, raw: str) -> list[Value]:
    name = base_id(row)
    stripped = raw.replace("\\", "").replace("*", "").strip()
    if name == "DPF.1" and not stripped:
        if "0.3um per side" not in row.desc or "5.6 umX 5.6um square" not in row.desc:
            raise ValueError("Changed DPF.1 embedded dimensions")
        return [
            Value(0.3, "Dummy Poly2 extension beyond dummy COMP, per side", kind="overlap"),
            Value(5.6, "Square: width = length = 5.6 um", kind="width"),
        ]
    if name == "CUP.7" and not stripped and "Space = 1.3um" in row.desc:
        return [Value(1.3, "Guideline: individual top vias under pad opening")]
    match = SCALAR.fullmatch(stripped)
    if match:
        return [Value(finite_number(match[1]))]
    if not stripped or stripped.upper() in {"NA", "--", "-", "DRC", "DRC CHECK"}:
        return []
    if name in {"MT.1", "MT.2a", "MT.2b"}:
        first, second = stripped.split("/")
        return [
            Value(finite_number(first), "MetalTop thickness: 6000 Angstrom"),
            Value(finite_number(second), "MetalTop thickness: 9000 or 11000 Angstrom"),
        ]
    if name == "PL.2" and "/" in stripped:
        first, second = stripped.split("/")
        return [Value(finite_number(first), "NMOS"), Value(finite_number(second), "PMOS")]
    if name == "TM.3b" and stripped == "6 / per side":
        return [Value(6, "Per side")]
    if name == "DCF.1c" and stripped == "5X5":
        return [Value(5, "Square: width = length = 5 um")]
    parts = [part.strip() for part in raw.splitlines() if part.strip()]
    if name in {"Mn.1", "Mn.2a"}:
        expected = ("0.23 (n = 1)", "0.28 (2 <= n <= 5)")
        if tuple(parts) != expected:
            raise ValueError(f"Changed metal value format: {raw}")
        return [Value(0.23, "n = 1", (1,)), Value(0.28, "2 <= n <= 5", (2, 3, 4, 5))]
    if name in {"CO.6", "Vn.3", "Vn.4"}:
        conditions = (
            "Base overlap",
            "End-of-line overlap for metal width < 0.34 um",
            "Adjacent-edge overlap when overlap on one side is < 0.04 um",
            "Guideline: overlap on all sides for minimum resistance variation",
        )
        if name == "Vn.3":
            if len(parts) != 5 or parts[:2] != [
                "0.00 (n = 1)", "0.01 (2 <= n <= 5)",
            ]:
                raise ValueError(f"Changed via overlap format: {raw}")
            return [
                Value(0, conditions[0], (1,)),
                Value(0.01, conditions[0], (2, 3, 4, 5)),
                *[Value(finite_number(p), c) for p, c in zip(parts[2:], conditions[1:])],
            ]
        if len(parts) != 4:
            raise ValueError(f"Expected four overlap variants: {raw}")
        return [Value(finite_number(p), c) for p, c in zip(parts, conditions)]
    if name == "MT30.1":
        if len(parts) != 2:
            raise ValueError(f"Expected two thick-metal widths: {raw}")
        return [
            Value(finite_number(parts[0]), "Base minimum width"),
            Value(finite_number(parts[1]), "Metal line longer than 1000 um"),
        ]
    if len(parts) > 1 and all(SCALAR.fullmatch(p) for p in parts):
        if name == "PL.3a":
            conditions = ["On COMP", "On field"]
        else:
            conditions = re.split(r"\b[IVX]+\\?\)\s*", row.desc)[1:]
        if len(parts) != len(conditions):
            raise ValueError(f"{row.id}: Cannot associate values with conditions: {raw}")
        return [
            Value(finite_number(p), clean(c)) for p, c in zip(parts, conditions)
        ]
    if re.match(r"[+\-.\d]", stripped) and re.search(r"\d", stripped):
        raise ValueError(f"{row.id}: Unrecognized dimensional value: {raw}")
    return []


def metal_relation(
    kind: RuleType, first: str, second: str, level: int,
    condition: str = "", stack: int | None = None,
) -> Relation:
    context: dict[str, JSONValue] = {"metal_index": level}
    if condition:
        context["stack_condition"] = condition
    if stack is not None:
        context["metal_stack"] = stack
    return Relation(kind, first, second, context)


def expanded_relations(row: Row) -> tuple[Relation, ...]:
    name = base_id(row)
    if name in RELATIONS:
        return RELATIONS[name]
    out: list[Relation] = []
    if name.startswith("Mn."):
        kind: RuleType = "width" if name == "Mn.1" else "spacing"
        return tuple(
            metal_relation(kind, m, m, n, f"Metal stack has more than {n} levels")
            for n, m in enumerate(METALS[:5], 1)
        )
    if name.startswith("Vn."):
        for n, via in enumerate(VIAS, 1):
            if name in {"Vn.1", "Vn.2a", "Vn.2b"}:
                kind = "width" if name == "Vn.1" else "spacing"
                out.append(metal_relation(kind, via, via, n, f"At least {n + 1} metal levels"))
            elif name == "Vn.3":
                out.append(metal_relation(
                    "overlap", f"metal{n}", via, n, f"At least {n + 1} metal levels",
                ))
            elif name == "Vn.4":
                if n < 5:
                    out.append(metal_relation(
                        "overlap", f"metal{n + 1}", via, n,
                        f"More than {n + 1} metal levels",
                    ))
                out.append(metal_relation("overlap", "metaltop", via, n, stack=n + 1))
        return tuple(out)
    if name.startswith("MT.") or name in {"MT30.1", "MT30.2", "MT30.3", "MT30.4"}:
        kind = "width" if name in {"MT.1", "MT30.1"} else "spacing"
        return (Relation(kind, "metaltop", "metaltop"),)
    if name in {"MT30.5", "MT30.6", "PAD.5", "PAD.6", "CUP.6", "CUP.8a", "TM.8", "TM.10"}:
        for n, via in enumerate(VIAS, 1):
            if name in {"MT30.5", "MT30.6"}:
                kind, first, others = "enclosure", "metaltop", (via,)
            elif name in {"PAD.5", "PAD.6"}:
                kind = "overlap"
                first = "metaltop" if name == "PAD.5" else f"metal{n}"
                others = (via,)
            else:
                kind, first = "spacing", via
                others = UBMS if name == "TM.8" else ("pad",)
                if name == "CUP.8a":
                    if n < 3:
                        continue
                    first = f"via{n - 1}"
                elif name == "CUP.6" and n < 3:
                    continue
            out.extend(
                metal_relation(kind, first, second, n, stack=n + 1)
                for second in others
            )
        return tuple(out)
    if name in {"CUP.2", "CUP.3", "CUP.7", "GR.4", "GR.5", "GR.8", "PAD.19a", "MDP.3d", "MDP.4"}:
        for n, metal in enumerate(METALS, 1):
            condition = f"More than {n} metal levels" if n < 6 else "Top metal of the selected stack"
            kind, first, second = "spacing", metal, metal
            if name in {"CUP.2", "GR.4"}:
                kind = "width"
            elif name in {"CUP.7", "GR.8"}:
                if n == 6:
                    continue
                first = second = f"via{n}"
                condition = f"At least {n + 1} metal levels"
                if name == "CUP.7" and "individual top vias" in row.desc.lower():
                    if n < 3:
                        continue
                    out.append(metal_relation(kind, first, second, n, stack=n + 1))
                    continue
            elif name == "GR.5":
                second = "guard_ring_mk"
            elif name == "PAD.19a":
                if n == 6:
                    continue
                first, second = "pad", metal
            out.append(metal_relation(kind, first, second, n, condition))
        return tuple(out)
    if name == "GR.2":
        return tuple(
            Relation("spacing", "guard_ring_mk", p)
            for p in ("comp", "nwell", "poly2", *METALS)
        )
    if name.startswith("MIM.") or name.startswith("MIMTM."):
        option_b = name.startswith("MIMTM.")
        suffix = name.split(".")[1]
        for n in ((3, 4, 5) if option_b else (2,)):
            bottom, via = f"metal{n}", f"via{n}"
            pairs: dict[str, tuple[RuleType, str, str]] = {
                "1": ("spacing", bottom, bottom),
                "2": ("overlap", bottom, via),
                "3": ("overlap", bottom, "fusetop"),
                "4": ("overlap", "fusetop", via),
                "5": ("spacing", "fusetop", via),
                "6": ("spacing", "fusetop", "fusetop"),
                "7": ("enclosure", "cap_mk", "fusetop"),
                "9": ("spacing", via, via),
            }
            if suffix in pairs:
                kind, first, second = pairs[suffix]
                out.append(Relation(kind, first, second, {
                    "mim_option": "B" if option_b else "A",
                    "stack_condition": f"{n + 1} metal levels" if option_b else "At least 3 metal levels",
                    "bottom_plate": f"(fusetop sized by 1.06 um) AND ({bottom} interacting fusetop)",
                }))
        return tuple(out)
    if name in {"IO.1", "IO.3"}:
        first = "nwell" if name == "IO.1" and row.desc.startswith("(a2)") else "comp"
        return (Relation("spacing", first, "comp"),)
    esd = re.fullmatch(r"(?:G[._])?(CO)?(LVESD|HVNESD|HVPESD)\.(\d+)(?:\([abc]\))?", name)
    if esd:
        contact_only, device, number = esd.groups()
        if number == "1":
            implants = ("nplus",) if device == "HVNESD" else (
                ("pplus",) if device == "HVPESD" else ("nplus", "pplus")
            )
            return tuple(Relation("enclosure", "esd_mk", p) for p in ("comp", *implants))
        pair: tuple[RuleType, str, str] | None = {
            "3": ("width", "poly2", "poly2"),
            "4": ("width", "comp", "comp"),
            "7": ("spacing", "sab", "contact") if contact_only else ("overlap", "sab", "poly2"),
            "8": ("spacing", "contact", "poly2"),
            "10": ("spacing", "comp", "comp"),
            "11": ("enclosure", "lvs_source", "comp"),
            "12": ("spacing", "lvs_source", "poly2"),
            "13": ("spacing", "contact", "poly2"),
        }.get(number)
        return (Relation(*pair),) if pair else ()
    if name.startswith("DM."):
        for n, metal in enumerate(METALS, 1):
            dummy = f"{metal}_dummy"
            kind, first, seconds = "spacing", dummy, (dummy,)
            if name == "DM.1":
                kind = "width"
            elif name == "DM.2c":
                if metal != "metaltop":
                    continue
            elif name == "DM.3":
                seconds = (metal,)
            elif name == "DM.4":
                if n == 6:
                    continue
                if n < 5:
                    out.append(metal_relation(
                        kind, dummy, f"metal{n + 1}", n, f"More than {n + 1} metal levels",
                    ))
                out.append(metal_relation(kind, dummy, "metaltop", n, stack=n + 1))
                continue
            elif name == "DM.5":
                if n == 6:
                    out.extend(
                        metal_relation(kind, dummy, f"metal{k}", n, stack=k + 1)
                        for k in range(1, 6)
                    )
                    continue
                seconds = ("poly2" if n == 1 else f"metal{n - 1}",)
            elif name == "DM.8":
                seconds = ("fusetop", "polyfuse", "fusewindow_d", "pmndmy", "mtpmark", "otp_mk")
            out.extend(Relation(kind, first, p) for p in seconds)
        return tuple(out)
    if name.startswith("MSLOT."):
        suffix = name.split(".")[1]
        for n, metal in enumerate(METALS, 1):
            slot = f"{metal}_slot"
            if suffix in {"1", "2", "3", "4"}:
                first = metal if suffix == "1" else slot
                kind = "spacing" if suffix == "4" else "width"
                out.append(Relation(kind, first, first))
            elif suffix == "5":
                out.append(Relation("enclosure", metal, slot))
            elif suffix == "7" and n < 6:
                out.append(metal_relation(
                    "spacing", f"via{n}", slot, n, f"More than {n} metal levels",
                ))
            elif suffix == "8":
                if n < 6:
                    out.append(Relation("spacing", "contact" if n == 1 else f"via{n - 1}", slot))
                else:
                    out.extend(
                        metal_relation("spacing", via, slot, k, stack=k + 1)
                        for k, via in enumerate(VIAS, 1)
                    )
            elif suffix == "9":
                selections = (
                    ("metaltop", "MetalTop AND pad"),
                    ("metaltop", "MetalTop AND fusewindow_d"),
                    ("metal3", "Metal3 enclosed by fusetop"),
                    *(("metaltop", f"MetalTop AND {ubm}") for ubm in UBMS),
                    *((bottom, f"MIM bottom plate on {bottom} AND fusetop")
                      for bottom in METALS[1:5]),
                )
                out.extend(
                    Relation("spacing", slot, target, {
                        "layer2_selection": selection,
                        "id_correction": (
                            "CSV omits the rule ID; MSLOT.9 is identified by mslot.rb."
                        ),
                    })
                    for target, selection in selections
                )
        return tuple(out)
    return ()


def read_layer_map(root: Path) -> dict[str, JSONValue]:
    path = root / TECH / "klayout" / "tech" / "gf180mcu.lyp"
    return {
        name: {"layer": layer, "datatype": datatype}
        for name, (layer, datatype) in read_klayout_layer_map(path).items()
    }


def source_index(root: Path) -> dict[str, list[dict[str, JSONValue]]]:
    index: dict[str, list[dict[str, JSONValue]]] = {}
    paths = sorted((root / TECH / "klayout" / "tech" / "drc" / "rule_decks").glob("*.rb"))
    magic = root / TECH / "magic" / "gf180mcuA.tech"
    if not paths:
        raise FileNotFoundError("Missing KLayout rule decks")
    paths.append(magic)
    token = re.compile(r"\b(?:[A-Z][A-Za-z0-9_]*\.)+[0-9]+[a-z]*(?:_[A-Za-z0-9]+)*")
    for path in paths:
        for line, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if path.suffix == ".rb" and ".output(" not in text:
                continue
            if path.suffix == ".tech" and (text.lstrip().startswith("#") or '"' not in text):
                continue
            for name in sorted(set(token.findall(text))):
                index.setdefault(name, []).append({
                    "path": str(path.relative_to(root)), "line": line,
                    "role": "Related rule reference; not value equivalence.",
                })
    return index


def bound(desc: str) -> str:
    desc = desc.lower()
    if re.search(r"min(?:imum)?[.\s]*(?:/|&|and|\.)?\s*max", desc):
        return "exact"
    if re.search(r"\bmax(?:imum)?\b|recommended/max", desc):
        return "maximum"
    if re.search(r"\brecommended\b|\brecommeded\b", desc) and not re.search(r"\bmin", desc):
        return "recommended"
    return "minimum"


def reference_names(row: Row) -> tuple[str, ...]:
    name = row.id
    names = [name, re.sub(r"_(?:LV|MV)$", "", name)]
    if base_id(row) == "CO.6":
        names += ["CO.6a", "CO.6b"]
    if name.startswith("Y."):
        names.append("Y." + name[2:])
    return tuple(dict.fromkeys(names))


def manual_references(root: Path) -> dict[str, list[str]]:
    references: dict[str, list[str]] = {}
    for path in sorted((root / TABLES.parent).glob("*.rst")):
        for name in re.findall(r":file:\s*tables_clear/([^\s]+\.csv)", path.read_text(encoding="utf-8")):
            references.setdefault(name, []).append(str(path.relative_to(root)))
    return references


def build_catalog(root: Path) -> tuple[list[SimpleRule], dict[str, JSONValue]]:
    rows, supporting = read_rows(root)
    layers = read_layer_map(root)
    index = source_index(root)
    manuals = manual_references(root)
    rules: list[SimpleRule] = []
    excluded: list[JSONValue] = []
    unresolved: list[str] = []
    represented: set[tuple[Path, int]] = set()
    for row in rows:
        reason = exclusion(row)
        dimensional_cells = [
            (column, raw) for column, raw in zip(row.columns, row.cells)
            if not re.search(r"comment|for drawing|for drc", column, re.I)
            and raw not in {"For Drawing", "For DRC", "For DRC Check"}
        ]
        if reason:
            excluded.append({
                "id": row.id, "path": str(row.path), "record": row.record,
                "desc": row.desc, "reason": reason,
            })
            continue
        if base_id(row) == "DPF.1" or (
            base_id(row) == "CUP.7" and "Space = 1.3um" in row.desc
        ):
            parsed = [("DESCRIPTION", row.desc, parse_values(row, ""))]
        else:
            parsed = [
                (column, raw, parse_values(row, raw))
                for column, raw in dimensional_cells
            ]
        if not any(values for _, _, values in parsed):
            if any(re.fullmatch(rf"{NUMBER}", raw) for _, raw in dimensional_cells):
                unresolved.append(f"{row.path}:{row.line}: Unhandled numeric rule {row.id}")
            excluded.append({
                "id": row.id, "path": str(row.path), "record": row.record,
                "desc": row.desc, "reason": "No scalar distance in the value columns.",
            })
            continue
        relations = expanded_relations(row)
        if not relations:
            unresolved.append(f"{row.path}:{row.line}: No reviewed layer relation for {row.id}: {row.desc}")
            continue
        for column, raw, values in parsed:
            if not values:
                continue
            for relation in relations:
                for value in values:
                    if value.kind is not None and relation.type != value.kind:
                        continue
                    level = relation.context.get("metal_index")
                    if value.levels and level not in value.levels:
                        continue
                    for layer in (relation.layer1, relation.layer2):
                        if layer not in layers:
                            raise ValueError(f"{row.id}: Unknown physical layer {layer}")
                    context: dict[str, JSONValue] = {
                        "unit": "um",
                        "bound": bound(row.desc),
                        "value_column": column,
                        "condition": row.desc,
                    }
                    if row.parent != row.desc:
                        context["parent_description"] = row.parent
                    if row.notes:
                        context["table_notes"] = list(row.notes)
                    if value.condition:
                        context["variant"] = value.condition
                        if "guideline" in value.condition.lower():
                            context["bound"] = "recommended"
                    if len(values) > 1:
                        context["raw_value"] = raw
                    context.update(relation.context)
                    if row.id.startswith(("O.", "Y.", "EE.", "S.")):
                        context["device_region"] = {
                            "O": "otp_mk", "Y": "ymtp_mk",
                            "EE": "neo_ee_mk", "S": "sramcore",
                        }[row.id.split(".")[0]]
                    if base_id(row).startswith("MT30.") or base_id(row) == "DM.2c":
                        context["metaltop_thickness_um"] = 3.0
                    if row.id.startswith("CUP."):
                        context["minimum_metal_stack"] = 4
                    if "*" in row.raw_id or "guideline" in row.desc.lower() or "guideline" in value.condition.lower():
                        context["documentation_flag"] = (
                            "Guideline or not-coded marker; consult the manual."
                        )
                    if relation.type == "width" and re.search(
                        r"\blength\b", row.parent, re.I,
                    ):
                        context["measurement"] = "length"
                    if row.id in {"CO.1", "Vn.1", "DCF.1c"}:
                        context["bound"] = "exact"
                        context["measurement"] = "square_side"
                    if row.id == "DPF.1" and relation.type == "width":
                        context["bound"] = "exact"
                        context["measurement"] = "square_side"
                    if base_id(row) == "DF.11":
                        context["measurement"] = "butting_edge_length"
                    if row.id == "MSLOT.9":
                        context["id_correction"] = relation.context["id_correction"]
                    references: list[JSONValue] = []
                    for name in reference_names(row):
                        references.extend(index.get(name, []))
                    if base_id(row).startswith(("Mn.", "Vn.", "MSLOT.")):
                        deck = {"Mn": "metal.rb", "Vn": "via.rb", "MSLOT": "mslot.rb"}[
                            base_id(row).split(".")[0]
                        ]
                        references.append({
                            "path": str(TECH / "klayout" / "tech" / "drc" / "rule_decks" / deck),
                            "role": "Parameterized rule family; not value equivalence.",
                        })
                    rules.append({
                        "id": row.id, "desc": row.desc,
                        "layer1": relation.layer1, "layer2": relation.layer2,
                        "type": relation.type, "value": value.number, "context": context,
                        "source": {
                            "csv": str(row.path), "record": row.record,
                            "line_end": row.line, "rule_id": row.raw_id,
                            "manual": manuals.get(row.path.name, []),
                            "references": references,
                        },
                    })
                    represented.add((row.path, row.record))
    if unresolved:
        raise ValueError("Unresolved dimensional rules:\n" + "\n".join(unresolved))
    if not rules:
        raise ValueError("No dimensional rules exported")
    files = sorted({
        *list((root / TABLES).glob("*.csv")),
        root / TECH / "klayout" / "tech" / "gf180mcu.lyp",
        root / TECH / "magic" / "gf180mcuA.tech",
        *list((root / TECH / "klayout" / "tech" / "drc" / "rule_decks").glob("*.rb")),
        *list((root / TABLES.parent).glob("*.rst")),
    })
    report: dict[str, JSONValue] = {
        "technology": "gf180",
        "unit": "um",
        "authority": "CSV manual; KLayout and Magic links are references, not merged values.",
        "semantics": {
            "width": "Same physical layer; see measurement for length or square-side dimensions.",
            "spacing": "Distance between selected edges; maximum tap distances retain maximum bounds.",
            "overlap": "Layer1 overlaps or extends beyond layer2, following manual terminology.",
            "enclosure": "Layer1 encloses layer2; MSLOT.5 is an internal edge margin, not external spacing.",
            "selectors": "NCOMP, PCOMP, gate, device and boundary qualifiers remain in descriptions and context.",
            "applicability": "Process options and guidelines are retained, not simultaneously applicable.",
        },
        "derived_layers": {
            "ncomp": "comp AND nplus",
            "pcomp": "comp AND pplus",
            "gate": "poly2 AND comp",
            "n_channel_gate": "poly2 AND comp AND nplus",
            "p_channel_gate": "poly2 AND comp AND pplus",
            "unsalicided_poly2": "poly2 AND sab",
            "unsalicided_comp": "comp AND sab",
        },
        "known_source_corrections": [
            "Pplus table says 'PP = Nplus Implant'; PP rules use pplus.",
            "MSLOT.9 has no CSV ID; recovered from the matching KLayout rule.",
            "MCELL is mapped to mcell_feol_mk as in KLayout.",
        ],
        "rule_count": len(rules),
        "source_row_count": len(rows),
        "represented_row_count": len(represented),
        "excluded_row_count": len(excluded),
        "layers": layers,
        "excluded": excluded,
        "supporting_tables": supporting,
        "source_sha256": {
            str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files
        },
    }
    return rules, report


def write_catalog(root: Path, output: Path, report_path: Path) -> tuple[int, int]:
    if output.resolve() == report_path.resolve():
        raise ValueError("The catalog and report must have different output paths")
    if output.suffix.lower() != ".json" or report_path.suffix.lower() != ".json":
        raise ValueError("The catalog and report output paths must use .json")
    rules, report = build_catalog(root)
    for path, payload in ((output, rules), (report_path, report)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=True, allow_nan=False, indent=2) + "\n",
            encoding="utf-8",
        )
    exclusions = report["excluded_row_count"]
    if not isinstance(exclusions, int):
        raise TypeError("Invalid exclusion count")
    return len(rules), exclusions


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--drc-root", type=Path, default=Path("drc"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    output = args.output or args.drc_root / "gf180_simplified.json"
    report = args.report or args.drc_root / "gf180_simplified_report.json"
    count, exclusions = write_catalog(args.drc_root, output, report)
    print(f"Wrote {count} distance rules to {output}")
    print(f"Recorded {exclusions} excluded rows in {report}")


if __name__ == "__main__":
    main()
