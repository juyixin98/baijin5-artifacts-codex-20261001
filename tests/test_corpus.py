"""Tests for corpus normalization and threshold validation.

These assert concrete outcomes and the specific failure category
(``ErrorCode``), not merely "call did not raise".
"""

from __future__ import annotations

import pytest

from cfim.config import Settings
from cfim.corpus import normalize_corpus, support_threshold
from cfim.errors import DomainError, ErrorCode


def test_duplicate_items_within_transaction_counted_once(settings):
    # Rule 1: ["a", "a", "b"] is stored as exactly {"a", "b"}.
    result = normalize_corpus("c1", [["a", "a", "b"], ["b", "b", "b"]], settings)

    assert result.transactions == (("a", "b"), ("b",))
    assert result.duplicate_item_occurrences == 3  # 1 extra 'a' + 2 extra 'b'
    assert result.item_domain == ("a", "b")


def test_duplicate_transactions_keep_independent_identity(settings):
    # Rule 1: three identical rows remain three rows with distinct positions.
    result = normalize_corpus("c2", [["a", "b"], ["a", "b"], ["a", "b"]], settings)

    assert len(result.transactions) == 3
    assert all(tx == ("a", "b") for tx in result.transactions)
    # The threshold boundary below depends on these rows counting separately.
    assert support_threshold(3, len(result.transactions)) == 3
    with pytest.raises(DomainError) as exc:
        support_threshold(4, len(result.transactions))
    assert exc.value.code is ErrorCode.MIN_SUPPORT_INVALID


def test_empty_transactions_are_retained(settings):
    result = normalize_corpus("c3", [[], ["a"], []], settings)

    assert result.transactions == ((), ("a",), ())
    assert result.empty_transaction_count == 2
    assert len(result.transactions) == 3  # empties still count toward total


def test_items_are_stripped_and_sorted(settings):
    result = normalize_corpus("c4", [[" b ", "a"]], settings)
    assert result.transactions == (("a", "b"),)


@pytest.mark.parametrize(
    "name,transactions,expected_code",
    [
        ("", [["a"]], ErrorCode.VALIDATION_ERROR),
        ("   ", [["a"]], ErrorCode.VALIDATION_ERROR),
        ("ok", "not-a-list", ErrorCode.VALIDATION_ERROR),
        ("ok", [["a"], "nope"], ErrorCode.INVALID_TRANSACTION),
        ("ok", [[1]], ErrorCode.INVALID_ITEM),
        ("ok", [["   "]], ErrorCode.INVALID_ITEM),
        ("ok", [["a", 2]], ErrorCode.INVALID_ITEM),
    ],
)
def test_invalid_inputs_raise_distinct_categories(
    settings, name, transactions, expected_code
):
    with pytest.raises(DomainError) as exc:
        normalize_corpus(name, transactions, settings)
    assert exc.value.code is expected_code


def test_transaction_size_limit_reports_index(settings):
    oversized = [["x"] * (settings.max_items_per_transaction + 1)]
    with pytest.raises(DomainError) as exc:
        normalize_corpus("c5", oversized, settings)
    assert exc.value.code is ErrorCode.INVALID_TRANSACTION
    assert exc.value.details["transaction_index"] == 0


def test_corpus_size_limit(settings):
    settings = Settings(
        db_path=settings.db_path,
        log_level="DEBUG",
        default_budget=1,
        max_transactions=2,
        max_items_per_transaction=256,
        max_item_length=128,
        max_corpus_name_length=200,
        max_advance_budget=100_000,
    )
    with pytest.raises(DomainError) as exc:
        normalize_corpus("c6", [["a"], ["b"], ["c"]], settings)
    assert exc.value.code is ErrorCode.CORPUS_TOO_LARGE
    assert exc.value.details == {"transaction_count": 3, "limit": 2}


@pytest.mark.parametrize("value", [0, -1, 1.5, "2", True, None])
def test_threshold_must_be_positive_integer(settings, value):
    with pytest.raises(DomainError) as exc:
        support_threshold(value, 10)
    assert exc.value.code is ErrorCode.MIN_SUPPORT_INVALID


def test_threshold_boundaries(settings):
    # Boundary values are accepted exactly at the edges.
    assert support_threshold(1, 1) == 1
    assert support_threshold(5, 5) == 5
    with pytest.raises(DomainError) as exc:
        support_threshold(6, 5)
    assert exc.value.code is ErrorCode.MIN_SUPPORT_INVALID
    assert exc.value.details["min_support"] == 6
