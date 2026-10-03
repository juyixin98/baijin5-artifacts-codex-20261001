"""Domain service: orchestrates fill, self-verification and traceback."""
from __future__ import annotations

from dataclasses import dataclass

from .nussinov import NussinovTable, fill_dp
from .rules import MIN_LOOP_LENGTH, MODEL_WARNINGS
from .structures import Structure, StructureCheck, check_structure, enumerate_optimal, traceback_one
from .verification import verify_table


@dataclass(frozen=True)
class FoldResult:
    sequence: str
    optimum: int
    primary: Structure
    alternatives: tuple[Structure, ...]
    alternatives_truncated: bool
    table: NussinovTable
    self_check: StructureCheck
    min_loop_length: int
    warnings: tuple[str, ...]


def fold(
    sequence: str,
    *,
    enumerate_alternatives: bool = False,
    alternatives_limit: int = 10,
    min_loop_length: int = MIN_LOOP_LENGTH,
) -> FoldResult:
    """Run the full domain pipeline for a normalized ACGU sequence."""
    table = fill_dp(sequence, min_loop_length)

    # Internal integrity gate: independent scalar implementation must agree.
    verify_table(sequence, table.optimum, min_loop_length)

    primary = traceback_one(table)
    alternatives: tuple[Structure, ...] = ()
    truncated = False
    if enumerate_alternatives:
        all_optimal, truncated = enumerate_optimal(table, limit=alternatives_limit)
        alternatives = tuple(all_optimal[1:])  # index 0 is the primary

    self_check = check_structure(sequence, primary.pairs, min_loop_length)
    return FoldResult(
        sequence=sequence,
        optimum=table.optimum,
        primary=primary,
        alternatives=alternatives,
        alternatives_truncated=truncated,
        table=table,
        self_check=self_check,
        min_loop_length=min_loop_length,
        warnings=MODEL_WARNINGS,
    )
