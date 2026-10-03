"""CIGAR and fixture-line parsing tests, including gap operations."""

import pytest

from coverage_depth.parsing import (
    CigarError,
    LineParseError,
    cigar_to_blocks,
    iter_alignment_lines,
    parse_alignment_line,
)


class TestCigarToBlocks:
    def test_gap_op_n_splits_blocks_and_counts_nothing(self):
        blocks = cigar_to_blocks("5M5N5M", start=5)
        assert [(b.start, b.end) for b in blocks] == [(5, 10), (15, 20)]

    def test_deletion_is_also_a_gap(self):
        blocks = cigar_to_blocks("10M3D5M", start=0)
        assert [(b.start, b.end) for b in blocks] == [(0, 10), (13, 18)]

    def test_pure_match_single_block(self):
        blocks = cigar_to_blocks("10M", start=100)
        assert [(b.start, b.end) for b in blocks] == [(100, 110)]

    def test_soft_clip_and_insertion_consume_no_reference(self):
        blocks = cigar_to_blocks("5S8M2I2M", start=10)
        assert [(b.start, b.end) for b in blocks] == [(10, 20)]

    def test_hard_clip_ignored(self):
        blocks = cigar_to_blocks("3H7M", start=4)
        assert [(b.start, b.end) for b in blocks] == [(4, 11)]

    def test_adjacent_covered_ops_coalesce(self):
        blocks = cigar_to_blocks("4M6M", start=0)
        assert [(b.start, b.end) for b in blocks] == [(0, 10)]

    def test_read_ending_in_gap_emits_no_trailing_block(self):
        blocks = cigar_to_blocks("6M4N", start=2)
        assert [(b.start, b.end) for b in blocks] == [(2, 8)]

    def test_read_with_no_covered_op_yields_no_blocks(self):
        assert cigar_to_blocks("10N", start=0) == []

    @pytest.mark.parametrize(
        "bad",
        ["", "*", "10Z", "0M", "10M5", "M10", "10m", "5M-2M"],
    )
    def test_malformed_cigar_raises(self, bad):
        with pytest.raises(CigarError):
            cigar_to_blocks(bad, start=0)


class TestParseAlignmentLine:
    def test_well_formed_line(self):
        rec = parse_alignment_line("r1\tchrS\t5\t30\t0x400\t5M5N5M", 0)
        assert rec.read_id == "r1"
        assert rec.ref == "chrS"
        assert rec.start == 5
        assert rec.mapq == 30
        assert rec.flags == 0x400
        assert rec.cigar == "5M5N5M"

    def test_star_mapq_becomes_none(self):
        rec = parse_alignment_line("r1\tchrS\t5\t*\t0\t5M", 0)
        assert rec.mapq is None

    @pytest.mark.parametrize(
        "line,fragment",
        [
            ("r1\tchrS\t5\t30\t0", "6 tab-separated"),
            ("\tchrS\t5\t30\t0\t5M", "empty read_id"),
            ("r1\t\t5\t30\t0\t5M", "empty reference"),
            ("r1\tchrS\tx\t30\t0\t5M", "non-integer start"),
            ("r1\tchrS\t-1\t30\t0\t5M", "negative start"),
            ("r1\tchrS\t5\tx\t0\t5M", "non-integer mapq"),
            ("r1\tchrS\t5\t300\t0\t5M", "mapq out of range"),
            ("r1\tchrS\t5\t30\tzz\t5M", "non-integer flags"),
            ("r1\tchrS\t5\t30\t-1\t5M", "negative flags"),
        ],
    )
    def test_malformed_lines_raise_with_category(self, line, fragment):
        with pytest.raises(LineParseError, match=fragment):
            parse_alignment_line(line, 0)


class TestIterAlignmentLines:
    def test_skips_comments_and_blanks_and_indexes_records(self):
        text = "# header\n\nr1\tc\t0\t10\t0\t1M\n# note\nr2\tc\t1\t10\t0\t1M\n"
        pairs = list(iter_alignment_lines(text))
        assert [i for i, _ in pairs] == [0, 1]
        assert pairs[0][1].startswith("r1")
        assert pairs[1][1].startswith("r2")
