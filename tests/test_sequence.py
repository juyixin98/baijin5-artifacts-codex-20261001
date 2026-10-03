"""Sequence parsing: normalisation rules and failure categories."""

import pytest

from app.errors import DomainError, ErrorCategory
from app.sequence import parse_sequences


def test_raw_sequence_is_uppercased_and_named():
    (rec,) = parse_sequences("acgtACGT")
    assert rec.seq_id == "seq1"
    assert rec.bases == "ACGTACGT"
    assert rec.length == 8


def test_fasta_multiple_records_and_multiline():
    records = parse_sequences(">alpha\nACGT\nAC\n>beta\nTT\nGG\n")
    assert [r.seq_id for r in records] == ["alpha", "beta"]
    assert records[0].bases == "ACGTAC"
    assert records[1].bases == "TTGG"


def test_ambiguity_letters_become_unknown_n():
    (rec,) = parse_sequences("ACNRYSWKMBDHVGT")
    assert rec.bases == "AC" + "N" * 11 + "GT"


def test_non_letter_character_is_invalid_sequence():
    with pytest.raises(DomainError) as excinfo:
        parse_sequences("ACG1AC")
    assert excinfo.value.category is ErrorCategory.INVALID_SEQUENCE
    assert excinfo.value.detail["offset"] == 3


def test_non_iupac_letter_is_invalid_sequence():
    with pytest.raises(DomainError) as excinfo:
        parse_sequences("ACGU")  # RNA base is not accepted as DNA
    assert excinfo.value.category is ErrorCategory.INVALID_SEQUENCE


def test_empty_input_is_invalid_sequence():
    with pytest.raises(DomainError) as excinfo:
        parse_sequences("   \n ")
    assert excinfo.value.category is ErrorCategory.INVALID_SEQUENCE


def test_empty_fasta_record_is_invalid_sequence():
    with pytest.raises(DomainError) as excinfo:
        parse_sequences(">empty\n>full\nACGT\n")
    assert excinfo.value.category is ErrorCategory.INVALID_SEQUENCE
