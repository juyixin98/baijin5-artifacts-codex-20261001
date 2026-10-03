"""De-duplication: paired-mate overlap and repeated reads must not double count."""

from depthcov.config import Settings
from depthcov.coverage import PER_RECORD, UNION_PER_QUERY
from depthcov.engine import Reference, analyze
from depthcov.models import Alignment
from depthcov.synthetic import PAIRED_EXPECTED_DEPTH, paired_overlap_alignments

REFS = [Reference("chrTiny", 10)]


def test_overlapping_mates_same_qname_do_not_double_count():
    report = analyze(
        paired_overlap_alignments(), REFS,
        Settings(dedup_policy=UNION_PER_QUERY),
    )
    depth = report.results["chrTiny"].per_base_depth.tolist()
    assert depth == PAIRED_EXPECTED_DEPTH
    assert max(depth) == 1
    # weighted length of the union is 9 bases, not 6+5 = 11.
    assert report.results["chrTiny"].weighted_length == 9


def test_per_record_policy_does_double_count_for_distinct_records():
    # The policy is explicit: per_record counts every record independently.
    report = analyze(
        paired_overlap_alignments(), REFS,
        Settings(dedup_policy=PER_RECORD),
    )
    depth = report.results["chrTiny"].per_base_depth.tolist()
    # [0,4) depth1; [4,6) depth2; [6,9) depth1; [9,10) depth0.
    assert depth == [1, 1, 1, 1, 2, 2, 1, 1, 1, 0]
    assert report.results["chrTiny"].weighted_length == 11


def test_identical_qname_interval_appears_twice_counts_once():
    alns = [
        Alignment("dup", "chrTiny", 1, "4M", mapq=60),
        Alignment("dup", "chrTiny", 1, "4M", mapq=60),
    ]
    report = analyze(alns, REFS, Settings(dedup_policy=UNION_PER_QUERY))
    depth = report.results["chrTiny"].per_base_depth.tolist()
    assert depth[1:5] == [1, 1, 1, 1]
    assert report.results["chrTiny"].weighted_length == 4


def test_distinct_qnames_overlapping_do_count_separately():
    alns = [
        Alignment("a", "chrTiny", 0, "5M", mapq=60),
        Alignment("b", "chrTiny", 0, "5M", mapq=60),
    ]
    report = analyze(alns, REFS, Settings(dedup_policy=UNION_PER_QUERY))
    depth = report.results["chrTiny"].per_base_depth.tolist()
    assert depth[:5] == [2, 2, 2, 2, 2]
    assert report.results["chrTiny"].weighted_length == 10


def test_partial_overlap_same_qname_uses_union():
    # Same qname, two blocks that partially overlap.
    alns = [
        Alignment("q", "chrTiny", 0, "5M", mapq=60),   # [0,5)
        Alignment("q", "chrTiny", 3, "5M", mapq=60),   # [3,8)
    ]
    report = analyze(alns, REFS, Settings(dedup_policy=UNION_PER_QUERY))
    depth = report.results["chrTiny"].per_base_depth.tolist()
    assert depth == [1, 1, 1, 1, 1, 1, 1, 1, 0, 0]
    assert report.results["chrTiny"].weighted_length == 8
