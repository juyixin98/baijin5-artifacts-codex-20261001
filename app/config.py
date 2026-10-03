"""Runtime configuration.

All values are declarable via environment variables (prefix ``MOTIFSCAN_``)
and are snapshotted into every provenance record so a stored result always
carries the configuration that produced it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# Canonical base order used everywhere (PWM columns, background vector).
ALPHABET = "ACGT"

DEFAULT_BACKGROUND = {"A": 0.25, "C": 0.25, "G": 0.25, "T": 0.25}

#: Policies for windows containing unknown (non-ACGT) bases.
UNKNOWN_POLICIES = ("skip", "marginalize")


@dataclass(frozen=True)
class Settings:
    # Declared background model. All thresholds / p-values are calibrated
    # against exactly this distribution.
    background: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_BACKGROUND))
    # Pseudocount added (background-weighted) when turning counts into
    # probabilities: p'_{i,b} = (n_{i,b} + pc * bg_b) / (N_i + pc).
    pseudocount: float = 1.0
    # Exact enumeration is 4^k; k is capped so enumeration stays feasible.
    max_motif_length: int = 10
    # How windows containing unknown bases are handled by default.
    unknown_policy: str = "skip"
    # Decimals used when aggregating equal scores in the exact distribution.
    score_round_decimals: int = 9
    # Default p-value threshold when a request specifies no threshold at all.
    default_pvalue_threshold: float = 0.05
    # SQLite provenance database path.
    db_path: str = "motifscan.db"


def load_settings() -> Settings:
    """Build settings from environment variables, falling back to defaults."""
    bg = dict(DEFAULT_BACKGROUND)
    for base in ALPHABET:
        raw = os.environ.get(f"MOTIFSCAN_BG_{base}")
        if raw is not None:
            bg[base] = float(raw)
    return Settings(
        background=bg,
        pseudocount=float(os.environ.get("MOTIFSCAN_PSEUDOCOUNT", "1.0")),
        max_motif_length=int(os.environ.get("MOTIFSCAN_MAX_MOTIF_LENGTH", "10")),
        unknown_policy=os.environ.get("MOTIFSCAN_UNKNOWN_POLICY", "skip"),
        score_round_decimals=int(os.environ.get("MOTIFSCAN_SCORE_ROUND_DECIMALS", "9")),
        default_pvalue_threshold=float(os.environ.get("MOTIFSCAN_DEFAULT_PVALUE_THRESHOLD", "0.05")),
        db_path=os.environ.get("MOTIFSCAN_DB_PATH", "motifscan.db"),
    )
