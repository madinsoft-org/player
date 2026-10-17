"""Design-rule model for a target (or source) process technology.

Rules are intentionally kept minimal: the pieces needed to derive spacing
constraints during the plane-sweep pass (see
:mod:`gds_migrator.constraints.plane_sweep`). Real rule decks (e.g. the
Skywater PDK, see :mod:`gds_migrator.technology.skywater`) are much richer;
extend :class:`TechnologyRules` as new rule types are needed rather than
modeling every DRC rule upfront (YAGNI).
"""


from __future__ import annotations


import json
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

LayerIdentity = int | tuple[int, int]
SpacingKey = tuple[LayerIdentity, LayerIdentity]
GF180_GRID_NM = 5
GF180_NM_PER_UM = 1000
_LYP_LAYER_SOURCE = re.compile(r"(?:(?P<name>.+?)\s+)?(?P<layer>\d+)/(?P<datatype>\d+)@\d+")



def read_klayout_layer_map(path: Path) -> dict[str, tuple[int, int]]:
    """Read named GDS layer/datatype pairs from a KLayout layer-properties file."""

    if path.suffix.lower() != ".lyp":
        raise ValueError(f"Expected a KLayout .lyp layer-properties file, got {path}")
    layers: dict[str, tuple[int, int]] = {}
    for properties in ET.parse(path).iter("properties"):
        source = properties.findtext("source")
        match = _LYP_LAYER_SOURCE.fullmatch(source or "")
        if match is not None:
            name = (match.group("name") or properties.findtext("name") or "").strip()
            if name:
                layers[name.lower()] = (
                    int(match.group("layer")),
                    int(match.group("datatype")),
                )
    if not layers:
        raise ValueError(f"No named GDS layers found in {path}")
    return layers


@dataclass(frozen=True)
class DesignRule:
    """A single scalar design rule expressed in database units (nm)."""

    name: str
    value: int

    def __post_init__(self) -> None:
        if self.value < 0:
            raise ValueError(f"design rule '{self.name}' must be non-negative, got {self.value}")


