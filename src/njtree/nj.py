"""Neighbor-joining core (Saitou & Nei 1987), deterministic by construction.

Determinism contract
--------------------
* Nodes carry stable integer ids: leaves 0..n-1 in input order, internal
  nodes n, n+1, ... in creation order. The active set is kept sorted by id.
* The join criterion is Q(i,j) = (m-2)*d(i,j) - r(i) - r(j). Among all pairs
  attaining the *exact* minimum, the lexicographically smallest (id_i, id_j)
  wins (``np.argmin`` row-major over the id-sorted active set). The number of
  tied pairs is recorded in every JoinStep and logged with the decision.
* The loop joins until 3 nodes remain; the root then attaches all three with
  the standard 3-point formulas x_a = (d_ab + d_ac - d_bc)/2 etc. The tree is
  unrooted; the root is a degree-3 artifact at the final merge point.

Negative branch lengths
-----------------------
Handled exclusively through the declared ``NegativeBranchMode`` (see
models.py). Nothing is clamped silently: CLAMP records the original value,
REPORT keeps the negative value, ERROR aborts with ComputationError.
"""

from __future__ import annotations

import logging

import numpy as np

from .errors import ComputationError
from .models import DistanceMatrix, JoinStep, NegativeBranchEvent, NegativeBranchMode
from .tree import TreeNode


def _name(node: TreeNode) -> str:
    return node.label if node.label is not None else f"#{node.node_id}"


def neighbor_joining(
    dm: DistanceMatrix,
    *,
    mode: NegativeBranchMode = NegativeBranchMode.REPORT,
    run_id: str | None = None,
    logger: logging.Logger | None = None,
) -> tuple[TreeNode, list[JoinStep], list[NegativeBranchEvent]]:
    log = logger or logging.getLogger("njtree.nj")
    n = dm.n
    if n < 3:  # defensive: the validation boundary already enforces this
        raise ComputationError(f"neighbor joining requires at least 3 taxa, got {n}", run_id=run_id)

    nodes: list[TreeNode] = [TreeNode(node_id=i, label=label) for i, label in enumerate(dm.labels)]
    active: list[int] = list(range(n))  # invariant: sorted ascending
    D = dm.values.astype(np.float64, copy=True)  # aligned with `active`
    next_id = n
    steps: list[JoinStep] = []
    events: list[NegativeBranchEvent] = []

    def apply_mode(value: float, node: TreeNode, step_index: int) -> float:
        if value >= 0.0:
            return value
        if mode is NegativeBranchMode.ERROR:
            raise ComputationError(
                f"negative branch length {value:.10g} for node {_name(node)} (mode=error)",
                run_id=run_id,
                details={"node_id": node.node_id, "value": value, "step_index": step_index},
            )
        applied = value if mode is NegativeBranchMode.REPORT else 0.0
        events.append(NegativeBranchEvent(
            step_index=step_index, node_id=node.node_id, node_label=_name(node),
            original=float(value), applied=float(applied), mode=mode.value))
        log.warning(
            "run=%s step=%d negative branch %.10g for %s applied=%.10g mode=%s",
            run_id, step_index, value, _name(node), applied, mode.value)
        return applied

    step_index = 0
    while len(active) > 3:
        m = len(active)
        r = D.sum(axis=1)
        Q = (m - 2) * D - r[:, None] - r[None, :]
        np.fill_diagonal(Q, np.inf)
        q_min = float(Q.min())
        tie_count = int(np.count_nonzero(np.triu(Q == q_min, k=1)))
        i, j = divmod(int(np.argmin(Q)), m)  # row-major first == lexicographically smallest id pair
        node_i, node_j = nodes[active[i]], nodes[active[j]]
        d_ij = float(D[i, j])
        limb_i = 0.5 * d_ij + (float(r[i]) - float(r[j])) / (2.0 * (m - 2))
        limb_j = d_ij - limb_i
        applied_i = apply_mode(limb_i, node_i, step_index)
        applied_j = apply_mode(limb_j, node_j, step_index)

        parent = TreeNode(node_id=next_id, children=[(node_i, applied_i), (node_j, applied_j)])
        nodes.append(parent)
        log.info(
            "run=%s step=%d join (%s,%s) q=%.10g ties=%d tie_break=lexicographic_node_id "
            "limbs=(%.10g,%.10g) new_node=#%d",
            run_id, step_index, _name(node_i), _name(node_j), q_min, tie_count,
            applied_i, applied_j, next_id)
        steps.append(JoinStep(
            step_index=step_index,
            active_ids=list(active),
            chosen_ids=[node_i.node_id, node_j.node_id],
            chosen_labels=[_name(node_i), _name(node_j)],
            q_value=q_min,
            tie_count=tie_count,
            limbs=[applied_i, applied_j],
            limb_originals=[limb_i, limb_j],
        ))

        keep = [k for k in range(m) if k != i and k != j]
        d_new = 0.5 * (D[i, keep] + D[j, keep] - d_ij)
        reduced = np.zeros((m - 1, m - 1), dtype=np.float64)
        reduced[:-1, :-1] = D[np.ix_(keep, keep)]
        reduced[:-1, -1] = d_new
        reduced[-1, :-1] = d_new
        D = reduced
        active = [active[k] for k in keep] + [next_id]  # stays sorted: next_id is largest
        next_id += 1
        step_index += 1

    # Final step: attach the three remaining nodes to the root.
    na, nb, nc = (nodes[a] for a in active)
    d_ab, d_ac, d_bc = float(D[0, 1]), float(D[0, 2]), float(D[1, 2])
    originals = [
        0.5 * (d_ab + d_ac - d_bc),
        0.5 * (d_ab + d_bc - d_ac),
        0.5 * (d_ac + d_bc - d_ab),
    ]
    applied = [apply_mode(v, node, step_index) for v, node in zip(originals, (na, nb, nc))]
    root = TreeNode(node_id=next_id, children=list(zip((na, nb, nc), applied)))
    log.info(
        "run=%s step=%d final root #%d attaches (%s,%s,%s) limbs=(%.10g,%.10g,%.10g)",
        run_id, step_index, next_id, _name(na), _name(nb), _name(nc), *applied)
    steps.append(JoinStep(
        step_index=step_index,
        active_ids=list(active),
        chosen_ids=[na.node_id, nb.node_id, nc.node_id],
        chosen_labels=[_name(na), _name(nb), _name(nc)],
        q_value=None,
        tie_count=None,
        limbs=applied,
        limb_originals=originals,
        is_final=True,
    ))
    return root, steps, events
