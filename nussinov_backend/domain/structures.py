"""Secondary-structure representation, traceback and legality checks.

A structure is stored as a sorted tuple of ``(i, j)`` pairs (0-based,
``i < j``). Dot-bracket notation and the 1-based pair table are *derived*
from that single source of truth, so the three representations can never
disagree.
"""
from __future__ import annotations

from dataclasses import dataclass

from .nussinov import NussinovTable
from .rules import MIN_LOOP_LENGTH, can_pair
from ..errors import StructureError, TracebackError


@dataclass(frozen=True)
class Violation:
    code: str
    message: str
    pairs: tuple[tuple[int, int], ...] = ()


@dataclass(frozen=True)
class StructureCheck:
    valid: bool
    violations: tuple[Violation, ...]


@dataclass(frozen=True)
class Structure:
    sequence: str
    pairs: tuple[tuple[int, int], ...]
    dot_bracket: str
    pair_table: tuple[int, ...]  # 1-based partner, 0 when unpaired
    min_loop_length: int

    @property
    def pair_count(self) -> int:
        return len(self.pairs)


def pairs_to_dot_bracket(pairs: tuple[tuple[int, int], ...], length: int) -> str:
    chars = ["."] * length
    for i, j in pairs:
        chars[i] = "("
        chars[j] = ")"
    return "".join(chars)


def pairs_to_pair_table(pairs: tuple[tuple[int, int], ...], length: int) -> tuple[int, ...]:
    table = [0] * length
    for i, j in pairs:
        table[i] = j + 1
        table[j] = i + 1
    return tuple(table)


def build_structure(
    sequence: str,
    pairs: tuple[tuple[int, int], ...],
    min_loop_length: int = MIN_LOOP_LENGTH,
) -> Structure:
    ordered = tuple(sorted((min(i, j), max(i, j)) for i, j in pairs))
    return Structure(
        sequence=sequence,
        pairs=ordered,
        dot_bracket=pairs_to_dot_bracket(ordered, len(sequence)),
        pair_table=pairs_to_pair_table(ordered, len(sequence)),
        min_loop_length=min_loop_length,
    )


def check_structure(
    sequence: str,
    pairs: tuple[tuple[int, int], ...],
    min_loop_length: int = MIN_LOOP_LENGTH,
) -> StructureCheck:
    """Independently validate every model constraint on a pairing."""
    violations: list[Violation] = []
    n = len(sequence)

    seen_ends: dict[int, int] = {}
    normalized: list[tuple[int, int]] = []
    for pair in pairs:
        if (not isinstance(pair, tuple) or len(pair) != 2):
            violations.append(Violation("malformed_pair", f"malformed pair: {pair!r}"))
            continue
        i, j = pair
        if not (0 <= i < n and 0 <= j < n):
            violations.append(Violation("index_out_of_range", f"pair {pair} out of range", (pair,)))
            continue
        if i >= j:
            violations.append(Violation("pair_order", f"pair requires i < j: {(i + 1, j + 1)}", (pair,)))
        if i in seen_ends or j in seen_ends:
            violations.append(
                Violation("base_used_twice", f"base appears in multiple pairs near {(i + 1, j + 1)}", (pair,))
            )
            continue
        seen_ends[i] = j
        seen_ends[j] = i
        normalized.append((i, j))
        if not can_pair(sequence[i], sequence[j]):
            violations.append(
                Violation(
                    "non_canonical_pair",
                    f"positions {i + 1},{j + 1} form non-canonical pair "
                    f"{sequence[i]}-{sequence[j]}",
                    (pair,),
                )
            )
        loop = j - i - 1
        if loop < min_loop_length:
            violations.append(
                Violation(
                    "loop_too_short",
                    f"pair positions {i + 1},{j + 1} encloses {loop} bases, "
                    f"minimum loop length is {min_loop_length}",
                    (pair,),
                )
            )

    # Non-crossing check: for a<c we require b<c (disjoint) or c<d<b (nested).
    ordered = sorted(normalized)
    for idx, (a, b) in enumerate(ordered):
        for c, d in ordered[idx + 1 :]:
            if c >= b:
                break  # all later pairs are fully disjoint
            if not (d < b):  # a < c < b and not c < d < b  -> crossing a<c<b<d
                violations.append(
                    Violation(
                        "pseudoknot_crossing",
                        f"crossing pairs at positions {(a + 1, b + 1)} and {(c + 1, d + 1)}; "
                        "pseudoknots are not supported",
                        ((a, b), (c, d)),
                    )
                )

    return StructureCheck(valid=not violations, violations=tuple(violations))


