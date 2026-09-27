"""Kernel tests.

Ground truth comes from ``tests.oracle`` -- an independent naive
implementation that counts supports from raw frozensets and enumerates all
subsets with itertools. The kernel never generates its own expected values.

Coverage demanded by the acceptance rules:

* exhaustive support/closedness verification on small item domains (every
  subset checked by the oracle), including same-support containment cases;
* empty transactions and threshold boundaries;
* no duplicate enumeration;
* budget expiry returns partial results and resumes to the exact same final
  answer as an unbounded run.
"""

from __future__ import annotations

from itertools import product

import pytest

from cfim.config import HARD_ENUMERATION_CEILING
from cfim.errors import DomainError, ErrorCode
from cfim.kernel import (
    STATE_VERSION,
    MiningState,
    advance,
    build_vertical_database,
    initial_state,
    intersect_sorted,
    run_to_completion,
)
from tests.oracle import mine_reference


# A fixed 4-item universe gives 2^4 = 16 possible transactions; we run
# selected corpora plus a broad randomized family below.
ITEMS = ("a", "b", "c", "d")


def _kernel_closed_map(transactions, min_support):
    db = build_vertical_database(ITEMS, transactions)
    results = run_to_completion(db, min_support)
    return {frozenset(r.itemset): r.support for r in results}, results


# ----------------------------------------------------------------------
# Exhaustive small-domain checks
# ----------------------------------------------------------------------


def test_kernel_matches_oracle_on_handbuilt_corpora():
    # Every corpus here was chosen to exhibit a distinct structural case.
    corpora = [
        # Independent items: every non-empty itemset closed and maximal.
        [["a"], ["b"], ["c"], ["d"]],
        # Perfect co-occurrence: only the full set is closed; all smaller
        # sets share its support and must NOT be reported.
        [["a", "b", "c", "d"], ["a", "b", "c", "d"]],
        # Same-support containment chain: {a} and {a,b} share support 3,
        # so only {a,b} is closed; {c} is separate.
        [["a", "b"], ["a", "b"], ["a", "b", "c"]],
        # Closure case: ab appears only inside abc; c also appears alone,
        # yet c is in every transaction containing a, so cl(ab)=abc.
        [["a", "b", "c"], ["a", "b", "c"], ["c"]],
        # Disjoint blocks.
        [["a", "b"], ["a", "b"], ["c", "d"], ["c", "d"]],
        # Empty transactions mixed in: they never support an itemset but
        # they change the transaction total / threshold boundary.
        [[], ["a"], [], ["a", "b"]],
        # Duplicate transactions keep independent identity (rule 1):
        # identical rows both contribute support.
        [["a", "b"], ["a", "b"]],
        # One item only, repeated within and across rows.
        [["a", "a"], ["a"], []],
    ]
    for transactions in corpora:
        for min_support in range(1, len(transactions) + 1):
            _, closed_ref, _ = mine_reference(transactions, min_support)
            got_map, got_results = _kernel_closed_map(transactions, min_support)
            assert got_map == dict(closed_ref), (
                f"corpus={transactions} min_support={min_support}: "
                f"kernel={got_map} oracle={dict(closed_ref)}"
            )
            # No duplicate enumeration: one result per closed set.
            keys = [tuple(r.itemset) for r in got_results]
            assert len(keys) == len(set(keys))
            # Results carry internally consistent supports.
            for r in got_results:
                assert r.support == got_map[frozenset(r.itemset)]


@pytest.mark.parametrize("seed", range(40))
def test_kernel_matches_oracle_on_random_corpora(seed):
    # Deterministic synthetic family: each of up to 6 rows is an arbitrary
    # subset of 4 items (including empty), duplicates allowed.
    rng = _Lcg(seed)
    transactions = []
    for _ in range(1 + seed % 6):
        row = [item for item in ITEMS if rng.next_float() < 0.55]
        transactions.append(row)
    min_support = 1 + rng.next_int(len(transactions))

    _, closed_ref, _ = mine_reference(transactions, min_support)
    got_map, _ = _kernel_closed_map(transactions, min_support)
    assert got_map == dict(closed_ref)


