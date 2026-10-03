"""Consensus calling with an explicit coverage floor.

A column may only be called ``conserved`` when BOTH:
  * the top residue frequency reaches ``conservation_threshold``, and
  * ``effective_coverage`` (total non-gap weight) reaches
    ``min_effective_coverage``.

Below the coverage floor the column is reported as
``insufficient_coverage`` with consensus ``?`` -- low evidence never
produces a strong conservation conclusion, no matter how skewed the few
observations are.
"""

from __future__ import annotations

from .types import STATUS_CONSERVED, STATUS_INSUFFICIENT_COVERAGE, STATUS_VARIABLE

CONSENSUS_UNKNOWN = "?"


def call_consensus(
    distribution: dict[str, float],
    effective_coverage: float,
    conservation_threshold: float,
    min_effective_coverage: float,
) -> tuple[str, str]:
    """Return (consensus_symbol, status) for one column."""
    if effective_coverage < min_effective_coverage:
        return CONSENSUS_UNKNOWN, STATUS_INSUFFICIENT_COVERAGE

    top_base = max(distribution, key=lambda b: distribution[b])
    top_frequency = distribution[top_base]
    if top_frequency >= conservation_threshold:
        return top_base, STATUS_CONSERVED
    return top_base.lower(), STATUS_VARIABLE
