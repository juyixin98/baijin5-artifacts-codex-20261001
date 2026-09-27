"""Must-link / cannot-link constraint handling.

Constraints are validated *before* clustering: a cannot-link that falls
inside a must-link component (transitively) is a state conflict and aborts
the run with a ConstraintConflictError naming the contradictory path.
"""

from __future__ import annotations

from ..corpus.schema import require_known_ids
from ..errors import ConstraintConflictError
from ..models import ConstraintSet


class UnionFind:
    def __init__(self, items: list[str]) -> None:
        self.parent = {x: x for x in items}

    def find(self, x: str) -> str:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


def _norm_pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def validate_constraints(
    constraints: ConstraintSet, known_ids: set[str]
) -> None:
    """Raise InputValidationError for unknown ids and ConstraintConflictError
    for must/cannot contradictions (checked transitively)."""
    flat_must = [x for pair in constraints.must_link for x in pair]
    flat_cannot = [x for pair in constraints.cannot_link for x in pair]
    require_known_ids(flat_must, known_ids, context="must_link")
    require_known_ids(flat_cannot, known_ids, context="cannot_link")

    uf = UnionFind(sorted(known_ids))
    for a, b in constraints.must_link:
        if a == b:
            continue
        uf.union(a, b)

    for a, b in constraints.cannot_link:
        if a == b:
            raise ConstraintConflictError(
                "cannot-link references the same record twice",
                details={"record_id": a},
            )
        if uf.find(a) == uf.find(b):
            raise ConstraintConflictError(
                "cannot-link contradicts must-link chain",
                details={
                    "cannot_link": [a, b],
                    "must_link": [list(p) for p in constraints.must_link],
                },
            )


def must_link_components(
    record_ids: list[str], constraints: ConstraintSet
) -> list[set[str]]:
    uf = UnionFind(record_ids)
    for a, b in constraints.must_link:
        if a != b:
            uf.union(a, b)
    comps: dict[str, set[str]] = {}
    for rid in record_ids:
        comps.setdefault(uf.find(rid), set()).add(rid)
    return list(comps.values())


def cannot_pairs(constraints: ConstraintSet) -> set[tuple[str, str]]:
    return {_norm_pair(a, b) for a, b in constraints.cannot_link}
