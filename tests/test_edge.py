"""Edge cases: parsing helpers and remaining failure categories."""
from __future__ import annotations

import pytest

from miniseed.errors import ErrorCode, MiniseedError
from miniseed.sequence import (
    canonicalize,
    parse_fasta_records,
    parse_sequence,
    reverse_complement,
)


def test_parse_accepts_lowercase_and_whitespace_and_headers():
    p = parse_sequence(">h\nacgt acgt\n")
    assert p.sequence == "ACGTACGT"
    assert p.header_lines == 1
    assert p.n_count == 0


def test_parse_counts_n_bases():
    p = parse_sequence("acgNn")
    assert p.sequence == "ACGNN"
    assert p.n_count == 2


def test_parse_null_is_empty_sequence_category():
    with pytest.raises(MiniseedError) as exc:
        parse_sequence(None)  # type: ignore[arg-type]
    assert exc.value.code is ErrorCode.EMPTY_SEQUENCE


def test_parse_fasta_multiple_records():
    doc = ">one\nACGT\nACGT\n>two\nTTTT\n"
    records = parse_fasta_records(doc)
    assert records == [("one", "ACGTACGT"), ("two", "TTTT")]


def test_parse_fasta_headerless_document_is_single_record():
    assert parse_fasta_records("ACGT\nACGT") == [("", "ACGTACGT")]


def test_reverse_complement_and_palindrome_tie():
    assert reverse_complement("ACGT") == "ACGT"
    ck = canonicalize("ACGT")  # rc-palindrome -> forward orientation
    assert ck.canonical == "ACGT" and ck.orientation == "+"
    assert canonicalize("TTTA").orientation == "-"  # rc=TAAA < TTTA


def test_all_n_reference_gives_empty_index(service):
    # No N-free k-mer exists, so zero seeds -> EMPTY_INDEX at index time.
    with pytest.raises(MiniseedError) as exc:
        service.index_reference("alln", "N" * 30, k=9, w=5)
    assert exc.value.code is ErrorCode.EMPTY_INDEX


def test_validate_params_rejects_non_int(service):
    from miniseed.minimizer import validate_params

    with pytest.raises(MiniseedError) as exc:
        validate_params(True, 5)  # type: ignore[arg-type]
    assert exc.value.code is ErrorCode.INVALID_PARAMETER
