"""Neighbor Joining core (Saitou & Nei 1987, O(n^3) implementation).

Design decisions that the tests pin down:

Tie-breaking (stable):
    Pairs are enumerated in ascending ``(node_id_i, node_id_j)`` order, where
    leaf node ids follow the input label order and internal nodes take the
    next sequential ids as they are created. The minimum Q uses exact float
    equality; the FIRST pair reaching the minimum wins. When more than one
    pair attains the exact minimum, a ``q_tie`` event listing every candidate
    and the chosen pair is recorded, so the choice is replayable from the
    log. Same input + same options => byte-identical output, always.

Negative branch lengths:
    Handled per the declared ``negative_branch_mode`` (allow / clamp /
    error). Nothing is ever silently set to zero: ``clamp`` records a
    ``branch_clamped`` event with the raw value, and ``allow`` records a
    ``negative_branch`` event. The residual report is computed from the
    emitted tree, so clamping shows up as fit error.

Final edge:
    NJ is an unrooted method. When two nodes remain, a synthetic root node is
    inserted at the midpoint of the final edge (each side gets length d/2).
    This preserves every patristic distance; it is a representation choice,
    not a biological claim, and is documented as such.

Data contract:
    Input: validated labels + square matrix (see validation.py).
    Output: NJResult with an adjacency map, root id, leaf labels and the
    event list. Errors: ComputationFailedError only (input problems are
    caught earlier, in validation).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .errors import ComputationFailedError
from .models import NegativeBranchMode


@dataclass
class NJResult:
    adjacency: dict[int, dict[int, float]]
    root_id: int
    leaf_labels: dict[int, str]
    events: list[dict] = field(default_factory=list)


def _q_values(active: list[int], dist: list[list[float]]) -> dict[tuple[int, int], float]:
    """Q(i, j) = (m - 2) * d(i, j) - r(i) - r(j) for active pairs i < j."""
    m = len(active)
    row_sum = {i: sum(dist[i][j] for j in active if j != i) for i in active}
    q: dict[tuple[int, int], float] = {}
    for pos_i, i in enumerate(active):
        for j in active[pos_i + 1 :]:
            q[(i, j)] = (m - 2) * dist[i][j] - row_sum[i] - row_sum[j]
    return q


def _select_pair(
    q: dict[tuple[int, int], float], round_index: int, events: list[dict]
) -> tuple[int, int]:
    """Stable minimum: first pair in enumeration order at the exact min Q."""
    best = min(q.values())
    candidates = [pair for pair, value in q.items() if value == best]
    chosen = candidates[0]  # dict preserves the ascending enumeration order
    if len(candidates) > 1:
        events.append(
            {
                "type": "q_tie",
                "round": round_index,
                "message": "multiple pairs share the minimal Q; the pair with "
                "the lowest (i, j) node ids in input order was chosen",
                "data": {
                    "q": best,
                    "candidates": [list(p) for p in candidates],
                    "chosen": list(chosen),
                    "rule": "first_in_enumeration_order",
                },
            }
        )
    return chosen


def _apply_negative_mode(
    length: float,
    mode: NegativeBranchMode,
    round_index: int,
    node_id: int,
    parent_id: int,
    events: list[dict],
) -> float:
    if length >= 0.0:
        return length
    if mode == "error":
        raise ComputationFailedError(
            "negative branch length encountered under 'error' mode",
            {
                "round": round_index,
                "node": node_id,
                "parent": parent_id,
                "length": length,
            },
        )
    if mode == "clamp":
        events.append(
            {
                "type": "branch_clamped",
                "round": round_index,
                "message": "negative branch length clamped to 0.0; the fit "
                "error this introduces is visible in the residual report",
                "data": {
                    "node": node_id,
                    "parent": parent_id,
                    "raw_length": length,
                    "emitted_length": 0.0,
                },
            }
        )
        return 0.0
    events.append(
        {
            "type": "negative_branch",
            "round": round_index,
            "message": "negative branch length kept as computed ('allow' mode)",
            "data": {"node": node_id, "parent": parent_id, "length": length},
        }
    )
    return length


def neighbor_joining(
    labels: list[str],
    matrix: list[list[float]],
    negative_branch_mode: NegativeBranchMode = "allow",
) -> NJResult:
    """Build a tree from a validated distance matrix. Never mutates input."""
    n = len(labels)
    if n < 2:
        raise ComputationFailedError(
            "neighbor joining needs at least 2 taxa", {"got": n}
        )

    events: list[dict] = []
    adjacency: dict[int, dict[int, float]] = {}
    leaf_labels = {i: labels[i] for i in range(n)}

    # Working distances indexed by node id; internal nodes extend the lists.
    dist: list[list[float]] = [row[:] for row in matrix]
    next_id = n

    def add_edge(a: int, b: int, length: float) -> None:
        adjacency.setdefault(a, {})[b] = length
        adjacency.setdefault(b, {})[a] = length

    def join(round_index: int, i: int, j: int, active: list[int]) -> int:
        """Join nodes i and j into a new node; return the new node id."""
        nonlocal next_id, dist
        m = len(active)
        row_i = sum(dist[i][k] for k in active if k != i)
        row_j = sum(dist[j][k] for k in active if k != j)
        limb_i = 0.5 * dist[i][j] + (row_i - row_j) / (2.0 * (m - 2))
        limb_j = dist[i][j] - limb_i

        u = next_id
        next_id += 1
        limb_i = _apply_negative_mode(
            limb_i, negative_branch_mode, round_index, i, u, events
        )
        limb_j = _apply_negative_mode(
            limb_j, negative_branch_mode, round_index, j, u, events
        )
        add_edge(i, u, limb_i)
        add_edge(j, u, limb_j)

        # Distances from the new node to every remaining active node.
        new_row = [0.0] * (u + 1)
        for k in active:
            if k in (i, j):
                continue
            d_uk = 0.5 * (dist[i][k] + dist[j][k] - dist[i][j])
            new_row[k] = d_uk
            dist[k].append(d_uk)
        dist.append(new_row)
        return u

    active = list(range(n))
    round_index = 0
    while len(active) > 2:
        q = _q_values(active, dist)
        i, j = _select_pair(q, round_index, events)
        u = join(round_index, i, j, active)
        active = [k for k in active if k not in (i, j)] + [u]
        round_index += 1

    # Final edge: insert the root at its midpoint (see module docstring).
    a, b = active
    root = next_id
    half = dist[a][b] / 2.0
    half_a = _apply_negative_mode(half, negative_branch_mode, round_index, a, root, events)
    half_b = _apply_negative_mode(half, negative_branch_mode, round_index, b, root, events)
    add_edge(a, root, half_a)
    add_edge(b, root, half_b)

    return NJResult(
        adjacency=adjacency,
        root_id=root,
        leaf_labels=leaf_labels,
        events=events,
    )
