"""Tree structure, Newick serialization, and tree-derived distances.

Conventions (part of the public contract)
-----------------------------------------
* The tree is unrooted; the root node is a degree-3 artifact placed at the
  last NJ merge point. Root children are ordered by ascending node id.
* Leaf node ids are 0..n-1 in input label order; internal ids follow in
  creation order. ``leaf_map`` exposes this identity mapping.
* Newick lengths are formatted with ``%.10g``; negative lengths are emitted
  verbatim (allowed in REPORT mode) and never silently rewritten.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TreeNode:
    node_id: int
    label: str | None = None  # None for internal nodes
    children: list[tuple["TreeNode", float]] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        return not self.children


def format_length(value: float) -> str:
    return format(value, ".10g")


def to_newick(root: TreeNode) -> str:
    """Serialize deterministically: children are emitted in stored order,
    which the NJ core guarantees to be ascending node-id order."""

    def rec(node: TreeNode) -> str:
        if node.children:
            inner = ",".join(f"{rec(child)}:{format_length(length)}" for child, length in node.children)
            return f"({inner})" + (node.label or "")
        return node.label or ""

    return rec(root) + ";"


def leaf_map(root: TreeNode) -> dict[str, int]:
    """Leaf label -> stable node id, in pre-order traversal order."""
    result: dict[str, int] = {}

    def walk(node: TreeNode) -> None:
        if node.is_leaf:
            if node.label is not None:
                result[node.label] = node.node_id
            return
        for child, _ in node.children:
            walk(child)

    walk(root)
    return result


def patristic_distances(root: TreeNode) -> dict[tuple[str, str], float]:
    """All-pairs leaf path distances. Keys are ``tuple(sorted((a, b)))``."""
    adjacency: dict[int, list[tuple[int, float]]] = {}
    leaves: dict[int, str] = {}

    def walk(node: TreeNode) -> None:
        adjacency.setdefault(node.node_id, [])
        if node.is_leaf and node.label is not None:
            leaves[node.node_id] = node.label
        for child, length in node.children:
            adjacency[node.node_id].append((child.node_id, length))
            adjacency.setdefault(child.node_id, []).append((node.node_id, length))
            walk(child)

    walk(root)

    def path_distance(src: int, dst: int) -> float:
        # a tree has a unique path; iterative DFS suffices
        stack = [(src, -1, 0.0)]
        while stack:
            node_id, parent, dist = stack.pop()
            if node_id == dst:
                return dist
            for nxt, length in adjacency[node_id]:
                if nxt != parent:
                    stack.append((nxt, node_id, dist + length))
        raise KeyError(f"no path between {src} and {dst}")  # unreachable in a tree

    result: dict[tuple[str, str], float] = {}
    ids = sorted(leaves)
    for pos, a in enumerate(ids):
        for b in ids[pos + 1:]:
            key = tuple(sorted((leaves[a], leaves[b])))
            result[key] = path_distance(a, b)
    return result
