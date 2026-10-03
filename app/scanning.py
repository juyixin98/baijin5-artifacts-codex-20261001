"""Strand-aware sliding-window scanner.

Both strands of every window are scored: the plus strand as-is, the minus
strand by scoring the window's reverse complement with the same PWM. All
coordinates are reported on the plus strand, 0-based half-open [start, end),
so a minus-strand hit at [i, i+k) means the reverse complement of
sequence[i:i+k] matched.

Overlapping windows are scored independently and every result keeps its own
identity (seq_id, start, strand) — nothing is deduplicated or merged.
"""
from __future__ import annotations

from dataclasses import dataclass

from .config import UNKNOWN_BASE
from .errors import UnknownBasePolicyError
from .pwm import PWM

_COMPLEMENT = {"A": "T", "C": "G", "G": "C", "T": "A", UNKNOWN_BASE: UNKNOWN_BASE}

VALID_UNKNOWN_BASE_POLICIES = ("skip", "marginalize")


def reverse_complement(seq: str) -> str:
    return "".join(_COMPLEMENT[ch] for ch in reversed(seq))


@dataclass(frozen=True)
class WindowResult:
    seq_id: str
    start: int          # 0-based, plus-strand coordinates
    end: int            # exclusive
    strand: str         # "+" or "-"
    matched_sequence: str  # the sequence actually scored (RC for minus strand)
    status: str         # "scored" or "skipped"
    score: float | None = None
    reason: str | None = None  # why a window was skipped


def _score_window(
    seq_id: str,
    start: int,
    k: int,
    strand: str,
    oriented: str,
    pwm: PWM,
    unknown_policy: str,
) -> WindowResult:
    has_unknown = UNKNOWN_BASE in oriented
    if has_unknown and unknown_policy == "skip":
        return WindowResult(
            seq_id=seq_id,
            start=start,
            end=start + k,
            strand=strand,
            matched_sequence=oriented,
            status="skipped",
            reason="unknown_base",
        )
    score = pwm.score(oriented, marginalize_unknown=(unknown_policy == "marginalize"))
    return WindowResult(
        seq_id=seq_id,
        start=start,
        end=start + k,
        strand=strand,
        matched_sequence=oriented,
        status="scored",
        score=score,
    )


def scan_sequence(
    seq_id: str,
    sequence: str,
    pwm: PWM,
    unknown_policy: str = "skip",
) -> list[WindowResult]:
    """Score every window on both strands of one parsed sequence."""
    if unknown_policy not in VALID_UNKNOWN_BASE_POLICIES:
        raise UnknownBasePolicyError(
            f"unknown_base_policy must be one of {VALID_UNKNOWN_BASE_POLICIES}, "
            f"got {unknown_policy!r}",
            {"policy": unknown_policy},
        )
    k = pwm.length
    results: list[WindowResult] = []
    for i in range(0, len(sequence) - k + 1):
        window = sequence[i : i + k]
        results.append(_score_window(seq_id, i, k, "+", window, pwm, unknown_policy))
        results.append(
            _score_window(seq_id, i, k, "-", reverse_complement(window), pwm, unknown_policy)
        )
    return results