@dataclass
class TechnologyRules:
    """Per-layer design rules for a process technology.

    Attributes:
        name: Human-readable technology name (e.g. "sky130").
        grid: Manufacturing grid in database units; all migrated coordinates
            must be an integer multiple of this value.
        min_width: Minimum shape width per GDS layer, or layer/datatype pair.
        min_spacing: Minimum spacing keyed by an unordered pair of GDS layer
            identities.
        min_extension: Minimum extension rules keyed by an ordered pair of GDS layer
            identities (layer1 must extend layer2 by at least this amount).
        min_overlap: Minimum margins keyed by an ordered pair of GDS layer
            identities (layer1 must overlap beyond layer2 on same-facing edges).
        rule_id_width: Mapping from layer identity to rule ID for width rules.
        rule_id_spacing: Mapping from spacing key to rule ID for spacing rules.
        rule_id_extension: Mapping from extension key to rule ID for extension rules.
        rule_id_overlap: Mapping from overlap key to rule ID for overlap rules.
        layers: Optional mapping from lower-case layer names to
            ``(GDS layer, datatype)`` pairs.
    """

    name: str
    grid: int
    min_width: dict[LayerIdentity, int] = field(default_factory=dict)
    min_spacing: dict[SpacingKey, int] = field(default_factory=dict)
    min_extension: dict[SpacingKey, int] = field(default_factory=dict)
    min_overlap: dict[SpacingKey, int] = field(default_factory=dict)
    rule_id_width: dict[LayerIdentity, str] = field(default_factory=dict)
    rule_id_spacing: dict[SpacingKey, str] = field(default_factory=dict)
    rule_id_extension: dict[SpacingKey, str] = field(default_factory=dict)
    rule_id_overlap: dict[SpacingKey, str] = field(default_factory=dict)
    layers: dict[str, tuple[int, int]] = field(default_factory=dict)

    @staticmethod
    def _spacing_key(layer_a: LayerIdentity, layer_b: LayerIdentity) -> SpacingKey:
        def order(identity: LayerIdentity) -> tuple[int, int, int]:
            if isinstance(identity, tuple):
                return (identity[0], identity[1], 1)
            return (identity, -1, 0)

        return (layer_a, layer_b) if order(layer_a) <= order(layer_b) else (layer_b, layer_a)

    def min_spacing_for(
        self,
        layer_a: int,
        layer_b: int,
        datatype_a: int | None = None,
        datatype_b: int | None = None,
    ) -> int:
        """Return the minimum spacing rule between two layers, or 0 if unset."""
        if datatype_a is not None and datatype_b is not None:
            pair = self._spacing_key((layer_a, datatype_a), (layer_b, datatype_b))
            if pair in self.min_spacing:
                return self.min_spacing[pair]
        return self.min_spacing.get(self._spacing_key(layer_a, layer_b), 0)

    def min_width_for(self, layer: int, datatype: int | None = None) -> int:
        """Return the minimum width rule for a layer, or 0 if unset."""
        if datatype is not None:
            identity = (layer, datatype)
            if identity in self.min_width:
                return self.min_width[identity]
        return self.min_width.get(layer, 0)

    def min_extension_for(
        self,
        layer_a: int,
        layer_b: int,
        datatype_a: int | None = None,
        datatype_b: int | None = None,
    ) -> int:
        """Return the minimum extension rule (layer_a extends layer_b), or 0 if unset.
        
        Extension rules are directional: layer_a must extend beyond layer_b by at least
        the returned value.
        """
        if datatype_a is not None and datatype_b is not None:
            key = ((layer_a, datatype_a), (layer_b, datatype_b))
            if key in self.min_extension:
                return self.min_extension[key]
        
        key_with_dt = ((layer_a, 0), (layer_b, 0))
        if key_with_dt in self.min_extension:
            return self.min_extension[key_with_dt]
        
        return 0

    def min_overlap_for(
        self,
        layer_a: int,
        layer_b: int,
        datatype_a: int | None = None,
        datatype_b: int | None = None,
    ) -> int:
        """Return the minimum margin of layer_a beyond layer_b, or 0 if unset."""
        if datatype_a is not None and datatype_b is not None:
            pair = ((layer_a, datatype_a), (layer_b, datatype_b))
            if pair in self.min_overlap:
                return self.min_overlap[pair]
        if datatype_a in (None, 0) and datatype_b in (None, 0):
            pair_with_dt = ((layer_a, 0), (layer_b, 0))
            if pair_with_dt in self.min_overlap:
                return self.min_overlap[pair_with_dt]
        return self.min_overlap.get((layer_a, layer_b), 0)

    def rule_id_for_width(self, layer: int, datatype: int | None = None) -> str | None:
        """Return the rule ID for the width rule of a layer, or None if unset."""
        if datatype is not None:
            identity = (layer, datatype)
            if identity in self.rule_id_width:
                return self.rule_id_width[identity]
        return self.rule_id_width.get(layer, None)

    def rule_id_for_spacing(
        self,
        layer_a: int,
        layer_b: int,
        datatype_a: int | None = None,
        datatype_b: int | None = None,
    ) -> str | None:
        """Return the rule ID for the spacing rule between two layers, or None if unset."""
        if datatype_a is not None and datatype_b is not None:
            pair = self._spacing_key((layer_a, datatype_a), (layer_b, datatype_b))
            if pair in self.rule_id_spacing:
                return self.rule_id_spacing[pair]
        return self.rule_id_spacing.get(self._spacing_key(layer_a, layer_b), None)

    def rule_id_for_overlap(
        self,
        layer_a: int,
        layer_b: int,
        datatype_a: int | None = None,
        datatype_b: int | None = None,
    ) -> str | None:
        """Return the ID for the directional overlap rule, or None if unset."""
        if datatype_a is not None and datatype_b is not None:
            pair = ((layer_a, datatype_a), (layer_b, datatype_b))
            if pair in self.rule_id_overlap:
                return self.rule_id_overlap[pair]
        if datatype_a in (None, 0) and datatype_b in (None, 0):
            pair_with_dt = ((layer_a, 0), (layer_b, 0))
            if pair_with_dt in self.rule_id_overlap:
                return self.rule_id_overlap[pair_with_dt]
        return self.rule_id_overlap.get((layer_a, layer_b))

    def rule_id_for_extension(
        self,
        layer_a: int,
        layer_b: int,
        datatype_a: int | None = None,
        datatype_b: int | None = None,
    ) -> str | None:
        """Return the rule ID for the extension rule (layer_a extends layer_b), or None if unset."""
        if datatype_a is not None and datatype_b is not None:
            key = ((layer_a, datatype_a), (layer_b, datatype_b))
            if key in self.rule_id_extension:
                return self.rule_id_extension[key]
        
        key_with_dt = ((layer_a, 0), (layer_b, 0))
        if key_with_dt in self.rule_id_extension:
            return self.rule_id_extension[key_with_dt]
        
        return None

    def snap_to_grid(self, value: int) -> int:
        """Round ``value`` to the nearest multiple of the manufacturing grid."""
        if self.grid <= 0:
            return value
        return round(value / self.grid) * self.grid

    @classmethod
    def from_json(
        cls,
        path: Path,
        layer_properties_path: Path | None = None,
    ) -> "TechnologyRules":
        """Load technology rules from a JSON file.

        Supports the numeric technology schema and the GF180 simplified rule
        catalog. For that catalog, the adjacent ``gf180mcu.lyp`` file supplies
        the named GDS layer/datatype mapping; pass ``layer_properties_path``
        when it is stored elsewhere.

        Numeric schema::

            {
              "name": "sky130",
              "grid": 5,
              "min_width": {"67": 150},
              "min_spacing": [{"layers": [67, 67], "value": 210}]
            }
        """
        path = Path(path)
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            if layer_properties_path is None:
                parent_dir = path.parent
                data_dir = path.parent / "data"
                root_dir = path.parent.parent
                stem = path.stem
                
                possible_lyp_files = []
                for base_name in [
                    stem,
                    stem.replace("_gemini", "").replace("_simplified", ""),
                    "gf180mcu",
                    "scmos_180nm",
                    "sky130A",
                ]:
                    possible_lyp_files.extend([
                        parent_dir / (base_name + ".lyp"),
                        data_dir / (base_name + ".lyp"),
                        root_dir / "data" / (base_name + ".lyp"),
                    ])
                
                for candidate in possible_lyp_files:
                    if candidate.exists():
                        layer_properties_path = candidate
                        break
                if layer_properties_path is None:
                    raise ValueError(
                        f"No .lyp layer properties file found for {path}. "
                        f"Please provide layer_properties_path argument."
                    )
            layers = read_klayout_layer_map(layer_properties_path)
            
            layer_name_mapping = {}
            if "scmos" in path.stem.lower():
                layer_name_mapping = {
                    "well": "nwell",
                    "active": "diffusion",
                    "n_well": "nwell",
                }
            
            return cls._from_rule_catalog(
                data,
                layers,
                technology_name=path.stem,
                layer_name_mapping=layer_name_mapping,
            )
        if not isinstance(data, dict):
            raise ValueError("Technology JSON must contain an object or a rule list.")

        min_width = {int(layer): int(value) for layer, value in data.get("min_width", {}).items()}
        min_spacing: dict[SpacingKey, int] = {}
        for entry in data.get("min_spacing", []):
            layer_a, layer_b = entry["layers"]
            min_spacing[cls._spacing_key(int(layer_a), int(layer_b))] = int(entry["value"])
        return cls(
            name=data["name"],
            grid=int(data["grid"]),
            min_width=min_width,
            min_spacing=min_spacing,
        )

    @classmethod
    def _from_rule_catalog(
        cls,
        data: list[dict[str, object]],
        layers: dict[str, tuple[int, int]],
        *,
        technology_name: str = "unknown",
        grid_nm: int = GF180_GRID_NM,
        layer_properties_path: Path | None = None,
        default_unit: str = "um",
        unit_to_nm: dict[str, int] | None = None,
        default_bound: str = "minimum",
        layer_name_mapping: dict[str, str] | None = None,
    ) -> "TechnologyRules":
        """Load technology rules from a rule catalog JSON list.
        
        This is a generic function that can parse various rule catalog formats,
        including GF180 simplified and SCMOS formats.
        
        Args:
            data: List of rule entries from JSON
            layers: Mapping from layer names to (GDS layer, datatype) tuples
            technology_name: Name for the technology (default: "unknown")
            grid_nm: Manufacturing grid in nanometers (default: GF180_GRID_NM)
            layer_properties_path: Optional path to .lyp file for layer mapping
            default_unit: Default unit if not specified in context (default: "um")
            unit_to_nm: Conversion factors from units to nanometers
            default_bound: Default bound type to filter (default: "minimum")
            layer_name_mapping: Optional mapping from JSON layer names to .lyp layer names
        """
        if unit_to_nm is None:
            unit_to_nm = {"um": GF180_NM_PER_UM, "nm": 1, "micron": GF180_NM_PER_UM, "microns": GF180_NM_PER_UM}
        
        if layer_name_mapping is None:
            layer_name_mapping = {}
        
        min_width: dict[LayerIdentity, int] = {}
        min_spacing: dict[SpacingKey, int] = {}
        min_extension: dict[SpacingKey, int] = {}
        min_overlap: dict[SpacingKey, int] = {}
        rule_id_width: dict[LayerIdentity, str] = {}
        rule_id_spacing: dict[SpacingKey, str] = {}
        rule_id_extension: dict[SpacingKey, str] = {}
        rule_id_overlap: dict[SpacingKey, str] = {}
        for index, entry in enumerate(data):
            if not isinstance(entry, dict):
                raise ValueError(f"Rule {index} must be an object")
            rule_type = entry.get("type")
            if rule_type not in {"width", "spacing", "extension", "overlap"}:
                continue
            
            context = entry.get("context")
            unit = default_unit
            bound = default_bound
            
            if context is not None and isinstance(context, dict):
                if context.get("bound") != default_bound:
                    continue
                unit = context.get("unit", default_unit)
            
            layer1_value = entry.get("layer1")
            if not isinstance(layer1_value, str):
                raise ValueError(f"Rule {index} must have a string 'layer1'")
            layer1_name = layer1_value.lower()
            mapped_layer1_name = layer_name_mapping.get(layer1_name, layer1_name)
            if mapped_layer1_name not in layers:
                continue
            layer1 = layers[mapped_layer1_name]
            
            raw_value = entry.get("value")
            if (
                isinstance(raw_value, bool)
                or not isinstance(raw_value, (int, float))
                or not math.isfinite(raw_value)
                or raw_value < 0
            ):
                raise ValueError(f"Rule {index} must have a non-negative numeric value")
            
            conversion_factor = unit_to_nm.get(unit, 1)
            value = round(raw_value * conversion_factor)
            
            rule_id = str(entry.get("id", ""))
            
            if rule_type == "width":
                min_width[layer1] = max(min_width.get(layer1, 0), value)
                rule_id_width[layer1] = rule_id or rule_id_width.get(layer1, "")
                continue

            layer2_value = entry.get("layer2")
            if not isinstance(layer2_value, str):
                raise ValueError(f"Rule {index} ({rule_type}) must have a string 'layer2'")
            layer2_name = layer2_value.lower()
            mapped_layer2_name = layer_name_mapping.get(layer2_name, layer2_name)
            if mapped_layer2_name not in layers:
                continue
            layer2 = layers[mapped_layer2_name]
            
            if rule_type == "spacing":
                key = cls._spacing_key(layer1, layer2)
                min_spacing[key] = max(min_spacing.get(key, 0), value)
                rule_id_spacing[key] = rule_id or rule_id_spacing.get(key, "")
            elif rule_type == "extension":
                key = (layer1, layer2)
                min_extension[key] = max(min_extension.get(key, 0), value)
                rule_id_extension[key] = rule_id or rule_id_extension.get(key, "")
            elif rule_type == "overlap":
                key = (layer1, layer2)
                if key not in min_overlap or value >= min_overlap[key]:
                    min_overlap[key] = value
                    rule_id_overlap[key] = rule_id

        if not min_width and not min_spacing and not min_extension and not min_overlap:
            raise ValueError("Rule catalog contains no width, spacing, extension, or overlap rules")

        return cls(
            name=technology_name,
            grid=grid_nm,
            min_width=min_width,
            min_spacing=min_spacing,
            min_extension=min_extension,
            min_overlap=min_overlap,
            rule_id_width=rule_id_width,
            rule_id_spacing=rule_id_spacing,
            rule_id_extension=rule_id_extension,
            rule_id_overlap=rule_id_overlap,
            layers=layers,
        )

    @classmethod
    def _from_gf180_simplified(
        cls,
        data: list[dict[str, object]],
        layers: dict[str, tuple[int, int]],
    ) -> "TechnologyRules":
        """Parse GF180 simplified rule catalog (backwards compatibility)."""
        return cls._from_rule_catalog(
            data,
            layers,
            technology_name="gf180",
            grid_nm=GF180_GRID_NM,
            default_unit="um",
            unit_to_nm={"um": GF180_NM_PER_UM},
            default_bound="minimum",
        )
