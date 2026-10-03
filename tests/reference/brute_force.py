"""Independent brute-force oracle for tests.

This module enumerates *every* legal non-crossing pairing of a sequence by
raw recursion over intervals. It does NOT import or read the production DP
table, traceback or any other code under test - its only shared dependency
is the fixed compatibility rule (which bases may pair) and the minimum loop
constant, i.e. the problem specification itself.

It is the test reference answer ("参考答案"): production results must match
this exhaustive enumeration, never the other way around.
"""
from __future__ import annotations

from functools import lru_cache
from collections.abc import Iterable

# Canonical pairs including GU wobble (unordered) - the model specification.
COMPATIBLE: frozenset[tuple[str, str]] = frozenset(
    {
        ("G", "C"),
        ("C", "G"),
        ("A", "U"),
        ("U", "A"),
        ("G", "U"),
        ("U", "G"),
    }
)

Pair = tuple[int, int]
Structure = frozenset[Pair]


def _compatible(a: str, b: str) -> bool:
    return (a, b) in COMPATIBLE


def all_legal_structures(
    sequence: str,
    min_loop_length: int = 3,
) -> set[Structure]:
    """Return the full set of legal non-crossing pairings of ``sequence``.

    Recursion on interval [lo, hi]:
      * lo stays unpaired -> every structure on [lo+1, hi];
      * lo pairs with every admissible k (canonical + loop constraint) ->
        cartesian product of structures on [lo+1, k-1] and [k+1, hi].
    Splitting at the leftmost base guarantees non-crossing outputs and
    enumerates each matching exactly once. Sub-interval results are cached
    (memoization is local to this independent oracle only).
    """

    @lru_cache(maxsize=None)
    def solve(lo: int, hi: int) -> tuple[Structure, ...]:
        if lo >= hi:
            return (frozenset(),)
        results: set[Structure] = set(solve(lo + 1, hi))
        for k in range(lo + min_loop_length + 1, hi + 1):
            if not _compatible(sequence[lo], sequence[k]):
                continue
            for inner in solve(lo + 1, k - 1):
                for outer in solve(k + 1, hi):
                    results.add(frozenset({(lo, k)}) | inner | outer)
        return tuple(sorted(results, key=lambda s: (len(s), tuple(sorted(s)))))

    return set(solve(0, len(sequence) - 1)) if sequence else {frozenset()}


def optimum_count(sequence: str, min_loop_length: int = 3) -> int:
    structures = all_legal_structures(sequence, min_loop_length)
    return max((len(s) for s in structures), default=0)


def optimal_structures(
    sequence: str,
    min_loop_length: int = 3,
) -> set[Structure]:
    structures = all_legal_structures(sequence, min_loop_length)
    best = max((len(s) for s in structures), default=0)
    return {s for s in structures if len(s) == best}


def pair_count_distribution(
    sequence: str,
    min_loop_length: int = 3,
) -> dict[int, int]:
    """Map pair-count -> number of legal structures having that count."""
    distribution: dict[int, int] = {}
    for structure in all_legal_structures(sequence, min_loop_length):
        distribution[len(structure)] = distribution.get(len(structure), 0) + 1
    return dict(sorted(distribution.items()))


def is_nested_and_legal(
    sequence: str,
    pairs: Iterable[Pair],
    min_loop_length: int = 3,
) -> bool:
    """Standalone specification check used to validate outputs independently."""
    pair_set = set(pairs)
    ends: dict[int, int] = {}
    for i, j in pair_set:
        if i >= j or not (0 <= i < len(sequence) and 0 <= j < len(sequence)):
            return False
        if i in ends or j in ends:
            return False
        if not _compatible(sequence[i], sequence[j]):
            return False
        if j - i - 1 < min_loop_length:
            return False
        ends[i] = j
        ends[j] = i
    for a, b in pair_set:
        for c, d in pair_set:
            if a < c < b < d or c < a < d < b:  # strict crossing / pseudoknot
                return False
    return True


def parse_dot_bracket(dot_bracket: str) -> set[Pair]:
    """Parse dot-bracket notation into pairs (reference parser, stack based)."""
    stack: list[int] = []
    pairs: set[Pair] = set()
    for index, char in enumerate(dot_bracket):
        if char == "(":
            stack.append(index)
        elif char == ")":
            if not stack:
                raise ValueError(f"unmatched ')' at position {index}")
            pairs.add((stack.pop(), index))
        elif char != ".":
            raise ValueError(f"illegal character {char!r} at position {index}")
    if stack:
        raise ValueError(f"unmatched '(' at positions {stack}")
    return pairs
