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
_LYP_LAYER_SOURCE = re.compile(r"(.+?)\s+(\d+)/(\d+)@\d+")


def read_klayout_layer_map(path: Path) -> dict[str, tuple[int, int]]:
    """Read named GDS layer/datatype pairs from a KLayout layer-properties file."""
    if path.suffix.lower() != ".lyp":
        raise ValueError(f"Expected a KLayout .lyp layer-properties file, got {path}")
    layers: dict[str, tuple[int, int]] = {}
    for source in ET.parse(path).iter("source"):
        match = _LYP_LAYER_SOURCE.fullmatch(source.text or "")
        if match is not None:
            name, layer, datatype = match.groups()
            layers[name.lower()] = (int(layer), int(datatype))
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
        layers: Optional mapping from lower-case layer names to
            ``(GDS layer, datatype)`` pairs.
    """

    name: str
    grid: int
    min_width: dict[LayerIdentity, int] = field(default_factory=dict)
    min_spacing: dict[SpacingKey, int] = field(default_factory=dict)
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
            properties_path = layer_properties_path or path.with_name("gf180mcu.lyp")
            layers = read_klayout_layer_map(properties_path)
            return cls._from_gf180_simplified(data, layers)
        if not isinstance(data, dict):
            raise ValueError("Technology JSON must contain an object or a GF180 rule list.")

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
    def _from_gf180_simplified(
        cls,
        data: list[dict[str, object]],
        layers: dict[str, tuple[int, int]],
    ) -> "TechnologyRules":
        min_width: dict[LayerIdentity, int] = {}
        min_spacing: dict[SpacingKey, int] = {}
        for index, entry in enumerate(data):
            if not isinstance(entry, dict):
                raise ValueError(f"GF180 rule {index} must be an object")
            if (
                entry.get("type") not in {"width", "spacing"}
            ):
                continue
            context = entry.get("context")
            if not isinstance(context, dict):
                raise ValueError(f"GF180 rule {index} must have an object 'context'")
            if context.get("bound") != "minimum":
                continue
            if context.get("unit") != "um":
                raise ValueError(f"GF180 rule {index} must express values in micrometers")

            layer1_value = entry.get("layer1")
            if not isinstance(layer1_value, str):
                raise ValueError(f"GF180 rule {index} must have a string 'layer1'")
            layer1_name = layer1_value.lower()
            try:
                layer1 = layers[layer1_name]
            except KeyError as exc:
                raise ValueError(f"Unknown GF180 layer name {layer1_name!r}") from exc
            raw_value = entry.get("value")
            if (
                isinstance(raw_value, bool)
                or not isinstance(raw_value, (int, float))
                or not math.isfinite(raw_value)
                or raw_value < 0
            ):
                raise ValueError(f"GF180 rule {index} must have a non-negative numeric value")
            value = round(raw_value * GF180_NM_PER_UM)
            rule_type = entry["type"]
            if rule_type == "width":
                min_width[layer1] = max(min_width.get(layer1, 0), value)
                continue

            layer2_value = entry.get("layer2")
            if not isinstance(layer2_value, str):
                raise ValueError(f"GF180 spacing rule {index} must have a string 'layer2'")
            layer2_name = layer2_value.lower()
            try:
                layer2 = layers[layer2_name]
            except KeyError as exc:
                raise ValueError(f"Unknown GF180 layer name {layer2_name!r}") from exc
            key = cls._spacing_key(layer1, layer2)
            min_spacing[key] = max(min_spacing.get(key, 0), value)

        if not min_width and not min_spacing:
            raise ValueError("GF180 rule catalog contains no minimum width or spacing rules")

        return cls(
            name="gf180",
            grid=GF180_GRID_NM,
            min_width=min_width,
            min_spacing=min_spacing,
            layers=layers,
        )
