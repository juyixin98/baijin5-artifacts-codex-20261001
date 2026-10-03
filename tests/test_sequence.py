"""Sequence parsing: raw and FASTA input, declared failure categories."""
import pytest

from app.errors import EmptySequenceError, FastaFormatError, InvalidCharacterError
from app.sequence import parse_fasta, parse_sequence


def test_parse_raw_normalizes_case_and_whitespace():
    parsed = parse_sequence("s1", "  ac\ngt ")
    assert parsed.sequence == "ACGT"
    assert parsed.length == 4


def test_parse_allows_unknown_base_n():
    assert parse_sequence("s1", "ACNT").sequence == "ACNT"


def test_invalid_character_reports_position():
    with pytest.raises(InvalidCharacterError) as excinfo:
        parse_sequence("s1", "ACXT")
    assert excinfo.value.category == "invalid_character"
    assert excinfo.value.details["position"] == 2
    assert excinfo.value.details["character"] == "X"


def test_empty_sequence_rejected():
    with pytest.raises(EmptySequenceError) as excinfo:
        parse_sequence("s1", "   ")
    assert excinfo.value.category == "empty_sequence"


def test_parse_fasta_multiple_records():
    records = parse_fasta(">alpha description words\nACGT\n>beta\nTT\nTT\n")
    assert [r.id for r in records] == ["alpha", "beta"]
    assert records[0].sequence == "ACGT"
    assert records[1].sequence == "TTTT"


def test_fasta_data_before_header_rejected():
    with pytest.raises(FastaFormatError) as excinfo:
        parse_fasta("ACGT\n>beta\nTT\n")
    assert excinfo.value.category == "fasta_format_error"


def test_fasta_no_records_rejected():
    with pytest.raises(EmptySequenceError):
        parse_fasta("")