def test_exhaustive_all_corpora_three_items():
    # Brute force EVERY corpus over 3 items with rows drawn from the 8
    # possible transactions, up to length 4, allowing repeats: this is a
    # complete verification on the small domain, not a sample.
    universe = [
        [],
        ["a"],
        ["b"],
        ["c"],
        ["a", "b"],
        ["a", "c"],
        ["b", "c"],
        ["a", "b", "c"],
    ]
    checked = 0
    for length in range(1, 5):
        for rows in product(range(len(universe)), repeat=length):
            transactions = [universe[i] for i in rows]
            for min_support in (1, max(1, length // 2), length):
                _, closed_ref, _ = mine_reference(transactions, min_support)
                got_map, got_results = _kernel_closed_map(transactions, min_support)
                assert got_map == dict(closed_ref)
                keys = [frozenset(r.itemset) for r in got_results]
                assert len(keys) == len(set(keys))
                checked += 1
    assert checked > 500  # prove the loop really ran


# ----------------------------------------------------------------------
# Closed vs maximal (rule 3)
# ----------------------------------------------------------------------


def test_closed_vs_maximal_are_distinguished():
    # Two ab-rows plus one abc-row: T(ab)=3 and no absent item is in all
    # three rows, so {a,b}@3 is closed but NOT maximal (abc is frequent at
    # support 1); {a,b,c}@1 is the unique maximal closed set.
    transactions = [["a", "b"], ["a", "b"], ["a", "b", "c"]]
    db = build_vertical_database(("a", "b", "c"), transactions)
    results = run_to_completion(db, min_support=1)

    flagged = {frozenset(r.itemset): r for r in results}
    assert set(flagged) == {frozenset({"a", "b"}), frozenset({"a", "b", "c"})}
    assert flagged[frozenset({"a", "b"})].support == 3
    assert flagged[frozenset({"a", "b"})].maximal is False
    assert flagged[frozenset({"a", "b", "c"})].support == 1
    assert flagged[frozenset({"a", "b", "c"})].maximal is True

    # Oracle agreement on the maximal partition too.
    _, _, maximal_ref = mine_reference(transactions, 1)
    assert {k for k, r in flagged.items() if r.maximal} == set(maximal_ref)


def test_every_non_closed_frequent_set_has_same_support_superset():
    # Closure chain on rows abcx, abcx, abx: x is in every transaction that
    # contains a, so {a}, {a,b}, {a,x}, ... at support 3 are all non-closed
    # with the SAME-support closure {a,b,x}@3. The c-bearing sets collapse
    # onto {a,b,c,x}@2. Closed = {abx@3, abcx@2}, maximal = {abcx@2}.
    transactions = [["a", "b", "c", "x"], ["a", "b", "c", "x"], ["a", "b", "x"]]
    frequent, closed, _ = mine_reference(transactions, 2)
    for itemset, n in frequent.items():
        if not itemset:
            continue
        if itemset not in closed:
            # Rule-3 witness: a strict superset with identical support exists.
            assert any(
                itemset < other and support_other == n
                for other, support_other in frequent.items()
            )
    got_map = {
        frozenset(r.itemset): r.support
        for r in run_to_completion(
            build_vertical_database(("a", "b", "c", "x"), transactions), 2
        )
    }
    assert got_map == dict(closed)


# ----------------------------------------------------------------------
# Threshold boundary
# ----------------------------------------------------------------------


def test_threshold_boundary_support_equals_threshold():
    # {a} appears exactly twice: frequent at min_support=2, gone at 3.
    transactions = [["a"], ["a"], ["b"]]
    db = build_vertical_database(("a", "b"), transactions)
    assert {frozenset(r.itemset) for r in run_to_completion(db, 2)} == {
        frozenset({"a"})
    }
    assert run_to_completion(db, 3) == []


# ----------------------------------------------------------------------
# Budget, partial results, resume (rule 4)
# ----------------------------------------------------------------------


def _run_in_slices(transactions, min_support, budget_per_slice):
    db = build_vertical_database(sorted({i for tx in transactions for i in tx}), transactions)
    state = initial_state(db, min_support)
    seen_so_far: list = []
    slices = 0
    while not state.completed:
        advance(state, budget_per_slice)
        slices += 1
        # Partial results must be a prefix of... (order check below) and
        # never contain duplicates.
        keys = [tuple(r.itemset) for r in state.results]
        assert len(keys) == len(set(keys))
        # Monotonic accumulation: nothing previously reported disappears.
        assert keys[: len(seen_so_far)] == seen_so_far
        seen_so_far = keys
        assert slices < 1000, "resume loop did not converge"
    return state


def test_budget_one_node_per_slice_reaches_same_answer():
    transactions = [
        ["a", "b", "c"],
        ["a", "b", "d"],
        ["a", "c", "d"],
        ["b", "c"],
        ["a", "d"],
    ]
    for budget_per_slice in (1, 2, 3, 7, 1000):
        sliced = _run_in_slices(transactions, 2, budget_per_slice)
        _, closed_ref, maximal_ref = mine_reference(transactions, 2)
        got = {frozenset(r.itemset): r.support for r in sliced.results}
        assert got == dict(closed_ref)
        assert {k for k, r in ((frozenset(r.itemset), r) for r in sliced.results) if r.maximal} == set(maximal_ref)
        # Budget accounting: every slice but the last spent its full budget.
        assert sliced.total_budget_used == sliced.nodes_visited


def test_partial_results_are_valid_closed_sets():
    transactions = [["a", "b"], ["a", "b", "c"], ["a", "c", "d"], ["b", "d"]]
    db = build_vertical_database(("a", "b", "c", "d"), transactions)
    state = initial_state(db, 2)
    advance(state, budget=1)
    assert state.completed is False
    # Everything reported on a partial slice must be a genuinely closed
    # frequent itemset, verified by the independent oracle.
    _, closed_ref, _ = mine_reference(transactions, 2)
    for r in state.results:
        assert frozenset(r.itemset) in closed_ref
        assert r.support == closed_ref[frozenset(r.itemset)]
        assert r.maximal is False  # never finalized before completion


def test_budget_validation_categories():
    db = build_vertical_database(("a",), [["a"]])
    state = initial_state(db, 1)
    for bad in (0, -3, 1.0, "5", False):
        with pytest.raises(DomainError) as exc:
            advance(state, bad)  # type: ignore[arg-type]
        assert exc.value.code is ErrorCode.BUDGET_INVALID
    with pytest.raises(DomainError) as exc:
        advance(state, HARD_ENUMERATION_CEILING + 1)
    assert exc.value.code is ErrorCode.BUDGET_INVALID


def test_advance_on_completed_state_is_noop():
    db = build_vertical_database(("a", "b"), [["a"], ["b"]])
    state = initial_state(db, 1)
    advance(state, 100)
    assert state.completed
    visited = state.nodes_visited
    advance(state, 100)
    assert state.nodes_visited == visited


# ----------------------------------------------------------------------
# State serialization
# ----------------------------------------------------------------------


def test_state_roundtrip_mid_run_continues_identically():
    transactions = [
        ["a", "b"], ["a", "b", "c"], ["a", "c"], ["b", "c"], ["a", "b", "d"],
    ]
    db = build_vertical_database(("a", "b", "c", "d"), transactions)
    state = initial_state(db, 2)
    advance(state, 3)
    assert not state.completed

    restored = MiningState.from_json(state.to_json())
    advance(restored, 1000)

    uninterrupted = initial_state(db, 2)
    advance(uninterrupted, 1000)
    assert [(r.itemset, r.support, r.maximal) for r in restored.results] == [
        (r.itemset, r.support, r.maximal) for r in uninterrupted.results
    ]
    assert restored.nodes_visited == uninterrupted.nodes_visited


def test_state_rejects_incompatible_version():
    db = build_vertical_database(("a",), [["a"]])
    payload = initial_state(db, 1).to_json()
    payload["state_version"] = STATE_VERSION + 99
    with pytest.raises(DomainError) as exc:
        MiningState.from_json(payload)
    assert exc.value.code is ErrorCode.STATE_VERSION_MISMATCH


# ----------------------------------------------------------------------
# Primitive tidset helpers
# ----------------------------------------------------------------------


def test_intersect_sorted():
    assert intersect_sorted((1, 2, 3), (2, 3, 4)) == (2, 3)
    assert intersect_sorted((), (1, 2)) == ()
    assert intersect_sorted((1, 2), (3, 4)) == ()


class _Lcg:
    """Tiny deterministic generator so tests need no random module state."""

    def __init__(self, seed: int):
        self._state = (seed + 1) * 2654435761 & 0xFFFFFFFF

    def next_int(self, n: int) -> int:
        self._state = (1103515245 * self._state + 12345) & 0x7FFFFFFF
        return self._state % n

    def next_float(self) -> float:
        return self.next_int(1_000_000) / 1_000_000
