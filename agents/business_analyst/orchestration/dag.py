"""Deterministic dependency-wave grouping for the Business Analyst planner."""

from dataclasses import dataclass, field
from typing import Any, List


@dataclass
class WaveNode:
    id: str
    depends_on: List[str] = field(default_factory=list)
    payload: Any = None


def group_into_waves(nodes: List[WaveNode]) -> List[List[WaveNode]]:
    """Topologically group nodes into stable, dependency-ordered parallel waves."""
    by_id = {node.id: node for node in nodes}
    if len(by_id) != len(nodes):
        raise ValueError("DAG contains duplicate node ids")

    unresolved = {node.id: set(node.depends_on) for node in nodes}
    unknown = {
        dependency
        for dependencies in unresolved.values()
        for dependency in dependencies
        if dependency not in by_id
    }
    if unknown:
        raise ValueError(f"DAG contains unknown dependencies: {sorted(unknown)}")

    waves: List[List[WaveNode]] = []
    while unresolved:
        ready_ids = sorted(node_id for node_id, dependencies in unresolved.items() if not dependencies)
        if not ready_ids:
            raise ValueError("Dependency cycle detected while grouping execution waves")

        waves.append([by_id[node_id] for node_id in ready_ids])
        ready = set(ready_ids)
        unresolved = {
            node_id: dependencies - ready
            for node_id, dependencies in unresolved.items()
            if node_id not in ready
        }

    return waves
