"""A directed weighted constraint graph solved as a longest-path problem.

Each edge ``(upstream, downstream, weight)`` encodes the inequality
``pos[downstream] - pos[upstream] >= weight``. Solving with Bellman-Ford
relaxation (instead of restricting to a DAG) yields, for every node, the
smallest coordinate that is both feasible and no smaller than a supplied
initial position -- the standard 1D "compaction" trick used to migrate a
layout with a minimal rightward-only perturbation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any


class InfeasibleConstraintsError(Exception):
    """Raised when the constraint graph contains a positive-weight cycle."""


@dataclass
class ConstraintGraph:
    """A 1D constraint graph for a single axis (X or Y)."""

    nodes: set[str] = field(default_factory=set)
    edges: list[tuple[str, str, float]] = field(default_factory=list)
    edge_ids: list[str] = field(default_factory=list)
    edge_metadata: list[dict[str, Any]] = field(default_factory=list)

    def add_node(self, node: str) -> None:
        self.nodes.add(node)

    def add_constraint(
        self,
        upstream: str,
        downstream: str,
        min_distance: float,
        *,
        constraint_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record ``pos[downstream] - pos[upstream] >= min_distance``."""
        self.add_node(upstream)
        self.add_node(downstream)
        if constraint_id is None:
            identity = json.dumps(
                [upstream, downstream, min_distance, len(self.edges)],
                separators=(",", ":"),
            )
            constraint_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:20]
        self.edges.append((upstream, downstream, min_distance))
        self.edge_ids.append(constraint_id)
        self.edge_metadata.append(metadata or {})

    def solve(
        self,
        initial_positions: dict[str, float],
        excluded_constraint_ids: set[str] | None = None,
    ) -> dict[str, float]:
        """Return the minimal feasible coordinates satisfying every constraint.

        Args:
            initial_positions: A lower bound per node, typically its
                original (pre-migration) coordinate.
            excluded_constraint_ids: Optional constraint IDs to omit for
                this solve without mutating the graph.

        Raises:
            ValueError: If a node has no initial position.
            InfeasibleConstraintsError: If the constraints contain a
                positive-weight cycle (no feasible assignment exists).
        """
        missing = self.nodes - initial_positions.keys()
        if missing:
            raise ValueError(f"missing initial position for nodes: {sorted(missing)}")

        dist: dict[str, float] = dict(initial_positions)
        excluded = excluded_constraint_ids or set()
        active_edges = [
            edge
            for edge, constraint_id in zip(self.edges, self.edge_ids)
            if constraint_id not in excluded
        ]
        for _ in range(max(len(self.nodes) - 1, 0)):
            changed = False
            for upstream, downstream, weight in active_edges:
                candidate = dist[upstream] + weight
                if candidate > dist[downstream]:
                    dist[downstream] = candidate
                    changed = True
            if not changed:
                break

        for upstream, downstream, weight in active_edges:
            if dist[upstream] + weight > dist[downstream] + 1e-9:
                raise InfeasibleConstraintsError(
                    f"positive-weight cycle detected through edge {upstream!r} -> {downstream!r}"
                )
        return dist
