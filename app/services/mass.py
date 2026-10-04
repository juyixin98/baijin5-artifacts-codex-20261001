"""Charge-state m/z conversion.

Neutral fragment masses (determinate or uncertainty intervals) are produced by
the digest engine from residue annotations; this module holds the single
ionization formula shared across the service.
"""

from __future__ import annotations

from app.domain.constants import PROTON_MASS


def mz(neutral_mass: float, charge: int, proton_mass: float = PROTON_MASS) -> float:
    """Protonated ion m/z for a positive integer charge state."""
    if charge < 1:
        raise ValueError("charge must be a positive integer")
    return (neutral_mass + charge * proton_mass) / charge
