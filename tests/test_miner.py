"""Mining kernel tests.

Exact hand-computed assertions plus exhaustive small-domain cross-checks
against the independent brute-force oracle in ``tests/brute_force.py`` (which
shares no code with the kernel under test).
"""

from __future__ import annotations

import random

import pytest

from app.corpus import normalise_transactions
from app.miner import derive_maximal, initialise_search, mine_chunk, sort_itemsets
from app.vertical_index import build_vertical_index
from tests.brute_force import closed_itemsets as oracle_closed
from tests.brute_force import maximal_itemsets as oracle_maximal

CORPUS_ABC = [
    {"tid": "t1", "items": ["a", "b"]},
    {"tid": "t2", "items": ["a", "b", "c"]},
    {"tid": "t3", "items": ["a", "c"]},
    {"tid": "t4", "items": ["b", "c"]},
]


def _index_for(rows):
    return build_vertical_index(normalise_transactions(rows))


def _run_to_completion(rows, min_support, budget=10_000):
    """Returns (closed_map, frames_at_end, emitted_per_chunk list)."""
    index = _index_for(rows)
    frames, root_outcome = initialise_search(index, min_support)
    closed = dict(root_outcome.closed_new)
    emitted_per_chunk = [set(root_outcome.closed_new)]
    while frames:
        outcome = mine_chunk(index, min_support, budget, frames)
        closed.update(outcome.closed_new)
        emitted_per_chunk.append(set(outcome.closed_new))
        if outcome.complete:
            break
    return closed, emitted_per_chunk


# ---------------------------------------------------------------------------
# Exact, hand-computed results
# ---------------------------------------------------------------------------

def test_handcomputed_min_support_2_closed_and_maximal_differ():
    # supports: a=b=c=3, ab=ac=bc=2, abc=1, empty=4
    closed, _ = _run_to_completion(CORPUS_ABC, 2)
    assert closed == {
        frozenset(): 4,
        frozenset("a"): 3,
        frozenset("b"): 3,
        frozenset("c"): 3,
        frozenset("ab"): 2,
        frozenset("ac"): 2,
        frozenset("bc"): 2,
    }
    maximals = set(derive_maximal(closed))
    # Maximal: ab, ac, bc -- strictly fewer than the closed sets: the
    # singletons and empty set are closed but NOT maximal.
    assert maximals == {frozenset("ab"), frozenset("ac"), frozenset("bc")}


def test_same_support_containing_set_is_not_closed():
    # Both transactions are identical {a,b}: support(a)=support(b)=support(ab)=2
    rows = [{"tid": "t1", "items": ["a", "b"]}, {"tid": "t2", "items": ["a", "b"]}]
    closed, _ = _run_to_completion(rows, 1)
    # ab is the only non-empty closed itemset; a, b share ab's support so they
    # are not closed; the empty itemset is not closed either (a, b occur in
    # every transaction, so closure(empty) = ab).
    assert closed == {frozenset("ab"): 2}
    assert set(derive_maximal(closed)) == {frozenset("ab")}


def test_empty_transactions_set_empty_itemset_support():
    rows = [
        {"tid": "e1", "items": []},
        {"tid": "e2", "items": []},
        {"tid": "x1", "items": ["x"]},
        {"tid": "x2", "items": ["x", "x"]},  # repeat inside one transaction
    ]
    # n=4; support(empty)=4, support(x)=2.
    closed_3, _ = _run_to_completion(rows, 3)
    assert closed_3 == {frozenset(): 4}
    closed_2, _ = _run_to_completion(rows, 2)
    assert closed_2 == {frozenset(): 4, frozenset("x"): 2}
    assert set(derive_maximal(closed_2)) == {frozenset("x")}


def test_threshold_boundaries():
    # min_support = n: only items in EVERY transaction survive (plus empty)
    closed_n, _ = _run_to_completion(CORPUS_ABC, 4)
    assert closed_n == {frozenset(): 4}

    # min_support = 1: the full set abc is now frequent and the unique maximal
    closed_1, _ = _run_to_completion(CORPUS_ABC, 1)
    assert closed_1[frozenset("abc")] == 1
    assert set(derive_maximal(closed_1)) == {frozenset("abc")}

    # min_support > n: trivially complete, no results
    index = _index_for(CORPUS_ABC)
    frames, outcome = initialise_search(index, 5)
    assert frames == []
    assert outcome.complete is True
    assert outcome.closed_new == {}


def test_min_support_must_be_positive_integer():
    index = _index_for(CORPUS_ABC)
    with pytest.raises(ValueError):
        initialise_search(index, 0)
    frames, _ = initialise_search(index, 1)
    with pytest.raises(ValueError):
        mine_chunk(index, 0, 1, frames)


