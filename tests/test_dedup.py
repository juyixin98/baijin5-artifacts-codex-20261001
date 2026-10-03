"""Per-read dedup tests: paired overlap must not double count."""

from coverage_depth.dedup import group_and_merge, merge_intervals, merge_read_blocks
from coverage_depth.models import Block, ReadBlock


class TestMergeIntervals:
    def test_overlapping_intervals_merge(self):
        merged = merge_intervals([Block(0, 10), Block(5, 15)])
        assert [(b.start, b.end) for b in merged] == [(0, 15)]

    def test_touching_intervals_merge_without_double_count(self):
        # Half-open: [0,10) and [10,20) share no base; union is [0,20).
        merged = merge_intervals([Block(0, 10), Block(10, 20)])
        assert [(b.start, b.end) for b in merged] == [(0, 20)]

    def test_disjoint_intervals_stay_separate(self):
        merged = merge_intervals([Block(0, 5), Block(10, 15)])
        assert [(b.start, b.end) for b in merged] == [(0, 5), (10, 15)]

    def test_contained_interval_absorbed(self):
        merged = merge_intervals([Block(0, 20), Block(5, 10)])
        assert [(b.start, b.end) for b in merged] == [(0, 20)]

    def test_unsorted_input_accepted(self):
        merged = merge_intervals([Block(10, 20), Block(0, 5), Block(3, 12)])
        assert [(b.start, b.end) for b in merged] == [(0, 20)]


class TestReadBlockMerge:
    def test_mate_pair_overlap_merged_to_union(self):
        # Two records, ONE read_id, overlapping by 5 bases.
        blocks = [
            ReadBlock("r3", "chrS", 20, 30),
            ReadBlock("r3", "chrS", 25, 35),
        ]
        merged = merge_read_blocks(blocks)
        assert [(b.start, b.end) for b in merged] == [(20, 35)]
        # The overlap [25,30) contributes 5 bases once, not twice.
        assert sum(b.end - b.start for b in merged) == 15

    def test_distinct_reads_never_merge(self):
        blocks = [
            ReadBlock("rA", "chrS", 0, 10),
            ReadBlock("rB", "chrS", 5, 15),
        ]
        out = list(group_and_merge(blocks))
        assert [(b.read_id, b.start, b.end) for b in out] == [
            ("rA", 0, 10),
            ("rB", 5, 15),
        ]

    def test_grouping_requires_sorted_input_by_read(self):
        blocks = [
            ReadBlock("rA", "chrS", 0, 10),
            ReadBlock("rA", "chrS", 20, 30),
            ReadBlock("rB", "chrS", 5, 8),
        ]
        out = list(group_and_merge(blocks))
        assert [(b.read_id, b.start, b.end) for b in out] == [
            ("rA", 0, 10),
            ("rA", 20, 30),
            ("rB", 5, 8),
        ]

    def test_empty_group_yields_nothing(self):
        assert list(group_and_merge(iter([]))) == []
