"""Graph data model: nodes, edges, topological validation.

A graph is a JSON-serializable DAG specification:

    {"nodes": [
       {"id": "a_t", "op": "transpose", "inputs": ["a"],
        "params": {"axes": [1, 0]}},
       {"id": "r", "op": "reshape", "inputs": ["a_t"],
        "params": {"shape": [6], "order": "C"}},
    ],
     "outputs": ["r"],
     "inputs": ["a"]}

An entry in ``inputs`` resolves first against execution bindings (external
input names) and then against earlier node ids. Validation is strict:
duplicate ids, unknown references, cycles and bad arity all fail *before*
any tensor is touched, each with its own error category.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Sequence

from ..errors import GraphError


@dataclass(frozen=True)
class NodeSpec:
    id: str
    op: str
    inputs: tuple[str, ...]
    params: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "NodeSpec":
        if not isinstance(raw, dict):
            raise GraphError(f"node must be a mapping, got {type(raw).__name__}")
        node_id = raw.get("id")
        op = raw.get("op")
        if not isinstance(node_id, str) or not node_id:
            raise GraphError(f"node requires a non-empty string 'id', got {node_id!r}")
        if not isinstance(op, str) or not op:
            raise GraphError(f"node {node_id!r} requires a non-empty string 'op'")
        raw_inputs = raw.get("inputs", [])
        if not isinstance(raw_inputs, Sequence) or isinstance(raw_inputs, str):
            raise GraphError(f"node {node_id!r}: 'inputs' must be a list")
        inputs = tuple(raw_inputs)
        for ref in inputs:
            if not isinstance(ref, str) or not ref:
                raise GraphError(
                    f"node {node_id!r}: every input reference must be a "
                    f"non-empty string, got {ref!r}")
        params = raw.get("params", {})
        if params is None:
            params = {}
        if not isinstance(params, dict):
            raise GraphError(f"node {node_id!r}: 'params' must be a mapping")
        return cls(id=node_id, op=op, inputs=inputs, params=dict(params))

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "op": self.op,
                "inputs": list(self.inputs), "params": dict(self.params)}


@dataclass(frozen=True)
class Graph:
    nodes: tuple[NodeSpec, ...]
    outputs: tuple[str, ...]
    inputs: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Graph":
        if not isinstance(raw, dict):
            raise GraphError(f"graph must be a mapping, got {type(raw).__name__}")
        raw_nodes = raw.get("nodes")
        if not isinstance(raw_nodes, Sequence) or isinstance(raw_nodes, str):
            raise GraphError("graph requires a list 'nodes'")
        nodes = tuple(NodeSpec.from_dict(n) for n in raw_nodes)

        seen: set[str] = set()
        for node in nodes:
            if node.id in seen:
                raise GraphError(f"duplicate node id {node.id!r}")
            seen.add(node.id)

        raw_outputs = raw.get("outputs", [])
        if not isinstance(raw_outputs, Sequence) or isinstance(raw_outputs, str):
            raise GraphError("'outputs' must be a list of node ids")
        outputs = tuple(raw_outputs)

        raw_inputs = raw.get("inputs", [])
        if not isinstance(raw_inputs, Sequence) or isinstance(raw_inputs, str):
            raise GraphError("'inputs' must be a list of external input names")
        inputs = tuple(raw_inputs)
        for name in inputs:
            if not isinstance(name, str) or not name:
                raise GraphError(f"external input names must be non-empty strings")

        graph = cls(nodes=nodes, outputs=outputs, inputs=inputs)
        graph.validate_references()
        graph.validate_acyclic()
        return graph

    def node_map(self) -> dict[str, NodeSpec]:
        return {node.id: node for node in self.nodes}

    def validate_references(self) -> None:
        ids = {node.id for node in self.nodes}
        external = set(self.inputs)
        for node in self.nodes:
            for ref in node.inputs:
                if ref == node.id:
                    raise GraphError(
                        f"node {node.id!r} cannot reference itself",
                        details={"node": node.id})
                if ref not in ids and ref not in external:
                    raise GraphError(
                        f"node {node.id!r} references unknown input {ref!r}; "
                        "not a node id and not declared in graph inputs",
                        details={"node": node.id, "reference": ref})
        for out in self.outputs:
            if out not in ids and out not in external:
                raise GraphError(
                    f"graph output {out!r} is neither a node id nor an input")

    def validate_acyclic(self) -> None:
        """Kahn's algorithm; the leftover set marks a cycle."""
        ids = {node.id for node in self.nodes}
        indegree = {node.id: 0 for node in self.nodes}
        dependents: dict[str, list[str]] = {node.id: [] for node in self.nodes}
        for node in self.nodes:
            for ref in node.inputs:
                if ref in ids:
                    indegree[node.id] += 1
                    dependents[ref].append(node.id)
        ready = [nid for nid, deg in indegree.items() if deg == 0]
        processed: list[str] = []
        while ready:
            nid = ready.pop()
            processed.append(nid)
            for dep in dependents[nid]:
                indegree[dep] -= 1
                if indegree[dep] == 0:
                    ready.append(dep)
        if len(processed) != len(self.nodes):
            stuck = sorted(set(ids) - set(processed))
            raise GraphError(
                f"graph contains a cycle; nodes never schedulable: {stuck}",
                details={"cyclic_nodes": stuck})

    def topological_order(self, only_for: Sequence[str] | None = None
                          ) -> list[NodeSpec]:
        """Nodes in dependency-first order, optionally pruned to a target set."""
        by_id = self.node_map()
        ids = set(by_id)
        external = set(self.inputs)
        order: list[NodeSpec] = []
        placed: set[str] = set()

        def visit(nid: str, stack: tuple[str, ...]) -> None:
            if nid in placed or nid in external:
                return
            if nid in stack:
                raise GraphError(
                    f"cycle detected through {nid!r}",
                    details={"cycle_entry": nid})
            node = by_id[nid]
            next_stack = stack + (nid,)
            for ref in node.inputs:
                if ref in ids:
                    visit(ref, next_stack)
            placed.add(nid)
            order.append(node)

        targets = list(only_for) if only_for is not None else [n.id for n in self.nodes]
        for nid in targets:
            if nid in external:
                continue
            if nid not in by_id:
                raise GraphError(f"unknown node {nid!r} in execution target")
            visit(nid, ())
        return order

    def to_dict(self) -> dict[str, Any]:
        return {
            "nodes": [node.to_dict() for node in self.nodes],
            "outputs": list(self.outputs),
            "inputs": list(self.inputs),
        }