# ---------------------------------------------------------------------------
# Exhaustive small-domain verification vs the independent oracle
# ---------------------------------------------------------------------------

def _all_corpora(max_items=3):
    """Every corpus (length 1..3) over all subsets of a small universe.

    Includes empty transactions and repeated (duplicate-content) transactions.
    """
    from itertools import combinations, product

    universe = [chr(ord("a") + i) for i in range(max_items)]
    contents = [frozenset()] + [
        frozenset(combo)
        for size in range(1, max_items + 1)
        for combo in combinations(universe, size)
    ]
    for length in (1, 2, 3):
        for choice in product(range(len(contents)), repeat=length):
            yield [
                {"tid": f"tx{pos}", "items": sorted(contents[idx])}
                for pos, idx in enumerate(choice)
            ]


@pytest.mark.parametrize("rows", list(_all_corpora(max_items=3)))
def test_exhaustive_matches_independent_oracle(rows):
    n = len(rows)
    index = _index_for(rows)
    for min_support in range(1, n + 2):  # includes the >n boundary
        frames, root_outcome = initialise_search(index, min_support)
        closed = dict(root_outcome.closed_new)
        while frames:
            outcome = mine_chunk(index, min_support, 10_000, frames)
            closed.update(outcome.closed_new)
            if outcome.complete:
                break
        assert closed == oracle_closed(rows, min_support)
        assert set(derive_maximal(closed)) == set(oracle_maximal(rows, min_support))


def test_random_corpora_match_oracle():
    rng = random.Random(20260927)
    for seed_case in range(40):
        universe = [chr(ord("a") + i) for i in range(rng.randint(1, 5))]
        n = rng.randint(1, 8)
        rows = []
        for i in range(n):
            subset = {item for item in universe if rng.random() < 0.4}
            # sometimes repeat an item inside a transaction
            items = list(subset) + ([next(iter(subset))] if subset and rng.random() < 0.3 else [])
            rows.append({"tid": f"r{i}", "items": items})
        for min_support in range(1, n + 2):
            closed, _ = _run_to_completion(rows, min_support)
            assert closed == oracle_closed(rows, min_support), (rows, min_support)
            assert set(derive_maximal(closed)) == set(oracle_maximal(rows, min_support))


# ---------------------------------------------------------------------------
# Budget exhaustion: partial results, resumability, no duplicated enumeration
# ---------------------------------------------------------------------------

def test_budgeted_chunks_converge_without_duplicates():
    index = _index_for(CORPUS_ABC)
    min_support = 1
    frames, root_outcome = initialise_search(index, min_support)
    accumulated: dict[frozenset, int] = dict(root_outcome.closed_new)
    seen_chunks: list[set] = [set(root_outcome.closed_new)]
    statuses = []
    for _ in range(100):
        outcome = mine_chunk(index, min_support, 1, frames)  # one eval per chunk
        # No closed itemset may be emitted by two different chunks.
        assert not (set(outcome.closed_new) & set().union(*seen_chunks))
        seen_chunks.append(set(outcome.closed_new))
        accumulated.update(outcome.closed_new)
        statuses.append(outcome.complete)
        if outcome.complete:
            break
    assert statuses[-1] is True
    assert accumulated == oracle_closed(CORPUS_ABC, 1)


def test_zero_budget_chunk_makes_no_progress_and_resumes():
    index = _index_for(CORPUS_ABC)
    frames, root = initialise_search(index, 1)
    outcome = mine_chunk(index, 1, 0, frames)
    assert outcome.evaluations_used == 0
    assert outcome.complete is False
    # Frames untouched: the next positive-budget chunk resumes exactly.
    outcome2 = mine_chunk(index, 1, 10_000, frames)
    assert outcome2.complete is True


def test_partial_closed_results_are_a_sound_subset_of_final():
    # Any closed itemset reported in a truncated run is still closed in the
    # final result (closure is an exact local property).
    index = _index_for(CORPUS_ABC)
    frames, root = initialise_search(index, 1)
    partial = dict(root.closed_new)
    outcome = mine_chunk(index, 1, 3, frames)
    partial.update(outcome.closed_new)
    assert outcome.complete is False
    final, _ = _run_to_completion(CORPUS_ABC, 1)
    assert set(partial) <= set(final)
    for itemset, support in partial.items():
        assert final[itemset] == support


def test_output_is_deterministic():
    first, _ = _run_to_completion(CORPUS_ABC, 2)
    second, _ = _run_to_completion(CORPUS_ABC, 2)
    assert sort_itemsets(first) == sort_itemsets(second)
