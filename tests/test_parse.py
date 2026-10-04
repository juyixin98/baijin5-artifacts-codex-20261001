"""Unit tests: synthetic sequence parsing and failure categories."""

from __future__ import annotations

import pytest

from app.domain.errors import DigestError, ErrorCode
from app.services.parse import normalize_sequence, parse_sequence

pytestmark = pytest.mark.unit


def test_normalize_strips_whitespace_and_uppercases() -> None:
    assert normalize_sequence(" a k \na\n") == "AKA"


def test_parses_canonical_residues_with_positions() -> None:
    parsed = parse_sequence("AGK", max_length=100)
    assert parsed.length == 3
    assert [r.position for r in parsed.residues] == [1, 2, 3]
    assert [r.letter for r in parsed.residues] == ["A", "G", "K"]
    assert all(r.known and not r.ambiguous for r in parsed.residues)


def test_ambiguous_letters_are_parseable() -> None:
    parsed = parse_sequence("BJZX", max_length=100)
    assert parsed.ambiguous_positions == (1, 2, 3, 4)
    assert parsed.has_ambiguous
    assert not parsed.has_unknown
    # B candidates are N/D explicitly.
    b = parsed.residues[0]
    assert set(b.candidates) == {"N", "D"}
    assert b.mass_min != b.mass_max


def test_j_isobaric_interval_collapses() -> None:
    parsed = parse_sequence("J", max_length=100)
    j = parsed.residues[0]
    assert j.ambiguous
    assert j.mass_min == j.mass_max  # I and L are isobaric


def test_empty_sequence_is_rejected() -> None:
    with pytest.raises(DigestError) as exc:
        parse_sequence("   \n\t ", max_length=100)
    assert exc.value.code is ErrorCode.EMPTY_SEQUENCE


def test_unsupported_character_reports_position_and_category() -> None:
    with pytest.raises(DigestError) as exc:
        parse_sequence("AK!A", max_length=100)
    assert exc.value.code is ErrorCode.UNSUPPORTED_RESIDUE
    assert exc.value.position == 3
    assert exc.value.residue == "!"


def test_sequence_too_long_is_rejected() -> None:
    with pytest.raises(DigestError) as exc:
        parse_sequence("AAA", max_length=2)
    assert exc.value.code is ErrorCode.SEQUENCE_TOO_LONG
    assert exc.value.detail == {"length": 3, "limit": 2}


def test_selenocysteine_is_supported() -> None:
    parsed = parse_sequence("UC", max_length=100)
    assert not parsed.has_unknown
    assert parsed.residues[0].letter == "U"
