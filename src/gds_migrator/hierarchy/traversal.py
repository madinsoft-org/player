"""Bottom-up ordering of a cell hierarchy (children processed before parents)."""

from __future__ import annotations

from typing import Mapping

from gds_migrator.hierarchy.model import HierarchyNode


def topological_order(cells: Mapping[str, HierarchyNode]) -> list[str]:
    """Return cell names ordered so that every child precedes its parents.

    Raises:
        ValueError: If the hierarchy contains a cycle, or an instance
            references an unknown cell.
    """
    order: list[str] = []
    visited: set[str] = set()
    visiting: set[str] = set()

    def visit(name: str) -> None:
        if name in visited:
            return
        if name in visiting:
            raise ValueError(f"cycle detected in cell hierarchy at {name!r}")
        if name not in cells:
            raise ValueError(f"unknown cell referenced: {name!r}")
        visiting.add(name)
        for instance in cells[name].instances:
            visit(instance.cell_name)
        visiting.discard(name)
        visited.add(name)
        order.append(name)

    for name in cells:
        visit(name)
    return order
