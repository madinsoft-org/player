"""Parser for KLayout Ruby DRC files."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

CHECK_OPERATIONS = (
    "width",
    "space",
    "separation",
    "enclosing",
    "enclosed",
    "notch",
    "isolated",
    "overlap",
)
_NUMBER = re.compile(r"^-?\d+(?:\.\d+)?$")
_RULE = re.compile(
    r"(?P<expression>[A-Za-z_][\w.]*(?:\s*\([^()]*\))?"
    r"(?:\s*\.\s*[A-Za-z_]\w*(?:\s*\([^()]*\))?)*)\s*\.\s*(?P<operation>"
    + "|".join(CHECK_OPERATIONS)
    + r")\(\s*(?P<arguments>[^()]*)\)\s*\.output\(\s*"
    r'"(?P<rule_id>[^"]+)"(?:\s*,\s*"(?P<description>[^"]*)")?\s*\)',
    re.DOTALL,
)


@dataclass(frozen=True)
class DrcRule:
    """One geometric rule extracted from a KLayout DRC expression."""

    rule_id: str
    operation: str
    expression: str
    value: float | None
    layers: tuple[str, ...]
    description: str | None
    line: int


def _strip_ruby_comments(text: str) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        quote_open = False
        for index, character in enumerate(line):
            if character == '"':
                quote_open = not quote_open
            elif character == "#" and not quote_open:
                line = line[:index]
                break
        lines.append(line)
    return "\n".join(lines)


def _arguments(value: str) -> tuple[float | None, tuple[str, ...]]:
    args = [item.strip() for item in value.split(",") if item.strip()]
    numbers = [float(item) for item in args if _NUMBER.fullmatch(item)]
    layers = tuple(item for item in args if item != "euclidian" and not _NUMBER.fullmatch(item))
    return (numbers[0] if numbers else None), layers


def parse_klayout_drc(text: str) -> list[DrcRule]:
    """Parse DRC source text, including expressions spanning multiple lines."""
    source = _strip_ruby_comments(text)
    rules: list[DrcRule] = []
    for match in _RULE.finditer(source):
        value, layers = _arguments(match.group("arguments"))
        rules.append(
            DrcRule(
                rule_id=match.group("rule_id"),
                operation=match.group("operation"),
                expression=match.group("expression"),
                value=value,
                layers=layers,
                description=match.group("description"),
                line=source.count("\n", 0, match.start()) + 1,
            )
        )
    return rules


def parse_klayout_drc_file(drc_file_path: str | Path) -> list[DrcRule]:
    """Parse a KLayout Ruby DRC file."""
    path = Path(drc_file_path)
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")
    return parse_klayout_drc(path.read_text(encoding="utf-8"))


def dump_rules(rules: Iterable[DrcRule], output_path: str | Path) -> None:
    """Write parsed rules as stable, human-readable JSON."""
    Path(output_path).write_text(
        json.dumps([asdict(rule) for rule in rules], indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("drc_file", type=Path)
    parser.add_argument("-o", "--output", type=Path, default=Path("rules.json"))
    args = parser.parse_args()
    parsed_rules = parse_klayout_drc_file(args.drc_file)
    dump_rules(parsed_rules, args.output)
    print(f"Extracted {len(parsed_rules)} rules to {args.output}")
