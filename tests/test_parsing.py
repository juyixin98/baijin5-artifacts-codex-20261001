"""Synthetic sequence parsing: valid inputs and every declared error."""

from __future__ import annotations

import pytest

from app.errors import ErrorCategory, InputValidationError
from app.parsing import p_distance_matrix, parse_fasta


def test_parse_valid_fasta():
    records = parse_fasta(">a\nACGT\n>b\nAC\nGT\n")
    assert [r.label for r in records] == ["a", "b"]
    assert records[1].sequence == "ACGT"  # multi-line sequences are joined


def test_empty_input_rejected():
    with pytest.raises(InputValidationError) as exc:
        parse_fasta("   \n  ")
    assert exc.value.category is ErrorCategory.INPUT_VALIDATION


def test_sequence_before_header_rejected():
    with pytest.raises(InputValidationError) as exc:
        parse_fasta("ACGT\n>a\nACGT\n")
    assert "before the first FASTA header" in exc.value.message


def test_empty_header_label_rejected():
    with pytest.raises(InputValidationError):
        parse_fasta(">\nACGT\n")


def test_duplicate_label_rejected():
    with pytest.raises(InputValidationError) as exc:
        parse_fasta(">a\nACGT\n>a\nTTTT\n")
    assert "duplicate" in exc.value.message


def test_invalid_character_rejected():
    with pytest.raises(InputValidationError) as exc:
        parse_fasta(">a\nACGX\n")
    assert exc.value.details["invalid_characters"] == ["X"]


def test_sequence_without_residues_rejected():
    with pytest.raises(InputValidationError):
        parse_fasta(">a\n>b\nACGT\n")


def test_no_records_rejected():
    with pytest.raises(InputValidationError):
        parse_fasta("")


def test_unequal_lengths_rejected():
    records = parse_fasta(">a\nACGT\n>b\nACG\n")
    with pytest.raises(InputValidationError) as exc:
        p_distance_matrix(records)
    assert exc.value.details["lengths"] == {"a": 4, "b": 3}


def test_single_sequence_rejected():
    records = parse_fasta(">a\nACGT\n")
    with pytest.raises(InputValidationError):
        p_distance_matrix(records)


def test_p_distance_values_hand_computed():
    # alpha vs gamma: 2 mismatches of 4 -> 0.5; alpha vs delta: 4 of 4 -> 1.
    records = parse_fasta(">alpha\nAAAA\n>gamma\nAACC\n>delta\nCCCC\n")
    labels, matrix = p_distance_matrix(records)
    assert labels == ["alpha", "gamma", "delta"]
    assert matrix == [
        [0.0, 0.5, 1.0],
        [0.5, 0.0, 0.5],
        [1.0, 0.5, 0.0],
    ]
