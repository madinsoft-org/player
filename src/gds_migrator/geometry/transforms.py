"""2D affine transforms matching GDSII ``SREF``/``AREF`` placement semantics.

GDSII applies transforms to a referenced cell in this fixed order:
mirror across X axis (if ``mirror_x``) -> scale by ``magnification`` ->
rotate by ``rotation`` degrees (counter-clockwise) -> translate by
``origin``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Point:
    """An integer-grid 2D point expressed in database units."""

    x: float
    y: float

    def __add__(self, other: "Point") -> "Point":
        return Point(self.x + other.x, self.y + other.y)

    def __sub__(self, other: "Point") -> "Point":
        return Point(self.x - other.x, self.y - other.y)


@dataclass(frozen=True)
class Transform:
    """An affine placement transform, as used by GDSII instances.

    Attributes:
        origin: Translation applied last.
        rotation: Rotation in degrees, applied counter-clockwise, before translation.
        mirror_x: If True, mirror across the X axis before rotation/scaling.
        magnification: Uniform scale factor, applied after mirroring.
    """

    origin: Point = Point(0.0, 0.0)
    rotation: float = 0.0
    mirror_x: bool = False
    magnification: float = 1.0

    def apply(self, point: Point) -> Point:
        """Map a point from the referenced cell's local frame to the parent frame."""
        x, y = point.x, point.y
        if self.mirror_x:
            y = -y
        x *= self.magnification
        y *= self.magnification
        angle = math.radians(self.rotation)
        cos_a, sin_a = math.cos(angle), math.sin(angle)
        x, y = x * cos_a - y * sin_a, x * sin_a + y * cos_a
        return Point(x + self.origin.x, y + self.origin.y)

    def compose(self, inner: "Transform") -> "Transform":
        """Return the transform equivalent to applying ``inner`` then ``self``.

        Useful when flattening a hierarchy: if a grandchild is placed by
        ``inner`` inside a child, and the child is placed by ``self`` inside
        the parent, ``self.compose(inner)`` maps grandchild-local
        coordinates directly to the parent frame.
        """
        mirror_x = self.mirror_x != inner.mirror_x
        magnification = self.magnification * inner.magnification
        rotation = (self.rotation + (-inner.rotation if self.mirror_x else inner.rotation)) % 360
        origin = self.apply(inner.origin)
        return Transform(origin=origin, rotation=rotation, mirror_x=mirror_x, magnification=magnification)
