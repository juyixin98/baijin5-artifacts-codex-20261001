"""Constrained clustering over candidate pairs.

This is deliberately NOT threshold connected-components: pairwise similarity
is not transitive, so each candidate pair is considered in score order and a
merge happens only when it violates no cannot-link constraint and no lock.
Every accept/reject is logged with its reason for audit and replay.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..config import Settings
from ..errors import ConstraintConflictError
from ..models import ConstraintSet, Decision, PairDecision
from .constraints import UnionFind, cannot_pairs, validate_constraints
from .similarity import NormalizedRecord, ScoreBreakdown


@dataclass(frozen=True)
class ScoredPair:
    left: str
    right: str
    breakdown: ScoreBreakdown


@dataclass
class ClusteringResult:
    # record_id -> cluster key (deterministic: smallest member id)
    assignment: dict[str, str]
    decisions: list[PairDecision] = field(default_factory=list)

    def clusters(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for rid, cid in self.assignment.items():
            out.setdefault(cid, []).append(rid)
        return {cid: sorted(members) for cid, members in sorted(out.items())}


def _lock_signature(
    members: set[str], lock_of: dict[str, str]
) -> frozenset[str]:
    return frozenset(lock_of[m] for m in members if m in lock_of)


def cluster_records(
    records: list[NormalizedRecord],
    scored_pairs: list[ScoredPair],
    constraints: ConstraintSet,
    locks: dict[str, str],
    settings: Settings,
) -> ClusteringResult:
    ids = sorted(r.record_id for r in records)
    validate_constraints(constraints, set(ids))

    unknown_locks = sorted(set(locks) - set(ids))
    if unknown_locks:
        raise ConstraintConflictError(
            "locks reference unknown record id(s)",
            details={"unknown_ids": unknown_locks},
        )

    uf = UnionFind(ids)
    decisions: list[PairDecision] = []

    # Seed 1: must-link components.
    for a, b in constraints.must_link:
        if a != b and uf.find(a) != uf.find(b):
            uf.union(a, b)
            decisions.append(
                PairDecision(left=a, right=b, score=None,
                             decision=Decision.SEEDED, reason="must_link")
            )

    # Seed 2: human-confirmed locks keep their records together.
    for rid, lock_id in sorted(locks.items()):
        members = sorted(r for r, l in locks.items() if l == lock_id)
        for other in members:
            if rid < other and uf.find(rid) != uf.find(other):
                uf.union(rid, other)
                decisions.append(
                    PairDecision(left=rid, right=other, score=None,
                                 decision=Decision.SEEDED,
                                 reason=f"lock:{lock_id}")
                )

    # Locks must not contradict cannot-links.
    cannot = cannot_pairs(constraints)
    for a, b in sorted(cannot):
        if uf.find(a) == uf.find(b):
            raise ConstraintConflictError(
                "cannot-link contradicts must-link chain or human lock",
                details={"cannot_link": [a, b]},
            )

    def members_of(root: str) -> set[str]:
        return {r for r in ids if uf.find(r) == root}

    # Greedy pass over candidate pairs, highest score first; ties broken by
    # record ids so the run is deterministic.
    ordered = sorted(
        scored_pairs, key=lambda p: (-p.breakdown.score, p.left, p.right)
    )
    for pair in ordered:
        a, b = pair.left, pair.right
        score = pair.breakdown.score
        if uf.find(a) == uf.find(b):
            decisions.append(
                PairDecision(left=a, right=b, score=score,
                             decision=Decision.MERGED,
                             reason="already_same_cluster")
            )
            continue
        if score < settings.merge_threshold:
            decisions.append(
                PairDecision(left=a, right=b, score=score,
                             decision=Decision.REJECTED,
                             reason="below_threshold")
            )
            continue
        ra, rb = uf.find(a), uf.find(b)
        ma, mb = members_of(ra), members_of(rb)
        blocked = sorted(
            (x, y) for x in ma for y in mb if (min(x, y), max(x, y)) in cannot
        )
        if blocked:
            decisions.append(
                PairDecision(left=a, right=b, score=score,
                             decision=Decision.REJECTED,
                             reason=f"cannot_link:{blocked[0][0]}~{blocked[0][1]}")
            )
            continue
        la, lb = _lock_signature(ma, locks), _lock_signature(mb, locks)
        if la and lb and la != lb:
            decisions.append(
                PairDecision(left=a, right=b, score=score,
                             decision=Decision.REJECTED,
                             reason="lock_mismatch")
            )
            continue
        uf.union(a, b)
        decisions.append(
            PairDecision(left=a, right=b, score=score,
                         decision=Decision.MERGED,
                         reason=f"score>={settings.merge_threshold}:{pair.breakdown.reason}")
        )

    assignment = {rid: uf.find(rid) for rid in ids}
    return ClusteringResult(assignment=assignment, decisions=decisions)
