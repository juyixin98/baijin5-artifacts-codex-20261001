"""Synthetic sequence parsing: valid input normalized, invalid rejected."""

import pytest

from app.errors import InvalidSequenceError
from app.sequence import SequenceRecord, parse_fasta


def test_record_uppercases_and_validates():
    rec = SequenceRecord(name="r1", sequence="acgt")
    assert rec.sequence == "ACGT"
    assert len(rec) == 4


def test_invalid_base_reports_position():
    with pytest.raises(InvalidSequenceError) as excinfo:
        SequenceRecord(name="bad", sequence="ACGN")
    assert excinfo.value.category == "INVALID_SEQUENCE"
    assert excinfo.value.detail["position"] == 3
    assert excinfo.value.detail["base"] == "N"


def test_empty_sequence_rejected():
    with pytest.raises(InvalidSequenceError):
        SequenceRecord(name="empty", sequence="")


def test_empty_name_rejected():
    with pytest.raises(InvalidSequenceError):
        SequenceRecord(name="", sequence="ACGT")


def test_parse_fasta_two_records():
    records = parse_fasta(">a\nACGT\n\n>b\nTT\nAA\n")
    assert [(r.name, r.sequence) for r in records] == [
        ("a", "ACGT"),
        ("b", "TTAA"),
    ]


def test_parse_fasta_data_before_header_rejected():
    with pytest.raises(InvalidSequenceError) as excinfo:
        parse_fasta("ACGT\n>a\nTT\n")
    assert excinfo.value.category == "INVALID_SEQUENCE"
    assert excinfo.value.detail["line"] == 1


def test_parse_fasta_no_records_rejected():
    with pytest.raises(InvalidSequenceError):
        parse_fasta("\n\n")


def test_parse_fasta_empty_header_rejected():
    with pytest.raises(InvalidSequenceError):
        parse_fasta(">\nACGT\n")
