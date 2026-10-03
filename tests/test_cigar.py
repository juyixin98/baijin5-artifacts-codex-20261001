"""CIGAR parsing: exact failure categories and gap semantics."""

import pytest

from depthcov.cigar import (
    CigarError,
    covered_blocks,
    naive_covered_positions,
    parse_cigar,
    query_span,
    reference_span,
)
from depthcov.models import GAP_OPS


def test_parse_basic_cigar():
    assert parse_cigar("10M") == [(10, "M")]
    assert parse_cigar("5M2D3M") == [(5, "M"), (2, "D"), (3, "M")]


def test_parse_accepts_standard_ops():
    assert parse_cigar("1M2I3D4N5S6H7=8X9P") == [
        (1, "M"), (2, "I"), (3, "D"), (4, "N"), (5, "S"),
        (6, "H"), (7, "="), (8, "X"), (9, "P"),
    ]


@pytest.mark.parametrize(
    "bad",
    ["", "M", "10", "10Q", "0M", "5M 2D", "5M,2D", "abc", "10m", "-3M", "5M2"],
)
def test_parse_rejects_malformed(bad):
    with pytest.raises(CigarError):
        parse_cigar(bad)


def test_reference_span_counts_gap_ops():
    # 3M2D2M consumes 7 reference bases, of which only 5 are covered.
    assert reference_span("3M2D2M") == 7


def test_query_span_includes_clips_insertions():
    assert query_span("2S3M1I4M1H") == 2 + 3 + 1 + 4


def test_gap_ops_constant():
    # Regression guard: the two gap ops must be exactly D and N.
    assert GAP_OPS == {"D", "N"}


def test_gap_splits_blocks_and_is_excluded():
    blocks, ref_end = covered_blocks(1, "3M2D2M")
    assert [(b.start, b.end) for b in blocks] == [(1, 4), (6, 8)]
    assert ref_end == 8
    # The gap bases [4,6) appear in NO block.
    covered = {p for b in blocks for p in range(b.start, b.end)}
    assert covered == {1, 2, 3, 6, 7}
    assert not (covered & {4, 5})


def test_leading_trailing_and_all_gap_cigars():
    blocks, end = covered_blocks(0, "2D5M")
    assert [(b.start, b.end) for b in blocks] == [(2, 7)]
    assert end == 7
    blocks, end = covered_blocks(0, "5M2D")
    assert [(b.start, b.end) for b in blocks] == [(0, 5)]
    assert end == 7
    blocks, _ = covered_blocks(0, "3D2N")
    assert blocks == []


def test_insertions_do_not_move_reference():
    blocks, end = covered_blocks(2, "3M2I3M")
    assert [(b.start, b.end) for b in blocks] == [(2, 8)]
    assert end == 8


def test_block_expansion_matches_naive_oracle():
    cases = ["10M", "3M2D2M", "1D4M1N", "2S5M3S", "2M1I2M", "1N3M1D2M"]
    for cigar in cases:
        blocks, _ = covered_blocks(0, cigar)
        from_blocks = sorted(p for b in blocks for p in range(b.start, b.end))
        assert from_blocks == naive_covered_positions(0, cigar), cigar


def test_hard_clip_and_padding_add_no_coverage():
    blocks, end = covered_blocks(0, "2H3M1P2M2H")
    assert [(b.start, b.end) for b in blocks] == [(0, 5)]
    assert end == 5
