"""Tests for mature mass tables: hand sums, unknown vs ambiguous distinction."""

from __future__ import annotations

from app.domain.mass import (
    PROTON_MASS,
    WATER_MASS,
    compute_mass,
    compute_mass_batch,
    mass_table_metadata,
)
from tests.oracle import ORACLE_PROTON, ORACLE_RESIDUE_MASS, ORACLE_WATER


def test_constants_match_test_fixture():
    # Guards the independent test oracle against fixture drift.
    assert WATER_MASS == ORACLE_WATER
    assert PROTON_MASS == ORACLE_PROTON


def test_exact_mass_hand_sum_aak():
    # 2*A + K + H2O = 142.074227610 + 128.094963015 + 18.010564684
    result = compute_mass("AAK")
    assert result.status == "EXACT"
    assert result.neutral_mass == 288.179755
    assert result.mhplus_mz == 289.187032
    assert result.min_neutral_mass == result.max_neutral_mass == 288.179755
    # Explicit re-sum from independent oracle constants.
    expected = round(
        2 * ORACLE_RESIDUE_MASS["A"] + ORACLE_RESIDUE_MASS["K"] + ORACLE_WATER, 6
    )
    assert result.neutral_mass == expected


def test_single_residue_mass_includes_terminal_water():
    result = compute_mass("G")
    expected = round(ORACLE_RESIDUE_MASS["G"] + ORACLE_WATER, 6)
    assert result.neutral_mass == expected


def test_unknown_residue_x_is_not_a_number_and_not_success_mass():
    result = compute_mass("AXA")
    assert result.status == "UNKNOWN"
    assert result.neutral_mass is None
    assert result.mhplus_mz is None
    assert result.min_neutral_mass is None
    assert result.max_neutral_mass is None
    assert result.unknown_positions == [2]
    assert result.ambiguous_positions == []


def test_unsupported_letter_u_is_unknown_state_not_keyerror():
    result = compute_mass("AUA")
    assert result.status == "UNKNOWN"
    assert result.unknown_positions == [2]
    assert result.neutral_mass is None


def test_ambiguous_b_has_bounded_range_hand_computed():
    # B in {D,N}: min uses N (114.042927470), max uses D (115.026943065)
    result = compute_mass("AB")
    assert result.status == "AMBIGUOUS"
    assert result.neutral_mass is None  # no single exact value
    assert result.min_neutral_mass == 203.090606
    assert result.max_neutral_mass == 204.074622
    assert result.min_mhplus_mz == round(203.090605959 + PROTON_MASS, 6)
    assert result.max_mhplus_mz == round(204.074621554 + PROTON_MASS, 6)
    assert result.ambiguous_positions == [2]
    assert result.unknown_positions == []


def test_ambiguous_j_is_isobaric_but_still_flagged_ambiguous():
    result = compute_mass("AJ")
    assert result.status == "AMBIGUOUS"
    assert result.min_neutral_mass == result.max_neutral_mass == 202.131742
    assert result.neutral_mass is None
    assert result.ambiguous_positions == [2]


def test_unknown_takes_presence_over_ambiguity():
    result = compute_mass("XB")
    assert result.status == "UNKNOWN"
    assert result.unknown_positions == [1]
    assert result.ambiguous_positions == [2]
    assert result.min_neutral_mass is None


def test_batch_vector_path_agrees_with_scalar_and_preserves_order():
    seqs = ["AAK", "RPA", "AXA", "AB", "AJ", "G"]
    batch = compute_mass_batch(seqs)
    scalar = [compute_mass(s) for s in seqs]
    assert len(batch) == len(seqs)
    for b, s in zip(batch, scalar):
        assert b.status == s.status
        assert b.neutral_mass == s.neutral_mass
        assert (b.min_neutral_mass, b.max_neutral_mass) == (
            s.min_neutral_mass,
            s.max_neutral_mass,
        )
        assert b.unknown_positions == s.unknown_positions
        assert b.ambiguous_positions == s.ambiguous_positions


def test_metadata_versions_and_token_policy():
    meta = mass_table_metadata()
    assert meta["mass_table_version"] == "mono-residue-v1"
    assert meta["cysteine_state"] == "reduced"
    assert set(meta["ambiguous_tokens"]) == {"B", "Z", "J"}
    assert meta["unknown_token"] == "X"
