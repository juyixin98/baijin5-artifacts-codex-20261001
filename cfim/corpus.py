"""Corpus normalization and validation.

Semantics enforced here (acceptance rules 1):

* Duplicate items inside one transaction are counted once: the raw transaction
  ``["a", "a", "b"]`` is stored as the set ``{"a", "b"}``.
* Duplicate transactions keep independent identity: two identical transactions
  receive distinct tids, so each one still contributes one to every itemset's
  support.
* Empty transactions are legal: they are stored as empty rows and count toward
  the transaction total (which matters for the minimum-support boundary).
* Item identifiers are non-empty strings after stripping surrounding
  whitespace; an all-whitespace item is invalid.
"""

from __future__ import annotations

from dataclasses import dataclass

from cfim.config import Settings
from cfim.errors import DomainError, ErrorCode


@dataclass(frozen=True)
class NormalizedCorpus:
    """A corpus after validation and de-duplication within transactions.

    Attributes:
        name: corpus identifier.
        transactions: tuple of item tuples, index is the raw transaction
            position; tids assigned by the store start at 1 (tid = index + 1).
        item_domain: sorted tuple of every distinct item that appears.
        empty_transaction_count: number of transactions containing no items.
        duplicate_item_occurrences: total number of repeated item occurrences
            removed during normalization, exposed for explainability.
    """

    name: str
    transactions: tuple[tuple[str, ...], ...]
    item_domain: tuple[str, ...]
    empty_transaction_count: int
    duplicate_item_occurrences: int


def normalize_corpus(
    name: str,
    raw_transactions: list[list[str]] | tuple[list[str], ...],
    settings: Settings,
) -> NormalizedCorpus:
    """Validate and normalize an incoming corpus.

    Raises:
        DomainError: on malformed name, non-string items, blank items,
            oversized transactions, or too many transactions. Each error
            carries a distinct ``ErrorCode`` so callers can branch on the
            failure category.
    """
    if not isinstance(name, str) or not name.strip():
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            "corpus name must be a non-empty string",
            {"field": "name"},
        )
    name = name.strip()
    if len(name) > settings.max_corpus_name_length:
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            f"corpus name exceeds {settings.max_corpus_name_length} characters",
            {"field": "name", "length": len(name)},
        )
    if not isinstance(raw_transactions, (list, tuple)):
        raise DomainError(
            ErrorCode.VALIDATION_ERROR,
            "transactions must be a list of item lists",
            {"field": "transactions", "type": type(raw_transactions).__name__},
        )
    if len(raw_transactions) > settings.max_transactions:
        raise DomainError(
            ErrorCode.CORPUS_TOO_LARGE,
            f"corpus has {len(raw_transactions)} transactions; limit is "
            f"{settings.max_transactions}",
            {"transaction_count": len(raw_transactions), "limit": settings.max_transactions},
        )

    normalized: list[tuple[str, ...]] = []
    item_domain: set[str] = set()
    empty_count = 0
    duplicate_occurrences = 0

    for position, raw_tx in enumerate(raw_transactions):
        if not isinstance(raw_tx, (list, tuple)):
            raise DomainError(
                ErrorCode.INVALID_TRANSACTION,
                f"transaction at index {position} must be a list of items",
                {"transaction_index": position, "type": type(raw_tx).__name__},
            )
        if len(raw_tx) > settings.max_items_per_transaction:
            raise DomainError(
                ErrorCode.INVALID_TRANSACTION,
                f"transaction at index {position} has {len(raw_tx)} items; "
                f"limit is {settings.max_items_per_transaction}",
                {
                    "transaction_index": position,
                    "item_count": len(raw_tx),
                    "limit": settings.max_items_per_transaction,
                },
            )

        seen: set[str] = set()
        for raw_item in raw_tx:
            if not isinstance(raw_item, str):
                raise DomainError(
                    ErrorCode.INVALID_ITEM,
                    f"item at transaction {position} is not a string",
                    {"transaction_index": position, "item_repr": repr(raw_item)},
                )
            item = raw_item.strip()
            if not item:
                raise DomainError(
                    ErrorCode.INVALID_ITEM,
                    f"blank item found in transaction {position}",
                    {"transaction_index": position},
                )
            if len(item) > settings.max_item_length:
                raise DomainError(
                    ErrorCode.INVALID_ITEM,
                    f"item in transaction {position} exceeds "
                    f"{settings.max_item_length} characters",
                    {"transaction_index": position, "length": len(item)},
                )
            if item in seen:
                # Rule 1: repeated occurrence within one transaction is ignored.
                duplicate_occurrences += 1
                continue
            seen.add(item)
            item_domain.add(item)

        ordered = tuple(sorted(seen))
        if not ordered:
            empty_count += 1
        normalized.append(ordered)

    return NormalizedCorpus(
        name=name,
        transactions=tuple(normalized),
        item_domain=tuple(sorted(item_domain)),
        empty_transaction_count=empty_count,
        duplicate_item_occurrences=duplicate_occurrences,
    )


def support_threshold(min_support: object, transaction_count: int) -> int:
    """Validate the minimum-support threshold as an integer count.

    Rule 2: minimum support is an absolute integer threshold on the number of
    distinct transactions containing the itemset (never a fraction).

    Bounds: ``1 <= min_support <= transaction_count``. Zero is rejected rather
    than treated as "match everything", because a threshold of zero makes the
    empty itemset special and has no useful mining meaning here; an empty
    corpus therefore cannot produce any frequent itemset.
    """
    if isinstance(min_support, bool) or not isinstance(min_support, int):
        raise DomainError(
            ErrorCode.MIN_SUPPORT_INVALID,
            "min_support must be an integer transaction-count threshold",
            {"received_type": type(min_support).__name__},
        )
    if min_support < 1:
        raise DomainError(
            ErrorCode.MIN_SUPPORT_INVALID,
            "min_support must be at least 1",
            {"min_support": min_support},
        )
    if min_support > transaction_count:
        raise DomainError(
            ErrorCode.MIN_SUPPORT_INVALID,
            f"min_support={min_support} exceeds transaction count "
            f"{transaction_count}; no itemset can be frequent",
            {"min_support": min_support, "transaction_count": transaction_count},
        )
    return min_support
