"""Reference technology loader for the Skywater SKY130 open PDK.

The full rule deck lives in https://github.com/google/skywater-pdk. This
module does not vendor or fetch that repository; it only documents the
expected JSON extraction format and provides a small, hand-picked subset of
SKY130 metal-1 rules (in nanometers) useful for prototyping and tests.
"""

from __future__ import annotations

from gds_migrator.technology.rules import TechnologyRules

#: GDSII layer numbers used by SKY130 for a handful of layers relevant to
#: cell abutment (metal1 drawing/pin). See the SKY130 layer map in
#: https://github.com/google/skywater-pdk for the authoritative source.
SKY130_LICON_LAYER = 66
SKY130_METAL1_LAYER = 67

#: Minimal SKY130-derived rule subset (values from the public SKY130 rule
#: deck, in nanometers on a 5 nm grid). Extend as more layers are required.
SKY130_MINIMAL = TechnologyRules(
    name="sky130",
    grid=5,
    min_width={
        SKY130_METAL1_LAYER: 140,
    },
    min_spacing={
        (SKY130_METAL1_LAYER, SKY130_METAL1_LAYER): 140,
        (SKY130_LICON_LAYER, SKY130_LICON_LAYER): 170,
    },
)


def load_sky130_minimal() -> TechnologyRules:
    """Return the built-in minimal SKY130 rule subset."""
    return SKY130_MINIMAL