def _interval_value(table: NussinovTable, i: int, j: int) -> int:
    return 0 if i > j else table.value(i, j)


def _admissible_partners(table: NussinovTable, i: int, j: int) -> list[int]:
    target = _interval_value(table, i, j)
    partners: list[int] = []
    for k in range(i + table.min_loop_length + 1, j + 1):
        if not table.pairable(i, k):
            continue
        score = 1 + _interval_value(table, i + 1, k - 1) + _interval_value(table, k + 1, j)
        if score == target:
            partners.append(k)
    return partners


def traceback_one(table: NussinovTable) -> Structure:
    """Deterministic optimal traceback.

    Rule (documented and stable): base ``i`` is left unpaired whenever that
    preserves the optimum; otherwise its partner is the leftmost admissible
    ``k`` that preserves it.
    """
    partners: dict[int, int] = {}

    def walk(i: int, j: int) -> None:
        if i >= j:
            return
        target = _interval_value(table, i, j)
        if target == 0:
            return
        if _interval_value(table, i + 1, j) == target:
            walk(i + 1, j)
            return
        for k in _admissible_partners(table, i, j):
            partners[i] = k
            partners[k] = i
            walk(i + 1, k - 1)
            walk(k + 1, j)
            return
        raise TracebackError(
            f"no optimal traceback choice for interval [{i}, {j}] with optimum {target}; "
            "DP table and traceback disagree"
        )

    walk(0, table.size - 1)
    pairs = tuple(sorted((i, j) for i, j in partners.items() if i < j))

    check = check_structure(table.sequence, pairs, table.min_loop_length)
    if not check.valid:
        raise StructureError(
            "traceback produced an illegal structure: "
            + "; ".join(v.message for v in check.violations)
        )
    if len(pairs) != table.optimum:
        raise TracebackError(
            f"traceback yielded {len(pairs)} pairs but DP optimum is {table.optimum}"
        )
    return build_structure(table.sequence, pairs, table.min_loop_length)


def enumerate_optimal(
    table: NussinovTable,
    *,
    limit: int,
) -> tuple[list[Structure], bool]:
    """Enumerate optimal structures in deterministic order.

    An explicit stack of *pending intervals* is used (rather than nested
    recursion), so a structure is emitted only once, after every split
    interval has been solved. Returns ``(structures, truncated)``.

    Choice order matches :func:`traceback_one` (leave ``i`` unpaired first,
    then partners ``k`` ascending), so the first element is always the
    primary structure.
    """
    if limit < 1:
        raise ValueError("limit must be >= 1")

    structures: list[Structure] = []
    truncated = False

    # State: (partial pair map, pending intervals to solve, first interval span)
    initial_i, initial_j = 0, table.size - 1
    stack: list[tuple[dict[int, int], tuple[tuple[int, int], ...]]] = []
    if table.size == 0:
        # Empty sequence: the empty pairing is the unique optimal structure.
        return [build_structure("", (), table.min_loop_length)], False
    # Even when the optimum is 0 there is exactly one optimal structure:
    # the empty pairing. The walk finalizes it on its first step.
    stack.append(({}, ((initial_i, initial_j),)))

    while stack:
        current, pending = stack.pop()
        if not pending:
            structures.append(_finalize(table, current))
            if len(structures) >= limit:
                # Truncated only if further branches remain unexplored.
                return structures, bool(stack)
            continue

        (i, j), *rest = pending
        rest_tuple = tuple(rest)

        if i >= j or _interval_value(table, i, j) == 0:
            stack.append((current, rest_tuple))
            continue

        target = _interval_value(table, i, j)
        partners = _admissible_partners(table, i, j)

        # Push reverse exploration order so LIFO visits unpaired first.
        for k in reversed(partners):
            branched = dict(current)
            branched[i] = k
            branched[k] = i
            next_pending = ((i + 1, k - 1), (k + 1, j)) + rest_tuple
            stack.append((branched, next_pending))

        if _interval_value(table, i + 1, j) == target:
            stack.append((current, ((i + 1, j),) + rest_tuple))

    return structures, truncated


def _finalize(table: NussinovTable, current: dict[int, int]) -> Structure:
    pairs = tuple(sorted((i, j) for i, j in current.items() if i < j))
    check = check_structure(table.sequence, pairs, table.min_loop_length)
    if not check.valid:
        raise StructureError(
            "enumeration produced an illegal structure: "
            + "; ".join(v.message for v in check.violations)
        )
    if len(pairs) != table.optimum:
        raise TracebackError(
            f"enumeration yielded {len(pairs)} pairs but DP optimum is {table.optimum}"
        )
    return build_structure(table.sequence, pairs, table.min_loop_length)
