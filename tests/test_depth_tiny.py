"""Per-base depth on the hand-derived tiny reference (exact expected array)."""

from depthcov.config import Settings
from depthcov.engine import Reference, analyze
from depthcov.synthetic import (
    TINY_EXPECTED_DEPTH,
    TINY_REFERENCES,
    tiny_alignments,
)

REFS = [Reference("chrTiny", TINY_REFERENCES["chrTiny"])]


def test_tiny_per_base_depth_matches_hand_derived_expectation():
    report = analyze(tiny_alignments(), REFS, Settings(min_mapq=20))
    result = report.results["chrTiny"]
    assert result.per_base_depth.tolist() == TINY_EXPECTED_DEPTH


def test_tiny_accept_and_reject_counts_are_specific():
    report = analyze(tiny_alignments(), REFS, Settings(min_mapq=20))
    reasons = {}
    for verdict in report.verdicts:
        reasons.setdefault(verdict.reason, []).append(verdict.query_name)
    assert sorted(reasons["accepted"]) == ["r1", "r2", "r3"]
    assert sorted(reasons["duplicate"]) == ["r3", "r5"]
    assert reasons["low_mapq"] == ["r6"]
    assert reasons["out_of_bounds"] == ["r7"]
    # r3 appears twice on input but once among accepted queries.
    assert result_accepted(report) == ["r1", "r2", "r3"]


def result_accepted(report):
    return list(report.results["chrTiny"].accepted_queries)


def test_gap_bases_carry_zero_extra_depth():
    # r1 = 3M2D2M at 1: gap [4,6) must not gain depth from r1.
    report = analyze(
        [a for a in tiny_alignments() if a.query_name in {"r1"}],
        REFS, Settings(min_mapq=20),
    )
    depth = report.results["chrTiny"].per_base_depth.tolist()
    assert depth == [0, 1, 1, 1, 0, 0, 1, 1, 0, 0]


def test_weighted_length_excludes_gap_bases():
    report = analyze(
        [a for a in tiny_alignments() if a.query_name in {"r1"}],
        REFS, Settings(min_mapq=20),
    )
    result = report.results["chrTiny"]
    # 5 covered bases across two blocks, depth 1 each.
    assert result.weighted_length == 5
    assert result.covered_bases == 5


def test_histogram_counts_every_reference_base_once():
    report = analyze(tiny_alignments(), REFS, Settings(min_mapq=20))
    hist = report.results["chrTiny"].histogram
    # depth array [0,1,1,2,2,2,2,1,0,0] -> three 0-bases, three 1, four 2.
    assert hist == {0: 3, 1: 3, 2: 4}
    assert sum(hist.values()) == 10
