"""Unit tests for the strict FASTA alignment parser."""

import pytest
from conftest import judgement, read_fixture

from msa_backend.errors import (
    AlignmentShapeError,
    FastaParseError,
    InvalidResidueError,
)
from msa_backend.parsing.fasta import parse_fasta_alignment


def test_parse_valid_alignment():
    alignment = parse_fasta_alignment(read_fixture("conserved.fa"))
    judgement("conserved.fa", "parse", "fixture has 4 records of 12 columns")
    assert alignment.n_sequences == 4
    assert alignment.n_columns == 12
    assert alignment.sequence_ids == ("s1", "s2", "s3", "s4")


def test_empty_input_raises_fasta_parse_error():
    with pytest.raises(FastaParseError) as excinfo:
        parse_fasta_alignment("   \n  ")
    judgement("empty-input", "parse", "category must be fasta_parse_error")
    assert excinfo.value.category == "fasta_parse_error"


def test_sequence_before_header_raises():
    with pytest.raises(FastaParseError):
        parse_fasta_alignment("ACGT\n>s1\nACGT\n")


def test_record_without_sequence_raises():
    with pytest.raises(FastaParseError):
        parse_fasta_alignment(">s1\n>s2\nACGT\n")


def test_non_rectangular_alignment_raises_shape_error():
    text = ">s1\nACGT\n>s2\nACG\n"
    with pytest.raises(AlignmentShapeError) as excinfo:
        parse_fasta_alignment(text)
    judgement("ragged-input", "shape-check", "row lengths {4, 3} must be rejected")
    assert excinfo.value.category == "alignment_shape_error"


def test_single_sequence_raises_shape_error():
    with pytest.raises(AlignmentShapeError):
        parse_fasta_alignment(">s1\nACGT\n")


def test_invalid_residue_raises_with_location():
    text = ">s1\nACGT\n>s2\nACXT\n"
    with pytest.raises(InvalidResidueError) as excinfo:
        parse_fasta_alignment(text)
    judgement("bad-residue", "alphabet-check", "X is not IUPAC DNA; error must name sequence and column")
    assert excinfo.value.category == "invalid_residue_error"
    assert "s2" in str(excinfo.value) and "column 3" in str(excinfo.value)


def test_duplicate_identifiers_raise():
    text = ">s1\nACGT\n>s1\nACGA\n"
    with pytest.raises(FastaParseError):
        parse_fasta_alignment(text)


def test_lowercase_and_multiline_sequences_are_accepted():
    text = ">s1 desc words\nac\ngt\n>s2\nAC\nGT\n"
    alignment = parse_fasta_alignment(text)
    judgement("lowercase-multiline", "parse", "case normalised to upper, chunks concatenated")
    assert alignment.rows == ("ACGT", "ACGT")
    assert alignment.sequence_ids == ("s1", "s2")
