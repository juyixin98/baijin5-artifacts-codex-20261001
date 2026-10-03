"""Parsing boundary tests: alphabet, alignment length, FASTA fixtures."""
import pytest

from seqdist.errors import ErrorCategory, InputValidationError
from seqdist.parsing import parse_fasta, parse_fasta_pair, parse_pair


def test_parse_pair_uppercases_and_accepts_full_alphabet():
    pair = parse_pair("acgtnry-", "ACGTNR Y-".replace(" ", ""))
    assert pair.seq1 == "ACGTNRY-"
    assert pair.alignment_length == 8


def test_parse_pair_rejects_invalid_characters_with_positions_class():
    with pytest.raises(InputValidationError) as excinfo:
        parse_pair("ACGTX", "ACGTA")
    err = excinfo.value
    assert err.category is ErrorCategory.INPUT_ERROR
    assert err.detail["invalid_characters"] == ["X"]


def test_parse_pair_rejects_unequal_lengths():
    with pytest.raises(InputValidationError) as excinfo:
        parse_pair("ACGT", "ACG")
    assert excinfo.value.category is ErrorCategory.INPUT_ERROR
    assert excinfo.value.detail == {"len_seq1": 4, "len_seq2": 3}


def test_parse_pair_rejects_empty_sequence():
    with pytest.raises(InputValidationError):
        parse_pair("", "ACGT")


def test_parse_fasta_two_records():
    text = ">seqA\nACGT\nACGT\n>seqB\nACGA\n"
    records = parse_fasta(text)
    assert records == [("seqA", "ACGTACGT"), ("seqB", "ACGA")]


def test_parse_fasta_pair_requires_exactly_two_records():
    with pytest.raises(InputValidationError) as excinfo:
        parse_fasta_pair(">a\nACGT\n>b\nACGT\n>c\nACGT\n")
    assert excinfo.value.detail["n_records"] == 3


def test_parse_fasta_rejects_data_before_header():
    with pytest.raises(InputValidationError):
        parse_fasta("ACGT\n>a\nACGT\n")


def test_parse_fasta_rejects_headerless_name_and_empty_record():
    with pytest.raises(InputValidationError):
        parse_fasta(">\nACGT\n")
    with pytest.raises(InputValidationError):
        parse_fasta(">a\n>b\nACGT\n")
