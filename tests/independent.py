"""Independent reference utilities for tests and scripts/validate.py.

Deliberately shares no code with njtree's tree/nj modules: if the core
mis-serializes or mis-computes, these checks fail instead of agreeing with
it. Reference expectations come from hand-computed fixture values or from
this file -- never from the NJ core itself.
"""

from __future__ import annotations

import re

_TOKEN = re.compile(r"\s*([(),:;]|[^(),:;\s]+)")


class Node:
    __slots__ = ("name", "length", "children")

    def __init__(self, name=None, length=None, children=None):
        self.name = name
        self.length = length
        self.children = children or []


def parse_newick(text: str) -> Node:
    tokens = _TOKEN.findall(text)
    pos = 0

    def peek():
        return tokens[pos] if pos < len(tokens) else None

    def take(expected=None):
        nonlocal pos
        tok = tokens[pos]
        if expected is not None and tok != expected:
            raise ValueError(f"expected {expected!r}, got {tok!r} in {text!r}")
        pos += 1
        return tok

    def subtree() -> Node:
        if peek() == "(":
            take("(")
            children = [subtree()]
            while peek() == ",":
                take(",")
                children.append(subtree())
            take(")")
            node = Node(children=children)
            if peek() not in (",", ")", ":", ";", None):
                node.name = take()
        else:
            node = Node(name=take())
        if peek() == ":":
            take(":")
            node.length = float(take())
        return node

    root = subtree()
    take(";")
    if pos != len(tokens):
        raise ValueError(f"trailing tokens in {text!r}")
    return root


def _adjacency(root: Node):
    adj: dict[int, list[tuple[int, float]]] = {}
    names: dict[int, str] = {}

    def walk(node: Node):
        adj.setdefault(id(node), [])
        if not node.children and node.name is not None:
            names[id(node)] = node.name
        for child in node.children:
            length = child.length if child.length is not None else 0.0
            adj[id(node)].append((id(child), length))
            adj.setdefault(id(child), []).append((id(node), length))
            walk(child)

    walk(root)
    return adj, names


def leaf_path_lengths(root: Node) -> dict[frozenset, float]:
    """All leaf-pair path distances, keyed by frozenset({label_a, label_b})."""
    adj, names = _adjacency(root)
    out: dict[frozenset, float] = {}
    ids = sorted(names, key=lambda i: names[i])
    for x_pos, x in enumerate(ids):
        for y in ids[x_pos + 1:]:
            # unique path in a tree: iterative DFS
            stack = [(x, -1, 0.0)]
            while stack:
                cur, parent, dist = stack.pop()
                if cur == y:
                    out[frozenset((names[x], names[y]))] = dist
                    break
                for nxt, w in adj[cur]:
                    if nxt != parent:
                        stack.append((nxt, cur, dist + w))
    return out


def splits(root: Node) -> set[frozenset]:
    """Nontrivial unrooted splits. Each split is
    frozenset({frozenset(side_a), frozenset(side_b)}) with both sides >= 2."""
    adj, names = _adjacency(root)
    all_leaves = frozenset(names.values())
    seen_edges: set[frozenset] = set()
    out: set[frozenset] = set()
    for src in list(adj):
        for dst, _ in adj[src]:
            edge = frozenset((src, dst))
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            # side reachable from src without crossing to dst
            side: set[int] = set()
            stack = [src]
            while stack:
                cur = stack.pop()
                if cur in side:
                    continue
                side.add(cur)
                for nxt, _ in adj[cur]:
                    if nxt != dst:
                        stack.append(nxt)
            side_labels = frozenset(names[i] for i in side if i in names)
            other = all_leaves - side_labels
            if len(side_labels) >= 2 and len(other) >= 2:
                out.add(frozenset((side_labels, frozenset(other))))
    return out


def total_residual(labels: list[str], matrix: list[list[float]],
                   paths: dict[frozenset, float]) -> float:
    total = 0.0
    for i, a in enumerate(labels):
        for j, b in enumerate(labels):
            if j <= i:
                continue
            total += abs(matrix[i][j] - paths[frozenset((a, b))])
    return total


def attachments(root: Node) -> dict[str, tuple[int, float]]:
    """Leaf label -> (id of the node it attaches to, branch length)."""
    out: dict[str, tuple[int, float]] = {}

    def walk(node: Node):
        for child in node.children:
            if not child.children and child.name is not None:
                out[child.name] = (id(node), child.length if child.length is not None else 0.0)
            walk(child)

    walk(root)
    return out


def cherries(root: Node) -> list[tuple[tuple[str, str], tuple[float, float]]]:
    """(label pair, branch-length pair) for every internal node whose
    children are both leaves."""
    out = []

    def walk(node: Node):
        if node.children and all(not c.children for c in node.children):
            labels = tuple(c.name for c in node.children)
            lengths = tuple(c.length if c.length is not None else 0.0 for c in node.children)
            out.append((labels, lengths))
        for child in node.children:
            walk(child)

    walk(root)
    return out
