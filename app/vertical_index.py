"""Vertical tid-index built on Python integer bitsets.

Each item maps to an integer whose ``k``-th bit is set iff the transaction at
position ``k`` contains the item. Intersection is a single ``&`` operation,
which makes the closure/Eclat search fast while keeping support counts exact
(``int.bit_count``).
"""

from __future__ import annotations

from dataclasses import dataclass

from .corpus import Transaction


@dataclass(frozen=True)
class VerticalIndex:
    """Immutable vertical database over a fixed list of transactions."""

    tids: tuple[str, ...]
    items: tuple[str, ...]  # items present in at least one transaction, sorted
    tidsets: dict[str, int]

    @property
    def transaction_count(self) -> int:
        return len(self.tids)

    def support(self, tidset: int) -> int:
        return tidset.bit_count()

    def item_support(self, item: str) -> int:
        return self.tidsets.get(item, 0).bit_count()


def build_vertical_index(transactions: list[Transaction]) -> VerticalIndex:
    """Construct the index. Duplicate items inside a transaction are one bit."""
    tids = tuple(txn.tid for txn in transactions)
    tidsets: dict[str, int] = {}
    for position, txn in enumerate(transactions):
        bit = 1 << position
        for item in txn.items:  # txn.items is already deduplicated
            tidsets[item] = tidsets.get(item, 0) | bit
    return VerticalIndex(
        tids=tids,
        items=tuple(sorted(tidsets)),
        tidsets=tidsets,
    )
