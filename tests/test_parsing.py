"""Tests for sequence parsing: boundaries, empty/illegal/unknown categories."""

from __future__ import annotations

import pytest

from app.domain.parsing import (
    ambiguous_positions,
    parse_sequence,
    unknown_positions,
    unsupported_letter_positions,
)
from app.errors import (
    EmptySequenceError,
    IllegalSymbolError,
    SequenceTooLongError,
    UnknownResidueError,
)


def test_parse_normalizes_case_and_reports_termini():
    parsed = parse_sequence("aAk", max_length=100)
    assert parsed.sequence == "AAK"
    assert parsed.length == 3
    assert parsed.n_terminus == 0
    assert parsed.c_terminus == 3


def test_empty_and_blank_sequences_are_category_empty_sequence():
    for raw in ("", "   ", "\t\n"):
        with pytest.raises(EmptySequenceError) as exc:
            parse_sequence(raw, max_length=100)
        assert exc.value.code == "EMPTY_SEQUENCE"


def test_internal_whitespace_is_illegal_symbol_not_silently_stripped():
    with pytest.raises(IllegalSymbolError) as exc:
        parse_sequence("AA K", max_length=100)
    assert exc.value.code == "ILLEGAL_SYMBOL"
    assert exc.value.details["symbol"] == " "


def test_stop_digit_and_dash_are_illegal_symbols():
    for raw in ("AA*", "A1K", "A-K"):
        with pytest.raises(IllegalSymbolError) as exc:
            parse_sequence(raw, max_length=100)
        assert exc.value.code == "ILLEGAL_SYMBOL", raw


def test_sequence_too_long_is_its_own_category():
    with pytest.raises(SequenceTooLongError) as exc:
        parse_sequence("AAA", max_length=2)
    assert exc.value.code == "SEQUENCE_TOO_LONG"
    assert exc.value.details == {"length": 3, "max_length": 2}


def test_x_is_unknown_bzj_are_ambiguous_u_is_unsupported():
    assert unknown_positions("AXA") == [2]
    assert ambiguous_positions("ABZJ") == [2, 3, 4]
    assert unsupported_letter_positions("AUA") == [2]


def test_strict_path_distinguishes_x_from_unsupported_letter():
    from app.domain.parsing import ensure_no_unknown_residues

    with pytest.raises(UnknownResidueError) as exc:
        ensure_no_unknown_residues("AAX")
    assert exc.value.code == "UNKNOWN_RESIDUE"
    assert exc.value.details["token"] == "X"
    assert exc.value.details["positions"] == [3]

    with pytest.raises(UnknownResidueError) as exc:
        ensure_no_unknown_residues("AAU")
    assert exc.value.details["token"] == "U"
    assert exc.value.details["first_position"] == 3
