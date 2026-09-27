"""Kernel clustering tests: concrete partitions and decision-log reasons.

The chain test is the key anti-connected-components check: A-B, B-C, A-C
are all pairwise identical after normalization, yet A and C must end up in
different clusters because of the cannot-link constraint.
"""

from er_backend.models import ConstraintSet, Decision

from .fixtures import (
    ALIAS_RECORDS,
    CHAIN_CONSTRAINTS,
    CHAIN_RECORDS,
    CROSSLINGUAL_RECORDS,
    SAMENAME_CONSTRAINTS,
    SAMENAME_RECORDS,
    TOKEN_MAP,
    rec,
)
from .helpers import partition_of, run_kernel


def test_chain_similarity_does_not_transitively_merge_cannot_link():
    result = run_kernel(CHAIN_RECORDS, CHAIN_CONSTRAINTS)
    partition = partition_of(result.assignment)
    assert partition == frozenset(
        [frozenset({"A", "B"}), frozenset({"C"})]
    ), "connected components would have merged A, B, C into one cluster"

    rejections = [
        d for d in result.decisions
        if d.decision == Decision.REJECTED and d.reason.startswith("cannot_link")
    ]
    assert rejections, "expected cannot-link rejections in the decision log"
    rejected_pairs = {(d.left, d.right) for d in rejections}
    assert ("A", "C") in rejected_pairs


def test_alias_match_merges_records():
    result = run_kernel(ALIAS_RECORDS)
    assert partition_of(result.assignment) == frozenset(
        [frozenset({"D", "E"})]
    )
    merged = [d for d in result.decisions if d.decision == Decision.MERGED]
    assert any("alias_match" in d.reason for d in merged)


def test_cross_lingual_normalization_merges_same_org():
    result = run_kernel(CROSSLINGUAL_RECORDS, token_map=TOKEN_MAP)
    partition = partition_of(result.assignment)
    assert partition == frozenset(
        [frozenset({"F", "G"}), frozenset({"H"})]
    ), "CJK and latin names of the same org must cluster together"


def test_same_name_does_not_imply_same_entity():
    # Identical names, different registration ids, plus a cannot-link:
    # the records must stay in separate clusters despite a perfect score.
    result = run_kernel(SAMENAME_RECORDS, SAMENAME_CONSTRAINTS)
    assert partition_of(result.assignment) == frozenset(
        [frozenset({"I"}), frozenset({"J"})]
    )


def test_shared_registration_id_is_strong_evidence():
    records = [
        rec("R1", "Umbrella Holdings", attributes={"registration_id": "REG-9"}),
        rec("R2", "Umbrella Hldgs", attributes={"registration_id": "REG-9"}),
    ]
    result = run_kernel(records)
    assert partition_of(result.assignment) == frozenset([frozenset({"R1", "R2"})])
    merged = [d for d in result.decisions if d.decision == Decision.MERGED]
    assert any("shared_registration_id" in d.reason for d in merged)


def test_human_lock_forces_merge_below_threshold():
    records = [rec("P", "Pine Labs"), rec("Q", "Pine Laboratories")]
    unlocked = run_kernel(records)
    assert partition_of(unlocked.assignment) == frozenset(
        [frozenset({"P"}), frozenset({"Q"})]
    ), "pair must be below threshold without the lock"

    locked = run_kernel(records, locks={"P": "L1", "Q": "L1"})
    assert partition_of(locked.assignment) == frozenset([frozenset({"P", "Q"})])
    seeded = [d for d in locked.decisions if d.decision == Decision.SEEDED]
    assert any(d.reason == "lock:L1" for d in seeded)


def test_conflicting_locks_block_merge():
    records = [rec("S1", "Orion Systems Ltd"), rec("S2", "Orion Systems Inc")]
    result = run_kernel(records, locks={"S1": "L1", "S2": "L2"})
    assert partition_of(result.assignment) == frozenset(
        [frozenset({"S1"}), frozenset({"S2"})]
    )
    assert any(
        d.decision == Decision.REJECTED and d.reason == "lock_mismatch"
        for d in result.decisions
    )


def test_clustering_is_deterministic():
    first = run_kernel(CROSSLINGUAL_RECORDS, token_map=TOKEN_MAP)
    second = run_kernel(CROSSLINGUAL_RECORDS, token_map=TOKEN_MAP)
    assert first.assignment == second.assignment
    assert [d.model_dump() for d in first.decisions] == [
        d.model_dump() for d in second.decisions
    ]


def test_must_link_seeds_cluster_without_pairwise_score():
    records = [rec("X", "Northwind Traders"), rec("Y", "Zebra Outfitters")]
    constraints = ConstraintSet(must_link=[("X", "Y")])
    result = run_kernel(records, constraints)
    assert partition_of(result.assignment) == frozenset([frozenset({"X", "Y"})])
    seeded = [d for d in result.decisions if d.decision == Decision.SEEDED]
    assert any(d.reason == "must_link" for d in seeded)
