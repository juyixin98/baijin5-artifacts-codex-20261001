"""A minimal, INDEPENDENT Newick parser used only by tests and verify.py.

It exists so that the reference checks do not rely on any code path of the
service under test: the service serializes with app/newick.py, and this
parser re-reads the emitted string from scratch to recover leaf-to-leaf
path distances. If both sides agree on the distances, the round trip is
verified by construction.

Supported subset: the exact grammar the service emits --
``(child:length,child:length)`` nodes, leaf names without quoting, a
length-less root, terminated by ``;``.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Node:
    name: str | None = None
    length: float | None = None
    children: list["Node"] = field(default_factory=list)

    @property
    def is_leaf(self) -> bool:
        return not self.children


class NewickParseError(ValueError):
    pass


def parse_newick(text: str) -> Node:
    text = text.strip()
    if not text.endswith(";"):
        raise NewickParseError("newick string must end with ';'")
    pos = 0

    def peek() -> str:
        return text[pos] if pos < len(text) else ""

    def parse_subtree() -> Node:
        nonlocal pos
        if peek() == "(":
            pos += 1
            node = Node()
            while True:
                node.children.append(parse_subtree())
                ch = peek()
                if ch == ",":
                    pos += 1
                    continue
                if ch == ")":
                    pos += 1
                    break
                raise NewickParseError(f"expected ',' or ')' at position {pos}")
        else:
            start = pos
            while pos < len(text) and text[pos] not in ",():":
                pos += 1
            name = text[start:pos].strip()
            if not name:
                raise NewickParseError(f"empty leaf name at position {start}")
            node = Node(name=name)
        if peek() == ":":
            pos += 1
            start = pos
            while pos < len(text) and text[pos] not in ",();":
                pos += 1
            try:
                node.length = float(text[start:pos])
            except ValueError as exc:
                raise NewickParseError(
                    f"invalid branch length at position {start}"
                ) from exc
        return node

    root = parse_subtree()
    if pos != len(text) - 1:
        raise NewickParseError(f"trailing content at position {pos}")
    return root


def leaf_distances(root: Node) -> dict[tuple[str, str], float]:
    """All leaf-to-leaf path distances, keyed by sorted name pairs."""
    adjacency: dict[int, list[tuple[int, float]]] = {}
    names: dict[int, str] = {}

    def build(node: Node, parent: int | None) -> int:
        node_id = id(node)
        adjacency[node_id] = []
        if parent is not None:
            assert node.length is not None
            adjacency[node_id].append((parent, node.length))
            adjacency[parent].append((node_id, node.length))
        if node.is_leaf:
            assert node.name is not None
            names[node_id] = node.name
        for child in node.children:
            build(child, node_id)
        return node_id

    build(root, None)

    leaves = sorted(names, key=lambda nid: names[nid])
    result: dict[tuple[str, str], float] = {}
    for pos_i, i in enumerate(leaves):
        # BFS from leaf i over the tree.
        dist = {i: 0.0}
        stack = [i]
        while stack:
            node = stack.pop()
            for neighbor, weight in adjacency[node]:
                if neighbor not in dist:
                    dist[neighbor] = dist[node] + weight
                    stack.append(neighbor)
        for j in leaves[pos_i + 1 :]:
            pair = (names[i], names[j])
            result[pair] = dist[j]
    return result


def cherries(root: Node) -> list[tuple[str, str]]:
    """Pairs of leaves sharing the same parent node."""
    pairs: list[tuple[str, str]] = []

    def visit(node: Node) -> None:
        if node.is_leaf:
            return
        leaf_children = [c.name for c in node.children if c.is_leaf]
        if len(leaf_children) == 2:
            pairs.append((leaf_children[0], leaf_children[1]))
        for child in node.children:
            visit(child)

    visit(root)
    return pairs
