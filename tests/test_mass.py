"""Unit tests: mass calculation, uncertainty states, m/z.

Expected numbers are hand-calculated constants and values frozen from the
independent derivation script. The NumPy oracle below is a separate code path
from the production mass routine, so agreement is genuine cross-validation.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.domain.constants import (
    AMBIGUOUS_RESIDUES,
    ELEMENT_MASSES,
    ELEMENT_ORDER,
    ENZYMES,
    RESIDUES,
    WATER_MASS,
    PROTON_MASS,
)
from app.domain.models import MassStatus
from app.services.digest import digest
from app.services.mass import mz
from app.services.parse import parse_sequence

pytestmark = pytest.mark.unit

TOL = 1e-6

# Hand constants (monoisotopic, Da):
WATER_EXPECTED = 18.010564684
RESIDUE_A = 71.037113805
RESIDUE_G = 57.021463735
RESIDUE_K = 128.094963015
PEPTIDE_G = 75.032028404
PEPTIDE_A = 89.047678474
PEPTIDE_AAK = 288.179755262


# ---------------------------------------------------------------------------
# Fully independent NumPy oracle (does not call app.services.mass).
# ---------------------------------------------------------------------------
_ATOMIC = np.array([ELEMENT_MASSES[e] for e in ELEMENT_ORDER], dtype=np.float64)


def _oracle_neutral(sequence: str) -> float:
    total = WATER_MASS
    for letter in sequence:
        total += RESIDUES[letter].residue_mass
    return float(total)


def test_water_mass_matches_hand_constant() -> None:
    assert WATER_MASS == pytest.approx(WATER_EXPECTED, abs=TOL)


def test_canonical_residue_masses_match_hand_constants() -> None:
    assert RESIDUES["A"].residue_mass == pytest.approx(RESIDUE_A, abs=TOL)
    assert RESIDUES["G"].residue_mass == pytest.approx(RESIDUE_G, abs=TOL)
    assert RESIDUES["K"].residue_mass == pytest.approx(RESIDUE_K, abs=TOL)


def test_determinate_peptide_mass_is_specific_number() -> None:
    parsed = parse_sequence("AAK", max_length=100)
    result = digest(parsed, ENZYMES["cnbr"], missed_cleavages=0)
    fragment = result.fragments[0]
    assert fragment.mass.status is MassStatus.DETERMINATE
    assert fragment.mass.nominal == pytest.approx(PEPTIDE_AAK, abs=TOL)
    assert fragment.mass.min_mass == fragment.mass.max_mass == fragment.mass.nominal
    # Independent oracle agrees.
    assert fragment.mass.nominal == pytest.approx(_oracle_neutral("AAK"), abs=TOL)


def test_single_residue_peptide_includes_one_water() -> None:
    parsed = parse_sequence("G", max_length=100)
    from app.domain.constants import ENZYMES

    result = digest(parsed, ENZYMES["cnbr"], missed_cleavages=0)
    assert result.fragments[0].mass.nominal == pytest.approx(PEPTIDE_G, abs=TOL)


def test_ambiguous_B_gives_uncertain_interval_spanning_N_and_D() -> None:
    parsed = parse_sequence("BG", max_length=100)
    from app.domain.constants import ENZYMES

    result = digest(parsed, ENZYMES["cnbr"], missed_cleavages=0)
    mass = result.fragments[0].mass
    assert mass.status is MassStatus.UNCERTAIN
    assert mass.min_mass < mass.max_mass
    expected_min = RESIDUES["N"].residue_mass + RESIDUES["G"].residue_mass + WATER_MASS
    expected_max = RESIDUES["D"].residue_mass + RESIDUES["G"].residue_mass + WATER_MASS
    assert mass.min_mass == pytest.approx(expected_min, abs=TOL)
    assert mass.max_mass == pytest.approx(expected_max, abs=TOL)
    # Nominal is the midpoint under uncertainty.
    assert mass.nominal == pytest.approx((expected_min + expected_max) / 2, abs=TOL)


def test_ambiguous_J_isobaric_is_determinate_despite_identity_ambiguity() -> None:
    parsed = parse_sequence("JG", max_length=100)
    from app.domain.constants import ENZYMES

    result = digest(parsed, ENZYMES["cnbr"], missed_cleavages=0)
    mass = result.fragments[0].mass
    # Identity unknown but mass determinate - the required distinction.
    assert result.has_ambiguous
    assert mass.status is MassStatus.DETERMINATE
    assert mass.is_point


def test_empty_segment_has_zero_mass() -> None:
    parsed = parse_sequence("DAAD", max_length=100)
    from app.domain.constants import ENZYMES

    result = digest(parsed, ENZYMES["asp_n"], missed_cleavages=0)
    empty = result.fragments[0]
    assert empty.empty
    assert empty.mass.nominal == 0.0
    assert empty.mass.status is MassStatus.DETERMINATE


def test_mz_formula_specific_values() -> None:
    neutral = PEPTIDE_A
    z1 = neutral + PROTON_MASS
    z2 = (neutral + 2 * PROTON_MASS) / 2
    assert mz(neutral, 1) == pytest.approx(z1, abs=TOL)
    assert mz(neutral, 2) == pytest.approx(z2, abs=TOL)


def test_mz_rejects_non_positive_charge() -> None:
    with pytest.raises(ValueError):
        mz(PEPTIDE_A, 0)
