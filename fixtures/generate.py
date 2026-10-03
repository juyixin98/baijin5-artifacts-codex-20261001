#!/usr/bin/env python3
"""Regenerate the derived fixtures (additive6, noisy6, duplicates).

Reference matrices are produced here by hand-summing the branch lengths of
hand-defined trees via plain BFS -- this script shares no code with the NJ
core under test, so the fixtures are genuine independent references.

Usage: python3 fixtures/generate.py
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent

# Hand-defined 6-taxon tree as an edge list (leaf/internal labels, length).
# Unrooted topology: cherries {A,B} and {C,D} joined at T, cherry {E,F} at S,
# internal edge T-S of length 5.
ADDITIVE6_LEAVES = ["A", "B", "C", "D", "E", "F"]
ADDITIVE6_EDGES = [
    ("A", "P", 1.0), ("B", "P", 1.0),
    ("C", "Q", 1.5), ("D", "Q", 0.5),
    ("P", "T", 2.0), ("Q", "T", 1.0),
    ("E", "S", 2.0), ("F", "S", 1.0),
    ("T", "S", 5.0),
]
ADDITIVE6_TRUE_NEWICK = "((E:2,F:1):3,((A:1,B:1):2,(C:1.5,D:0.5):1):2);"


def path_matrix(leaves: list[str], edges: list[tuple[str, str, float]]) -> list[list[float]]:
    adjacency: dict[str, list[tuple[str, float]]] = {}
    for a, b, w in edges:
        adjacency.setdefault(a, []).append((b, w))
        adjacency.setdefault(b, []).append((a, w))

    def path(src: str, dst: str) -> float:
        seen = {src}
        queue: deque[tuple[str, float]] = deque([(src, 0.0)])
        while queue:
            node, dist = queue.popleft()
            if node == dst:
                return dist
            for nxt, w in adjacency[node]:
                if nxt not in seen:
                    seen.add(nxt)
                    queue.append((nxt, dist + w))
        raise AssertionError(f"disconnected: {src} {dst}")

    n = len(leaves)
    return [[0.0 if i == j else path(leaves[i], leaves[j]) for j in range(n)] for i in range(n)]


def write(name: str, payload: dict) -> None:
    path = FIXTURES / name
    path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {path}")


def main() -> None:
    matrix6 = path_matrix(ADDITIVE6_LEAVES, ADDITIVE6_EDGES)
    write("additive6.json", {
        "name": "additive6",
        "description": "Additive matrix of the hand-defined 6-taxon tree in "
                       "fixtures/generate.py (ADDITIVE6_EDGES), computed by BFS path sums.",
        "labels": ADDITIVE6_LEAVES,
        "matrix": matrix6,
        "expected": {
            "total_absolute_residual": 0.0,
            "true_tree_newick": ADDITIVE6_TRUE_NEWICK,
        },
    })

    # Deterministic noise: delta[i][j] = 0.1 * (((i+1)*(j+3)) % 3 - 1), i<j.
    n = len(ADDITIVE6_LEAVES)
    noisy = [row[:] for row in matrix6]
    for i in range(n):
        for j in range(i + 1, n):
            delta = 0.1 * (((i + 1) * (j + 3)) % 3 - 1)
            noisy[i][j] = noisy[j][i] = round(matrix6[i][j] + delta, 10)
    write("noisy6.json", {
        "name": "noisy6",
        "description": "additive6 matrix plus deterministic noise "
                       "delta[i][j] = 0.1*(((i+1)*(j+3)) % 3 - 1) for i<j (symmetric, zero diagonal).",
        "labels": ADDITIVE6_LEAVES,
        "matrix": noisy,
        "expected": {
            "residual_must_exceed": 0.0,
            "true_tree_newick": ADDITIVE6_TRUE_NEWICK,
        },
    })

    # 5-taxon matrix where E is an exact duplicate of D (additive4 base).
    base = [
        [0, 3, 6, 7],
        [3, 0, 7, 8],
        [6, 7, 0, 5],
        [7, 8, 5, 0],
    ]
    dup = [row + [row[3]] for row in base] + [[7, 8, 5, 0, 0]]
    write("duplicates.json", {
        "name": "duplicates",
        "description": "additive4 extended with leaf E duplicating D exactly "
                       "(identical rows, d(D,E)=0). D and E must be co-located: same "
                       "attachment node, zero-length branches (cherry topology is "
                       "non-identifiable at zero distance, so co-location is the contract).",
        "labels": ["A", "B", "C", "D", "E"],
        "matrix": dup,
        "expected": {
            "sister_labels": ["D", "E"],
            "sister_branch_lengths": [0.0, 0.0],
            "total_absolute_residual": 0.0,
        },
    })


if __name__ == "__main__":
    main()
