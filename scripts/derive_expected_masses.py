#!/usr/bin/env python3
"""Independent expected-mass derivation.

This script does NOT import the digest engine or app.services.mass. It derives
neutral monoisotopic masses directly from the free-amino-acid integer formula
table and atomic weights, for the short, hand-authored peptides used in
tests/expected/expected_cases.json. Its printed values are frozen into that
fixture so tests assert concrete numbers produced by a separate code path.

Usage:
    python scripts/derive_expected_masses.py
"""

from __future__ import annotations

# Local alias of the formula constants only (stoichiometric data, not logic).
from app.domain.constants import ELEMENT_MASSES, ELEMENT_ORDER, RESIDUES

# Hand-authored peptide whose mass is fully worked from first principles:
# glycine free amino acid C2H5NO2 = 75.032028404 Da (documented cross-check).
MANUAL_GLYCINE = 75.032028404

PEPTIDES = [
    "AAK",
    "AAAK",
    "AA",
    "AK",
    "K",
    "A",
    "G",
    "AM",
    "AFPW",
    "D",
    "AD",
    "DAA",
    "AKPAA",
    "PF",
]


def residue_mass(letter: str) -> float:
    formula = RESIDUES[letter].formula  # free amino acid
    mass = 0.0
    for count, element in zip(formula, ELEMENT_ORDER):
        mass += count * ELEMENT_MASSES[element]
    # residue = free amino acid - H2O
    mass -= 2 * ELEMENT_MASSES["H"] + ELEMENT_MASSES["O"]
    return mass


def neutral_mass(sequence: str) -> float:
    return sum(residue_mass(ch) for ch in sequence) + (
        2 * ELEMENT_MASSES["H"] + ELEMENT_MASSES["O"]
    )


def main() -> None:
    print("# Independent neutral monoisotopic masses (Da)")
    print(f"# glycine free AA cross-check: {MANUAL_GLYCINE:.9f} "
          f"(formula gives {sum(2 * [ELEMENT_MASSES['C']]) + 5 * ELEMENT_MASSES['H'] + ELEMENT_MASSES['N'] + 2 * ELEMENT_MASSES['O']:.9f})")
    for peptide in PEPTIDES:
        print(f"{peptide:6s} {neutral_mass(peptide):.9f}")


if __name__ == "__main__":
    main()
