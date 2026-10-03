"""Tests for sequence parsing/validation: input contract and error categories."""

from __future__ import annotations

import pytest

from app.errors import ErrorCategory, InputValidationError, ResourceExhaustedError
from app.sequences import (
    MAX_SEQUENCE_LENGTH,
    MAX_SEQUENCES,
    parse_fasta,
    validate_alignment,
)


class TestValidateAlignment:
    def test_normalizes_case_and_whitespace(self):
        ds = validate_alignment(["s1", "s2"], ["ac gt", "ACGT"])
        assert ds.sequences == ("ACGT", "ACGT")
        assert ds.length == 4
        assert ds.ids == ("s1", "s2")

    def test_rejects_unequal_lengths(self):
        with pytest.raises(InputValidationError) as exc:
            validate_alignment(["a", "b"], ["ACGT", "ACG"])
        assert exc.value.code == "unequal_sequence_lengths"
        assert exc.value.category is ErrorCategory.INPUT_VALIDATION

    def test_rejects_duplicate_ids(self):
        with pytest.raises(InputValidationError) as exc:
            validate_alignment(["x", "x"], ["AC", "AC"])
        assert exc.value.code == "duplicate_sequence_id"

    def test_rejects_empty_id_and_sequence(self):
        with pytest.raises(InputValidationError):
            validate_alignment(["", "b"], ["AC", "AC"])
        with pytest.raises(InputValidationError):
            validate_alignment(["a", "b"], ["AC", "  "])

    def test_rejects_too_few_and_too_many_sequences(self):
        with pytest.raises(InputValidationError):
            validate_alignment(["only"], ["AC"])
        with pytest.raises(InputValidationError):
            validate_alignment([f"s{i}" for i in range(MAX_SEQUENCES + 1)],
                               ["AC"] * (MAX_SEQUENCES + 1))

    def test_overlong_sequence_is_resource_exhaustion_not_validation(self):
        with pytest.raises(ResourceExhaustedError) as exc:
            validate_alignment(["a", "b"], ["A" * (MAX_SEQUENCE_LENGTH + 1), "AC"])
        assert exc.value.category is ErrorCategory.RESOURCE_EXHAUSTED
        assert exc.value.code == "sequence_too_long"


class TestParseFasta:
    def test_multiline_records(self):
        ds = parse_fasta(">seq one\nAC\nGT\n>seq2\nAC\nGT\n")
        assert ds.ids == ("seq one", "seq2")
        assert ds.sequences == ("ACGT", "ACGT")

    def test_data_before_header_rejected(self):
        with pytest.raises(InputValidationError) as exc:
            parse_fasta("ACGT\n>s\nACGT\n")
        assert exc.value.code == "fasta_missing_header"

    def test_empty_fasta_rejected(self):
        with pytest.raises(InputValidationError) as exc:
            parse_fasta("\n\n")
        assert exc.value.code == "fasta_empty"

    def test_unequal_record_lengths_rejected(self):
        with pytest.raises(InputValidationError) as exc:
            parse_fasta(">a\nACGT\n>b\nACG\n")
        assert exc.value.code == "unequal_sequence_lengths"
