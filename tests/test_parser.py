"""Tests for sequence parsing and typed failure categories."""
from __future__ import annotations

import pytest

from nussinov_backend.errors import (
    EmptySequenceError,
    InvalidBaseError,
    SequenceTooLongError,
)
from nussinov_backend.parser import parse_sequence


def test_plain_sequence_is_uppercased():
    parsed = parse_sequence("acgu", max_length=10)
    assert parsed.sequence == "ACGU"
    assert parsed.length == 4
    assert parsed.normalized_bases == ()


def test_dna_thymine_is_converted_to_uracil_and_recorded():
    parsed = parse_sequence("GCAT", max_length=10)
    assert parsed.sequence == "GCAU"
    assert parsed.normalized_bases == ((3, "T", "U"),)
    assert parsed.length == 4


def test_whitespace_and_newlines_are_stripped():
    parsed = parse_sequence("GG AA\nCC\tU", max_length=20)
    assert parsed.sequence == "GGAACCU"


def test_fasta_header_and_comments_are_removed():
    fasta = ">synthetic-fixture-1\n; a comment\nGGGAUCC\n"
    parsed = parse_sequence(fasta, max_length=20)
    assert parsed.sequence == "GGGAUCC"
    assert parsed.fasta_header == "synthetic-fixture-1"
    assert parsed.source.endswith(":fasta")


def test_empty_string_raises_typed_error():
    with pytest.raises(EmptySequenceError) as exc:
        parse_sequence("   \n\t", max_length=10)
    assert exc.value.category == "empty_sequence"


def test_invalid_base_reports_position_and_base():
    with pytest.raises(InvalidBaseError) as exc:
        parse_sequence("ACGXU", max_length=10)
    assert exc.value.category == "invalid_base"
    assert exc.value.position == 3
    assert exc.value.base == "X"


def test_digit_and_punctuation_are_rejected():
    with pytest.raises(InvalidBaseError) as exc:
        parse_sequence("ACG.U", max_length=10)
    assert exc.value.position == 3


def test_non_string_input_is_rejected():
    with pytest.raises(InvalidBaseError):
        parse_sequence(12345, max_length=10)  # type: ignore[arg-type]


def test_too_long_sequence_reports_limits():
    with pytest.raises(SequenceTooLongError) as exc:
        parse_sequence("A" * 65, max_length=64)
    err = exc.value
    assert err.category == "sequence_too_long"
    assert err.length == 65
    assert err.max_length == 64


def test_fasta_with_only_header_is_empty_sequence_error():
    with pytest.raises(EmptySequenceError):
        parse_sequence(">nothing here\n; only comments", max_length=10)
