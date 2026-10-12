"""Command-line entry point: migrate a GDSII file to a target technology."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from gds_migrator.hierarchy.migrator import migrate_hierarchy
from gds_migrator.io.gdsii_reader import read_library
from gds_migrator.io.gdsii_writer import write_library
from gds_migrator.technology.rules import TechnologyRules


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gds-migrate",
        description="Migrate a hierarchical GDSII layout to a target process technology.",
    )
    parser.add_argument("input", type=Path, help="Source GDSII file.")
    parser.add_argument("--technology", type=Path, required=True, help="Target technology rules (JSON).")
    parser.add_argument("--output", type=Path, required=True, help="Destination GDSII file.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)

    if not args.input.is_file():
        print(f"error: input file not found: {args.input}", file=sys.stderr)
        return 1
    if not args.technology.is_file():
        print(f"error: technology file not found: {args.technology}", file=sys.stderr)
        return 1

    tech = TechnologyRules.from_json(args.technology)
    cells = read_library(args.input)
    migrated = migrate_hierarchy(cells, tech)
    write_library(migrated, args.output)
    print(f"migrated {len(cells)} cell(s) to technology {tech.name!r} -> {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
