"""Independent reference oracle for itemset mining.

This implementation is deliberately naive and shares NO code with
``cfim.kernel``: it re-counts supports directly from raw transaction sets and
enumerates every subset of the item domain with ``itertools``. Test
fixtures use it as ground truth; the kernel is only accepted when its output
matches this independently computed answer set exactly.
"""

from __future__ import annotations

from itertools import combinations
from typing import FrozenSet, Iterable, Sequence


def normalize_transactions(
    transactions: Sequence[Iterable[str]],
) -> list[FrozenSet[str]]:
    """Mirror the production rule independently: intra-transaction
    duplicates collapse, transaction rows (even identical ones) stay distinct."""
    return [frozenset(tx) for tx in transactions]


def support(itemset: FrozenSet[str], transactions: Sequence[FrozenSet[str]]) -> int:
    """Count distinct transaction identities containing the itemset.

    Duplicate transactions are separate rows and each contributes one.
    """
    return sum(1 for tx in transactions if itemset <= tx)


def all_subsets(domain: Sequence[str]) -> list[FrozenSet[str]]:
    """Every subset of the domain, including the empty set, in fixed order."""
    subsets: list[FrozenSet[str]] = [frozenset()]
    for size in range(1, len(domain) + 1):
        for combo in combinations(sorted(domain), size):
            subsets.append(frozenset(combo))
    return subsets


def mine_reference(
    transactions: Sequence[Iterable[str]],
    min_support: int,
) -> tuple[
    dict[FrozenSet[str], int], dict[FrozenSet[str], int], dict[FrozenSet[str], int]
]:
    """Return (frequent, closed, maximal) itemset -> support maps.

    Definitions used as the specification:

    * frequent: ``support >= min_support``;
    * closed: frequent and no absent item occurs in *all* supporting
      transactions (equivalently every strict superset has strictly lower
      support);
    * maximal: frequent and no strict superset is frequent at all.
    """
    rows = normalize_transactions(transactions)
    domain = sorted({item for tx in rows for item in tx})
    supports = {s: support(s, rows) for s in all_subsets(domain)}
    frequent = {s: n for s, n in supports.items() if n >= min_support}

    closed: dict[FrozenSet[str], int] = {}
    for itemset, n in frequent.items():
        if not itemset:
            continue  # service mines non-empty itemsets only
        is_closed = True
        for item in domain:
            if item in itemset:
                continue
            if supports[itemset | {item}] == n:
                is_closed = False
                break
        if is_closed:
            closed[itemset] = n

    maximal: dict[FrozenSet[str], int] = {}
    for itemset, n in frequent.items():
        if not itemset:
            continue
        is_maximal = not any(itemset < other for other in frequent)
        if is_maximal:
            maximal[itemset] = n

    return frequent, closed, maximal
