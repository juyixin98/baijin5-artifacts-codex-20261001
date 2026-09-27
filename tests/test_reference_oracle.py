"""Exhaustive small-partition verification against the independent oracle.

The oracle (tests/reference.py) enumerates every set partition and picks the
optimum under its own similarity/objective. The kernel must reproduce the
oracle's partition exactly on these small fixtures — and the oracle itself
must not degenerate into connected components on the chain fixture.
"""

from .fixtures import (
    CHAIN_CONSTRAINTS,
    CHAIN_RECORDS,
    ORACLE_CONSTRAINTS,
    ORACLE_EXPECTED,
    ORACLE_RECORDS,
)
from .helpers import partition_of, run_kernel
from .reference import best_partition


def _names(records):
    return {r.record_id: r.name for r in records}


def test_oracle_itself_respects_cannot_link_on_chain():
    # Guard against a broken oracle: with all pairs maximally similar, a
    # connected-components oracle would merge A, B, C — it must not.
    oracle = best_partition(
        _names(CHAIN_RECORDS),
        must_link=CHAIN_CONSTRAINTS.must_link,
        cannot_link=CHAIN_CONSTRAINTS.cannot_link,
    )
    assert oracle == frozenset([frozenset({"A", "B"}), frozenset({"C"})])


def test_kernel_matches_oracle_on_chain_fixture():
    result = run_kernel(CHAIN_RECORDS, CHAIN_CONSTRAINTS)
    oracle = best_partition(
        _names(CHAIN_RECORDS),
        must_link=CHAIN_CONSTRAINTS.must_link,
        cannot_link=CHAIN_CONSTRAINTS.cannot_link,
    )
    assert partition_of(result.assignment) == oracle


def test_kernel_matches_oracle_on_five_record_fixture():
    result = run_kernel(ORACLE_RECORDS, ORACLE_CONSTRAINTS)
    oracle = best_partition(
        _names(ORACLE_RECORDS),
        must_link=ORACLE_CONSTRAINTS.must_link,
        cannot_link=ORACLE_CONSTRAINTS.cannot_link,
    )
    assert partition_of(result.assignment) == oracle
    # And the oracle partition is the hand-written expectation, so the
    # reference answer is anchored independently of both implementations.
    assert oracle == ORACLE_EXPECTED
