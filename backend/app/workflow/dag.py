"""DAG core: DagNode, Pipeline, DagContext, topological sort."""

from __future__ import annotations

from typing import Any, Dict, List
from dataclasses import dataclass, field
import uuid


class DagNode:
    """A node in the workflow DAG with upstream/downstream edges."""

    def __init__(self, name: str):
        self.name = name
        self.upstream: List[DagNode] = []
        self.downstream: List[DagNode] = []

    def set_downstream(self, node: DagNode) -> DagNode:
        self.downstream.append(node)
        node.upstream.append(self)
        return node

    def __rshift__(self, other: DagNode) -> DagNode:
        return self.set_downstream(other)

    def __rrshift__(self, other: DagNode) -> DagNode:
        other.downstream.append(self)
        self.upstream.append(other)
        return self

    def __repr__(self):
        return f"<{type(self).__name__} '{self.name}'>"


@dataclass
class DagContext:
    """Per-run shared state: node outputs, share_data, run_id."""

    pipeline_name: str
    run_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    node_outputs: Dict[str, Any] = field(default_factory=dict)
    share_data: Dict[str, Any] = field(default_factory=dict)

    def get_output(self, node_name: str) -> Any:
        return self.node_outputs.get(node_name)

    def set_output(self, node_name: str, value: Any) -> None:
        self.node_outputs[node_name] = value


class Pipeline:
    """Context manager for building and running a directed acyclic graph."""

    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description
        self.nodes: Dict[str, DagNode] = {}
        self._active = False

    def add_node(self, node: DagNode) -> DagNode:
        self.nodes[node.name] = node
        return node

    def topological_order(self) -> List[DagNode]:
        """BFS topological sort. Raises RuntimeError on cycle."""
        in_degree = {n.name: len(n.upstream) for n in self.nodes.values()}
        queue = [n for n in self.nodes.values() if in_degree[n.name] == 0]
        order = []
        while queue:
            node = queue.pop(0)
            order.append(node)
            for child in node.downstream:
                in_degree[child.name] -= 1
                if in_degree[child.name] == 0:
                    queue.append(child)
        if len(order) != len(self.nodes):
            raise RuntimeError(f"DAG cycle in pipeline '{self.name}'")
        return order

    def __enter__(self) -> Pipeline:
        self._active = True
        return self

    def __exit__(self, *args) -> None:
        self._active = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "nodes": {
                name: {
                    "type": type(n).__name__,
                    "upstream": [u.name for u in n.upstream],
                    "downstream": [d.name for d in n.downstream],
                }
                for name, n in self.nodes.items()
            },
        }
