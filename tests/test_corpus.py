"""Corpus specification tests: duplicate-item collapse and duplicate-transaction identity."""

from __future__ import annotations

import pytest

from app.corpus import (
    CorpusValidationError,
    empty_transaction_count,
    item_counts,
    normalise_items,
    normalise_transactions,
)
from app.service import compute_stats


def test_repeated_items_within_one_transaction_count_once():
    txn = normalise_transactions([{"tid": "t1", "items": ["x", "x", "x", "y"]}])
    assert txn[0].items == ("x", "y")
    assert item_counts(txn) == {"x": 1, "y": 1}


def test_duplicate_transactions_keep_independent_identities():
    rows = [
        {"tid": "a1", "items": ["x"]},
        {"tid": "a2", "items": ["x"]},  # same content, distinct tid
        {"tid": "a3", "items": ["x"]},
    ]
    txns = normalise_transactions(rows)
    assert [t.tid for t in txns] == ["a1", "a2", "a3"]
    stats = compute_stats(txns)
    assert stats.transaction_count == 3
    assert stats.duplicate_transaction_count == 3
    # Each identity contributes one containing transaction.
    assert item_counts(txns) == {"x": 3}


def test_empty_transactions_are_preserved():
    txns = normalise_transactions([
        {"tid": "e1", "items": []},
        {"tid": "e2", "items": []},
        {"tid": "n1", "items": ["z"]},
    ])
    assert empty_transaction_count(txns) == 2
    assert txns[0].items == ()
    stats = compute_stats(txns)
    assert stats.empty_transaction_count == 2


def test_duplicate_tid_in_one_batch_is_rejected_with_category():
    with pytest.raises(CorpusValidationError) as exc:
        normalise_transactions([
            {"tid": "dup", "items": ["a"]},
            {"tid": "dup", "items": ["b"]},
        ])
    assert "duplicate tids" in str(exc.value)


@pytest.mark.parametrize(
    "rows,expected_fragment",
    [
        ([{"items": ["a"]}], "tid must be a non-empty string"),
        ([{"tid": "t1", "items": "not-a-list"}], "items must be an array"),
        ([{"tid": "t1", "items": [123]}], "must be strings"),
        ([{"tid": " t1 ", "items": ["   "]}], "empty or whitespace-only"),
        ([], "at least one transaction"),
    ],
)
def test_invalid_inputs_raise_named_failures(rows, expected_fragment):
    with pytest.raises(CorpusValidationError) as exc:
        normalise_transactions(rows)
    assert expected_fragment in str(exc.value)


def test_normalise_items_sorts_and_deduplicates():
    assert normalise_items(["c", "a", "b", "a"]) == ("a", "b", "c")
    assert normalise_items([]) == ()
