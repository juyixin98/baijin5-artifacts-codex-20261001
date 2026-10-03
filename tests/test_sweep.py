"""Sweep-line segmentation and weighted-length conservation."""

from depthcov.config import Settings
from depthcov.coverage import coverage_for_reference
from depthcov.engine import Reference, analyze
from depthcov.filtering import adjudicate, FilterConfig
from depthcov.models import Alignment
from depthcov.cigar import blocks_for_alignment


def _verdicts(alns, ref_len=30):
    fc = FilterConfig()
    out = []
    for a in alns:
        v = adjudicate(a, fc, ref_length=ref_len, known_refs={"c"})
        assert v.accepted, v.detail
        out.append(v)
    return out


def test_segments_are_maximal_and_exhaustive():
    alns = [
        Alignment("a", "c", 0, "5M", mapq=60),
        Alignment("b", "c", 3, "4M", mapq=60),   # [3,7)
        Alignment("c2", "c", 10, "2M", mapq=60),  # [10,12)
    ]
    result = coverage_for_reference("c", 30, _verdicts(alns))
    segs = [(s.start, s.end, s.depth) for s in result.segments]
    # depth: [0,3)=1 [3,5)=2 [5,7)=1 [10,12)=1
    assert segs == [(0, 3, 1), (3, 5, 2), (5, 7, 1), (10, 12, 1)]
    # Segments tile only covered positions, never overlap.
    prev_end = -1
    for s in result.segments:
        assert s.start >= prev_end
        prev_end = s.end


def test_segment_depths_reconstruct_per_base_array():
    alns = [
        Alignment("a", "c", 1, "6M2D3M", mapq=60),
        Alignment("b", "c", 4, "8M", mapq=60),
    ]
    result = coverage_for_reference("c", 30, _verdicts(alns))
    rebuilt = [0] * result.ref_length
    for s in result.segments:
        for p in range(s.start, s.end):
            rebuilt[p] = s.depth
    assert rebuilt == result.per_base_depth.tolist()


def test_weighted_length_conservation_three_ways():
    alns = [
        Alignment("a", "c", 0, "4M1D4M", mapq=60),  # 8 covered bases
        Alignment("b", "c", 2, "6M", mapq=60),      # 6
        Alignment("d", "c", 20, "5M", mapq=60),     # 5
    ]
    result = coverage_for_reference("c", 30, _verdicts(alns))
    # Independent account from accepted blocks.
    expected = 0
    for a in alns:
        blocks, _ = blocks_for_alignment(a)
        expected += sum(b.length for b in blocks)
    assert result.weighted_length == expected == 19
    # Sweep segment account.
    seg_integral = sum(s.length * s.depth for s in result.segments)
    assert seg_integral == result.weighted_length
    # Histogram account: sum(depth * bases_at_depth).
    hist_integral = sum(d * c for d, c in result.histogram.items())
    assert hist_integral == result.weighted_length
    # Zero-depth bases fill the rest of the reference.
    assert sum(result.histogram.values()) == result.ref_length


def test_coverage_never_negative_and_closes_to_zero():
    # Nested intervals stress the event balancing.
    alns = [
        Alignment("a", "c", 0, "20M", mapq=60),
        Alignment("b", "c", 5, "10M", mapq=60),
        Alignment("c2", "c", 7, "3M", mapq=60),
    ]
    result = coverage_for_reference("c", 30, _verdicts(alns))
    assert min(result.per_base_depth.tolist()) >= 0
    assert result.per_base_depth[-1] == 0


def test_conservation_report_exposed_by_engine():
    alns = [Alignment("a", "c", 0, "3M2N3M", mapq=60)]
    report = analyze(alns, [Reference("c", 30)], Settings())
    cons = report.conservation()["c"]
    assert cons["weighted_length"] == 6
    assert cons["histogram_bases"] == 30
