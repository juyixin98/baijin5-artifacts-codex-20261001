"""Sweep-line tests: hand-computed segmentations plus an independent
per-base oracle (a naive loop written only for these tests, sharing no
code with the vectorized sweep under test)."""

import random

from coverage_depth.models import ReadBlock
from coverage_depth.sweep import sweep_segments


def naive_depths(blocks, ref_length):
    """Independent oracle: one Python loop per covered base."""
    depth = [0] * ref_length
    for b in blocks:
        for pos in range(b.start, b.end):
            depth[pos] += 1
    return depth


def segments_to_depths(segments, ref_length):
    depth = [0] * ref_length
    for seg in segments:
        for pos in range(seg.start, seg.end):
            depth[pos] = seg.depth
    return depth


def rb(read, start, end):
    return ReadBlock(read, "chrS", start, end)


class TestHandComputed:
    def test_gap_read_plus_overlap(self):
        # r1 has a 5-base gap: [5,10) and [15,20); r2 covers [8,18).
        blocks = [rb("r1", 5, 10), rb("r1", 15, 20), rb("r2", 8, 18)]
        segments, histogram, covered, weighted = sweep_segments(blocks, 30)
        assert [(s.start, s.end, s.depth) for s in segments] == [
            (5, 8, 1),
            (8, 10, 2),
            (10, 15, 1),
            (15, 18, 2),
            (18, 20, 1),
        ]
        assert histogram == {0: 5 + 10, 1: 10, 2: 5}
        assert covered == 15
        assert weighted == 20

    def test_boundary_adjacent_blocks_do_not_overlap(self):
        # [0,10) ends exactly where [10,20) begins; position 10 belongs
        # only to the second block. No position may reach depth 2.
        blocks = [rb("r1", 0, 10), rb("r2", 10, 20)]
        segments, histogram, covered, weighted = sweep_segments(blocks, 20)
        assert [(s.start, s.end, s.depth) for s in segments] == [
            (0, 10, 1),
            (10, 20, 1),
        ]
        assert histogram == {1: 20}
        assert covered == 20
        assert weighted == 20

    def test_empty_input_reports_full_reference_uncovered(self):
        segments, histogram, covered, weighted = sweep_segments([], 12)
        assert segments == ()
        assert histogram == {0: 12}
        assert covered == 0
        assert weighted == 0

    def test_single_block_interior(self):
        segments, histogram, covered, weighted = sweep_segments([rb("r", 3, 7)], 10)
        assert [(s.start, s.end, s.depth) for s in segments] == [(3, 7, 1)]
        assert histogram == {0: 6, 1: 4}
        assert covered == 4
        assert weighted == 4


class TestOracleCrossCheck:
    def test_random_inputs_match_naive_per_base_oracle(self):
        rng = random.Random(20261004)
        for trial in range(200):
            ref_length = rng.randint(1, 60)
            blocks = []
            for i in range(rng.randint(0, 12)):
                start = rng.randrange(0, ref_length)
                end = rng.randrange(start + 1, ref_length + 1)
                blocks.append(rb(f"r{i}", start, end))
            segments, histogram, covered, weighted = sweep_segments(blocks, ref_length)

            expected = naive_depths(blocks, ref_length)
            assert segments_to_depths(segments, ref_length) == expected
            assert covered == sum(1 for d in expected if d > 0)
            assert weighted == sum(expected)
            assert sum(histogram.values()) == ref_length
            for depth, bases in histogram.items():
                assert bases == sum(1 for d in expected if d == depth)

    def test_weighted_length_conservation(self):
        rng = random.Random(7)
        for _ in range(100):
            ref_length = rng.randint(1, 100)
            blocks = []
            for i in range(rng.randint(1, 20)):
                start = rng.randrange(0, ref_length)
                end = rng.randrange(start + 1, ref_length + 1)
                blocks.append(rb(f"r{i}", start, end))
            _, _, _, weighted = sweep_segments(blocks, ref_length)
            assert weighted == sum(b.end - b.start for b in blocks)
