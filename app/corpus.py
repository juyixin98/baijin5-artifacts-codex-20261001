"""Corpus specification: ingestion, normalisation and validation rules.

Rule 1 (acceptance): repeated items inside one transaction are counted only
once (deduplicated set semantics), while duplicate transactions keep distinct
identities (each input row owns an independent tid).

Transactions are represented as ``tuple[str, tuple[str, ...]]`` pairs of
``(transaction_id, sorted_tuple_of_distinct_items)``.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence

# A transaction id may not be empty or whitespace-only; items follow the same
# rule. The empty *itemset* (empty transaction) is perfectly legal.
MAX_ITEM_LENGTH = 256


class CorpusValidationError(ValueError):
    """Raised when a transaction batch violates the corpus specification."""


@dataclass(frozen=True)
class Transaction:
    tid: str
    items: tuple[str, ...]  # sorted, deduplicated


def normalise_items(raw_items: Iterable[object]) -> tuple[str, ...]:
    """Coerce, strip, validate and de-duplicate one transaction's items.

    Order of the input is irrelevant; the result is a sorted tuple so that two
    transactions with the same content normalise identically.
    """
    cleaned: list[str] = []
    for raw in raw_items:
        if not isinstance(raw, str):
            raise CorpusValidationError(
                f"item values must be strings, got {type(raw).__name__}: {raw!r}"
            )
        item = raw.strip()
        if not item:
            raise CorpusValidationError("empty or whitespace-only items are not allowed")
        if len(item) > MAX_ITEM_LENGTH:
            raise CorpusValidationError(f"item too long (>{MAX_ITEM_LENGTH}): {item[:20]}...")
        cleaned.append(item)
    return tuple(sorted(set(cleaned)))


def normalise_transactions(
    rows: Sequence[dict],
) -> list[Transaction]:
    """Validate a raw API batch and return normalised transactions."""
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise CorpusValidationError("transactions must be a JSON array")
    if len(rows) == 0:
        raise CorpusValidationError("at least one transaction is required")

    transactions: list[Transaction] = []
    tids: list[str] = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise CorpusValidationError(f"transaction #{index} must be an object")
        tid = row.get("tid")
        if not isinstance(tid, str) or not tid.strip():
            raise CorpusValidationError(f"transaction #{index}: tid must be a non-empty string")
        tid = tid.strip()
        items_raw = row.get("items", [])
        if not isinstance(items_raw, list):
            raise CorpusValidationError(f"transaction {tid!r}: items must be an array")
        transactions.append(Transaction(tid=tid, items=normalise_items(items_raw)))
        tids.append(tid)

    duplicates = [tid for tid, count in Counter(tids).items() if count > 1]
    if duplicates:
        raise CorpusValidationError(
            f"duplicate tids inside one batch are not allowed: {sorted(duplicates)}"
        )
    return transactions


def item_counts(transactions: Iterable[Transaction]) -> Counter:
    """Per-item number of containing transactions (set semantics per tid)."""
    counts: Counter = Counter()
    for txn in transactions:
        for item in set(txn.items):
            counts[item] += 1
    return counts


def empty_transaction_count(transactions: Iterable[Transaction]) -> int:
    return sum(1 for txn in transactions if len(txn.items) == 0)
