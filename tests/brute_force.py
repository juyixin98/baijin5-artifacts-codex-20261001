"""Independent brute-force oracle for test verification.

This oracle is deliberately written from scratch using only plain Python sets
and ``itertools`` -- it shares NO code with the mining kernel under test
(``app.miner`` / ``app.vertical_index``). Its purpose is to be a second,
obviously-correct implementation that the DFS engine is checked against.

Semantics implemented here are the specification:
* items repeated within one transaction count once (per-transaction sets);
* duplicate transactions keep separate identities (separate rows in the list);
* support is the integer number of containing transactions;
* closed: no strict superset has the same support;
* maximal: no strict superset is frequent.
"""

from __future__ import annotations

from itertools import combinations
from typing import Sequence


def _normalise(rows: Sequence[dict]) -> list[frozenset[str]]:
    return [frozenset(row["items"]) for row in rows]


def all_subsets(domain: set[str]) -> list[frozenset[str]]:
    items = sorted(domain)
    subsets: list[frozenset[str]] = []
    for size in range(len(items) + 1):
        for combo in combinations(items, size):
            subsets.append(frozenset(combo))
    return subsets


def support_map(rows: Sequence[dict]) -> dict[frozenset[str], int]:
    """Support of EVERY subset via direct per-transaction subset tests."""
    transactions = _normalise(rows)
    domain = set().union(*transactions) if transactions else set()
    supports: dict[frozenset[str], int] = {}
    for subset in all_subsets(domain):
        count = sum(1 for txn in transactions if subset <= txn)
        supports[subset] = count
    return supports


def frequent_itemsets(rows: Sequence[dict], min_support: int) -> dict[frozenset[str], int]:
    return {x: s for x, s in support_map(rows).items() if s >= min_support}


def closed_itemsets(rows: Sequence[dict], min_support: int) -> dict[frozenset[str], int]:
    frequent = frequent_itemsets(rows, min_support)
    closed: dict[frozenset[str], int] = {}
    for itemset, support in frequent.items():
        # Closed iff no strict superset reaches the SAME support.
        same_support_superset = any(
            itemset < other and other_support == support
            for other, other_support in frequent.items()
        )
        if not same_support_superset:
            closed[itemset] = support
    return closed


def maximal_itemsets(rows: Sequence[dict], min_support: int) -> dict[frozenset[str], int]:
    frequent = frequent_itemsets(rows, min_support)
    return {
        itemset: support
        for itemset, support in frequent.items()
        if not any(itemset < other for other in frequent)
    }
