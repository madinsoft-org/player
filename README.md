# GDS Migrator

Migrate hierarchical GDSII layouts from a source process technology to a target
technology while preserving cell-to-cell abutment relationships across the
design hierarchy.

## Problem

A GDSII layout is a hierarchy of cells (structures) instantiated through
placed references (`SREF`/`AREF`), each possibly rotated or mirrored. When
porting a design to a new technology node, every layer must satisfy new
design rules (minimum width, minimum spacing, minimum enclosure, grid pitch,
...). Naively rescaling geometry breaks the electrical/physical abutment
between adjacent cells (shared boundaries, pin alignment). This tool computes
a *minimal* geometric perturbation of each cell that satisfies the target
design rules while keeping declared abutment interfaces consistent through
the hierarchy.

## Approach

1. **I/O** (`gds_migrator.io`): read/write GDSII files via `gdstk`.
2. **Technology rules** (`gds_migrator.technology`): per-layer design rules
   (min width, min spacing, grid) loaded from JSON, with a Skywater PDK
   loader as reference implementation
   (see [google/skywater-pdk](https://github.com/google/skywater-pdk)).
3. **Geometry** (`gds_migrator.geometry`): 2D affine transforms (translation,
   Manhattan rotation, mirroring) matching GDSII `SREF`/`AREF` semantics.
4. **Constraints** (`gds_migrator.constraints`):
   - `plane_sweep`: a scanline / plane-sweep pass over the polygons of a cell
     generates, per axis, the minimal set of ordering + spacing constraints
     (`x[j] - x[i] >= min_spacing`) required by the DRC rules.
   - `graph`: a directed weighted constraint graph solved with Bellman-Ford
     as a longest-path problem (one solve per axis, X then Y). A positive
     cycle means the constraints are infeasible for the target technology.
   - `solver`: orchestrates per-axis longest-path solves and exposes an
     optional simplex fallback (HiGHS/Cbc via `highspy`) for the rare cases
     where diagonal / simultaneous 2D constraints are required.
5. **Hierarchy** (`gds_migrator.hierarchy`): bottom-up traversal of the
   design. Each child cell is migrated first, its boundary/abutment
   interface is computed, and the resulting boundary offsets are propagated
   as constraints to the parent before the parent geometry itself is solved.
   Instance transforms (rotation/mirror/translation) are preserved and
   recomposed around the new interface points.

## Development status

This is a Python prototype. The core numerical bottleneck (the per-axis
solve) is isolated behind `gds_migrator.constraints.solver` so it can later
be reimplemented in Rust without touching the rest of the pipeline.

## Usage

```bash
pip install -e .[dev]
pytest
gds-migrate input.gds --technology target_tech.json --output output.gds
```

### Web layout workbench

Start the local FastAPI application with:

```bash
pip install -e .
gds-migrator-web
```

Open `http://127.0.0.1:8000`, then upload a GDSII layout, a technology-rules
JSON file, and optionally a KLayout `.lyp` layer-properties file (or a JSON
layer map with `layer`, `name`, and optional `color` fields). The workbench
supports per-layer visibility, per-cell X/Y constraint inspection, temporary
constraint exclusions, zoom and pan, hierarchical migration, before/after
comparison, and download of the migrated GDSII file. Exclusions are held only
for the lifetime of the in-memory project session.

The viewer displays the axis-aligned bounding rectangles used by the current
migration model; non-rectilinear polygon outlines and GDSII datatypes are not
retained by that model. Uploads are limited to 100 MB per file, and the viewer
limits flattened cell views to 50,000 rectangles.

## Documented design-rule catalogs

Run `python src_new/extract_csv_rules.py` to generate `drc/sky130_rules.json`
and `drc/gf180_rules.json` from the local CSV documentation. Override the
locations with `--drc-root` and `--output-dir` if needed.

The expected source directories below the DRC root are `rules_sky` and
`physical_verification_gf180/design_manual/tables_clear`. No third-party
dependencies or network access are required.

Each catalog contains indexed `rules` and lossless `tables` with source paths,
SHA-256 hashes, CSV record numbers, and physical line numbers. SKY130's
per-layer periphery tables and GF180's rule tables are indexed; GF180's
non-coded rule references are also included. Other tables, including antenna
matrices, packaging constraints, and process metadata, remain available as
raw CSV records in `tables`. The aggregate SKY130 periphery CSV is retained
without indexing it again.

Conditional values, duplicate identifiers, continuation rows, flags, and
voltage columns are preserved. Only entire scalar cells receive numeric
values; units are normalized only when explicit, never guessed. All process
options are retained without variant filtering. These documentation catalogs
are not executable DRC decks and cannot be passed directly to `gds-migrate`
as technology configurations.

### Simplified GF180 distance catalog

Run `python -m gds_migrator.technology.gf180_catalog` from the repository root
to generate `drc/gf180_simplified.json` and
`drc/gf180_simplified_report.json`. Use `--drc-root`, `--output`, and `--report`
to override paths. The exporter uses only the Python standard library.

The first file is an array with `id`, `desc`, `layer1`, `layer2`, `type`,
`value`, `context`, and `source`. Values are in **micrometers**. The CSV
manual is authoritative; KLayout and Magic references are navigation aids,
not claims that implementations or values are equivalent. Magic raw DRC
units are not converted. Physical layer names are lower-case names validated
against `gf180mcu.lyp`; the report includes their GDS layer/datatype pairs.

- `width`: a same-layer dimension. `context.measurement` identifies lengths
  and square sides where applicable.
- `spacing`: distance between the selected edges. Maximum tap distances
  retain `context.bound = "maximum"`; they are not minimum clearances.
- `overlap`: layer1 overlaps or extends beyond layer2, preserving the
  manual's terminology, even when KLayout implements it using `enclosed`.
- `enclosure`: layer1 contains layer2. Slot-to-metal internal margins
  (`MSLOT.5`) are classified as enclosure rather than external spacing.

Each voltage, conditional threshold, metal level, and applicable layer pair
gets a separate entry; **IDs are intentionally not unique**. `Mn` becomes
metal1 through metal5, and vias are expanded through via5. `metaltop` is a
separate physical mask, not a synonym for metal5. Upper-via and top-metal
relations retain their metal-stack conditions. MIM A/B, thick top metal,
SRAM, OTP, NEO-EE, and YMTP options must not be applied simultaneously.

Context preserves minimum/maximum/exact/recommended bounds, voltage columns,
subcases, parent descriptions, and device regions. NCOMP/PCOMP and gate
qualifiers remain in the descriptions/context: mapping them to their
physical `comp` or `poly2` mask does **not** remove their implant or region
selection. Likewise, internal boundaries and contact-array selections are
not ordinary unrestricted layer-to-layer spacing.

The report accounts for every row in the identified rule tables, records
excluded rows and supporting tables, and hashes the inputs. Areas, densities,
counts, pitches, symbolic expressions, qualitative prohibitions, and
unidentified frame/scribe-line boundaries are not converted into invented
distances. Explicit dummy-poly dimensions and the individual-top-via
guideline are read from their descriptions. The missing CSV ID `MSLOT.9`
is recovered from KLayout and flagged, without relabeling it `MSLOT.8`.
Unknown numeric formats or unmapped dimensional rules abort generation.

This is a **documented distance catalog, not an executable DRC deck**.
Conditions remain descriptive, not a Boolean expression language. Follow
the linked manual notes and actual deck for device selection and exceptions.
`TechnologyRules.from_json` can load this catalog when `gf180mcu.lyp` is
available beside it, or when its path is supplied as the second argument.
The loader converts micrometers to nanometers, uses the LYP layer/datatype
mapping, and keeps only minimum `width` and `spacing` rules. Multiple
conditional minima are conservatively collapsed to the strictest value for
each GDS layer/datatype identity; conditions, `overlap`, and `enclosure`
rules are not modeled by `TechnologyRules`.

```python
from pathlib import Path
from gds_migrator.technology.rules import TechnologyRules

technology = TechnologyRules.from_json(Path("drc/gf180_simplified.json"))
assert technology.layers["metal1"] == (34, 0)
```

### Compact Magic rule catalogs

Run `python src_new/extract_magic_rules.py` to generate
`drc/sky130_magic_compact.json` and `drc/gf180_magic_compact.json`.
The script reads the two local `libs.tech/magic/*.tech` files, without
changing the CSV catalogs. `--drc-root` and `--output-dir` override locations.

`single_layer` groups width, area, and same-expression spacing rules by
their exact Magic layer expression. `between_layers` groups spacing and
surround rules by an ordered pair of expressions. For surround, the second
expression must enclose the first. Aliases, lists, contact types, and plane
qualifiers are preserved, not expanded or mapped to GDS layers. A group
may therefore describe a set of Magic types rather than one physical mask.

Rules retain numeric arguments, options (including directional enclosure
and corner exemptions), variants, style, source line, and the reference
from the final parenthesized part of the message. Messages are omitted and
JSON whitespace is minimized. Repeated rules are not merged.

Values are **raw DRC units**, with squared units for area. The DRC
`scalefactor` defines DRC units per internal Magic unit, optionally as
`scale / reducer`; it is not a nanometer multiplier. Physical dimensions
depend on the active CIF output style and internal-grid rounding. No
physical-unit conversion is guessed by this extractor.

Only explicit `width`, `spacing`, `surround`, and `area` checks are exported.
Other commands, including CIF checks, are reported in `omitted_commands`.
All variants are retained: `*` means every variant in the enclosing style.
These partial catalogs are not replacements for running the full DRC deck.

#### Loading compact catalogs with TechnologyRules

Run `python src_new/extract_magic_rules.py --technology-rules` to regenerate
the same compact JSON files with `name`, `grid`, `min_width`, and
`min_spacing` fields accepted by `TechnologyRules.from_json`. Without this
option the script exports the original raw catalog only.

```python
from pathlib import Path
from gds_migrator.technology.rules import TechnologyRules

technology = TechnologyRules.from_json(Path("drc/sky130_magic_compact.json"))
assert technology.min_width_for(68) == 140
```

The converter projects reviewed ordinary well and metal baseline rules from
the Magic `drc/full` profile onto drawing masks. GDS mappings, physical
scaling, and the 5 nm grid are checked against the source `gdsii` CIF output
style. Compound expressions are not expanded into arbitrary layer pairs.
The loader fields are in nanometers; the original catalog values remain
in raw DRC units. `conversion.records` identifies applied and unapplied
source rules by line, with reasons. Other catalog fields are ignored by
`TechnologyRules`.

This is intentionally a partial configuration: area, enclosure, conditional
spacing, and fill/obstruction interactions are not applied. Because
`TechnologyRules` ignores datatypes, use the constraints only for the drawing
masks listed in `conversion.drawing_layers`, not every GDS purpose.
SKY130 NWELL (64/20) and DNWELL (64/18) are both excluded to avoid merging
distinct masks into layer 64. No distinct-layer spacing is guessed.
GF180 uses the actual three-metal variant A, including its thick top metal.
The existing minimal SKY130 loader is not changed by this conversion.
