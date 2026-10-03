"""Boundary semantics: half-open [start,end), abutting intervals, 0-based."""

from depthcov.config import Settings
from depthcov.engine import Reference, analyze
from depthcov.synthetic import (
    BOUNDARY_EXPECTED_DEPTH,
    boundary_alignments,
)

REFS = [Reference("chrTiny", 10)]


def test_abutting_intervals_share_no_base():
    report = analyze(boundary_alignments(), REFS, Settings())
    depth = report.results["chrTiny"].per_base_depth.tolist()
    assert depth == BOUNDARY_EXPECTED_DEPTH
    # No base has depth 2 at the boundary coordinate 5.
    assert depth[5] == 1
    assert max(depth) == 1


def test_half_open_end_base_is_not_covered():
    from depthcov.models import Alignment

    # [2,5) covers exactly positions 2,3,4.
    report = analyze(
        [Alignment("x", "chrTiny", 2, "3M", mapq=60)],
        REFS, Settings(),
    )
    depth = report.results["chrTiny"].per_base_depth.tolist()
    assert depth == [0, 0, 1, 1, 1, 0, 0, 0, 0, 0]


def test_single_base_interval():
    from depthcov.models import Alignment

    report = analyze(
        [Alignment("s", "chrTiny", 0, "1M", mapq=60)],
        REFS, Settings(),
    )
    depth = report.results["chrTiny"].per_base_depth.tolist()
    assert depth == [1, 0, 0, 0, 0, 0, 0, 0, 0, 0]


def test_interval_ending_exactly_at_reference_end_is_accepted():
    from depthcov.models import Alignment

    report = analyze(
        [Alignment("end", "chrTiny", 7, "3M", mapq=60)],  # [7,10)
        REFS, Settings(),
    )
    assert report.accepted[0].reason == "accepted"
    assert report.results["chrTiny"].per_base_depth.tolist()[7:10] == [1, 1, 1]
