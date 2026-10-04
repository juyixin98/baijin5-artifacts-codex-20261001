"""Mature-peptide monoisotopic mass tables.

Scope and key trade-offs
------------------------
* Residue masses are **monoisotopic residue masses** (free amino-acid mass
  minus water), in daltons. Cysteine is treated as *reduced* (no
  carbamidomethylation or any other PTM).
* A mature neutral peptide mass is ``sum(residue masses) + H2O``; the
  singly protonated ion is ``neutral + PROTON`` (m/z at z = 1).
* **Unknown vs ambiguous is explicit and never collapsed to success:**
    - ``X`` (any/unknown residue): no candidate masses at all. The fragment
      mass status is ``UNKNOWN`` and every numeric mass is ``null``.
    - ``B`` (Asp/Asn), ``Z`` (Glu/Gln), ``J`` (Leu/Ile): the mass is bounded.
      Status is ``AMBIGUOUS`` with ``min_*`` / ``max_*`` values (J's bounds
      coincide because Leu and Ile are isobaric, but the token identity is
      still flagged).
    - Standard 20 residues: status ``EXACT``.
* NumPy is used for the batch path: a fragment set is encoded as an integer
  index matrix and summed vectorially, with a dedicated masked channel for
  unknown / ambiguous positions so bounds are computed without Python loops.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from app.domain.parsing import UNKNOWN_OR_AMBIGUOUS_TOKENS

MassStatus = Literal["EXACT", "AMBIGUOUS", "UNKNOWN"]

# Monoisotopic residue masses [Da] (reduced cysteine). Source: local fixed
# synthetic fixture table; versioned via MASS_TABLE_VERSION.
RESIDUE_MASSES: dict[str, float] = {
    "A": 71.037113805,
    "R": 156.101111050,
    "N": 114.042927470,
    "D": 115.026943065,
    "C": 103.009184505,
    "E": 129.042593135,
    "Q": 128.058577540,
    "G": 57.021463735,
    "H": 137.058911875,
    "I": 113.084063975,
    "L": 113.084063975,
    "K": 128.094963015,
    "M": 131.040484645,
    "F": 147.068413945,
    "P": 97.052763875,
    "S": 87.032028435,
    "T": 101.047678505,
    "W": 186.079312980,
    "Y": 163.063328575,
    "V": 99.068413945,
}

# Bounded ambiguity tokens -> concrete candidate residue identities.
AMBIGUITY_GROUPS: dict[str, tuple[str, ...]] = {
    "B": ("D", "N"),
    "Z": ("E", "Q"),
    "J": ("I", "L"),
}
UNKNOWN_TOKEN = "X"

WATER_MASS = 18.010564684
PROTON_MASS = 1.007276466621


def _is_unknown_token(ch: str) -> bool:
    """True for X or any legal letter with no mass and no ambiguity group (U, O)."""
    return ch == UNKNOWN_TOKEN or (
        ch not in RESIDUE_MASSES and ch not in AMBIGUITY_GROUPS
    )


@dataclass(frozen=True)
class MassResult:
    status: MassStatus
    neutral_mass: float | None
    min_neutral_mass: float | None
    max_neutral_mass: float | None
    mhplus_mz: float | None
    min_mhplus_mz: float | None
    max_mhplus_mz: float | None
    unknown_positions: list[int] = field(default_factory=list)
    ambiguous_positions: list[int] = field(default_factory=list)

    def rounded(self, decimals: int) -> "MassResult":
        def r(value: float | None) -> float | None:
            return round(value, decimals) if value is not None else None

        return MassResult(
            status=self.status,
            neutral_mass=r(self.neutral_mass),
            min_neutral_mass=r(self.min_neutral_mass),
            max_neutral_mass=r(self.max_neutral_mass),
            mhplus_mz=r(self.mhplus_mz),
            min_mhplus_mz=r(self.min_mhplus_mz),
            max_mhplus_mz=r(self.max_mhplus_mz),
            unknown_positions=list(self.unknown_positions),
            ambiguous_positions=list(self.ambiguous_positions),
        )

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "neutral_mass": self.neutral_mass,
            "min_neutral_mass": self.min_neutral_mass,
            "max_neutral_mass": self.max_neutral_mass,
            "mhplus_mz": self.mhplus_mz,
            "min_mhplus_mz": self.min_mhplus_mz,
            "max_mhplus_mz": self.max_mhplus_mz,
            "unknown_positions": self.unknown_positions,
            "ambiguous_positions": self.ambiguous_positions,
        }


def _single_mass(seq: str) -> MassResult:
    """Scalar reference implementation (also used to anchor the vector path)."""
    unknown_positions = [
        i for i, ch in enumerate(seq, start=1) if _is_unknown_token(ch)
    ]
    ambiguous_positions = [
        i
        for i, ch in enumerate(seq, start=1)
        if ch in AMBIGUITY_GROUPS
    ]

    if unknown_positions:
        return MassResult(
            status="UNKNOWN",
            neutral_mass=None,
            min_neutral_mass=None,
            max_neutral_mass=None,
            mhplus_mz=None,
            min_mhplus_mz=None,
            max_mhplus_mz=None,
            unknown_positions=unknown_positions,
            ambiguous_positions=ambiguous_positions,
        )

    exact_sum = 0.0
    min_extra = 0.0
    max_extra = 0.0
    for ch in seq:
        if ch in AMBIGUITY_GROUPS:
            candidates = [RESIDUE_MASSES[c] for c in AMBIGUITY_GROUPS[ch]]
            min_extra += min(candidates)
            max_extra += max(candidates)
        else:
            exact_sum += RESIDUE_MASSES[ch]

    if ambiguous_positions:
        min_neutral = exact_sum + min_extra + WATER_MASS
        max_neutral = exact_sum + max_extra + WATER_MASS
        return MassResult(
            "AMBIGUOUS",
            neutral_mass=None,
            min_neutral_mass=min_neutral,
            max_neutral_mass=max_neutral,
            mhplus_mz=None,
            min_mhplus_mz=min_neutral + PROTON_MASS,
            max_mhplus_mz=max_neutral + PROTON_MASS,
            unknown_positions=[],
            ambiguous_positions=ambiguous_positions,
        )

    neutral = exact_sum + WATER_MASS
    return MassResult(
        "EXACT",
        neutral_mass=neutral,
        min_neutral_mass=neutral,
        max_neutral_mass=neutral,
        mhplus_mz=neutral + PROTON_MASS,
        min_mhplus_mz=neutral + PROTON_MASS,
        max_mhplus_mz=neutral + PROTON_MASS,
    )


def compute_mass(seq: str, decimals: int = 6) -> MassResult:
    """Mature mass for one fragment; positions are 1-based within the fragment."""
    return _single_mass(seq).rounded(decimals)


def compute_mass_batch(sequences: list[str], decimals: int = 6) -> list[MassResult]:
    """Vectorized mass table for a batch of fragments.

    The matrix holds an exact mass per cell plus per-cell ``min_extra`` /
    ``max_extra`` deltas for ambiguous tokens; an unknown mask invalidates the
    whole row. Results are checked against the scalar implementation for the
    exact/ambiguous sums to keep the two paths honest.
    """
    if not sequences:
        return []

    width = max(len(s) for s in sequences)
    exact = np.zeros((len(sequences), width), dtype=np.float64)
    min_delta = np.zeros((len(sequences), width), dtype=np.float64)
    max_delta = np.zeros((len(sequences), width), dtype=np.float64)
    unknown_mask = np.zeros((len(sequences), width), dtype=bool)
    ambig_mask = np.zeros((len(sequences), width), dtype=bool)

    for row, seq in enumerate(sequences):
        for col, ch in enumerate(seq):
            if _is_unknown_token(ch):
                unknown_mask[row, col] = True
            elif ch in AMBIGUITY_GROUPS:
                candidates = [RESIDUE_MASSES[c] for c in AMBIGUITY_GROUPS[ch]]
                exact[row, col] = min(candidates)
                min_delta[row, col] = 0.0
                max_delta[row, col] = max(candidates) - min(candidates)
                ambig_mask[row, col] = True
            else:
                exact[row, col] = RESIDUE_MASSES[ch]

    sums = exact.sum(axis=1)
    lo = (sums + min_delta.sum(axis=1) + WATER_MASS)
    hi = (sums + max_delta.sum(axis=1) + WATER_MASS)
    has_unknown = unknown_mask.any(axis=1)
    has_ambig = ambig_mask.any(axis=1)

    results: list[MassResult] = []
    for row, seq in enumerate(sequences):
        if has_unknown[row]:
            result = _single_mass(seq)  # authoritatively supplies positions
        elif has_ambig[row]:
            result = MassResult(
                "AMBIGUOUS", None, float(lo[row]), float(hi[row]),
                None,
                float(lo[row] + PROTON_MASS),
                float(hi[row] + PROTON_MASS),
                [],
                [i for i, ch in enumerate(seq, 1) if ch in AMBIGUITY_GROUPS],
            )
        else:
            neutral = float(sums[row]) + WATER_MASS
            result = MassResult(
                "EXACT", neutral, neutral, neutral,
                neutral + PROTON_MASS,
                neutral + PROTON_MASS,
                neutral + PROTON_MASS,
            )
        results.append(result.rounded(decimals))
    return results


def mass_table_metadata() -> dict:
    return {
        "mass_table_version": "mono-residue-v1",
        "water_mass": WATER_MASS,
        "proton_mass": PROTON_MASS,
        "cysteine_state": "reduced",
        "post_translational_modifications": "none",
        "known_residues": "".join(sorted(RESIDUE_MASSES)),
        "ambiguous_tokens": {
            token: candidates for token, candidates in AMBIGUITY_GROUPS.items()
        },
        "unknown_token": UNKNOWN_TOKEN,
        "supported_ambiguity_tokens": "".join(sorted(UNKNOWN_OR_AMBIGUOUS_TOKENS)),
    }
