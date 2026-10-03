"""Quality filtering: each rejection category is asserted specifically."""

from depthcov.config import Settings
from depthcov.engine import Reference, analyze
from depthcov.filtering import FilterConfig, adjudicate
from depthcov.models import Alignment, RejectReason

REFS = [Reference("chr1", 20)]


def verdict_for(aln, **opts):
    return adjudicate(
        aln, FilterConfig(**opts), ref_length=20, known_refs={"chr1"}
    )


def test_low_mapq_inclusive_threshold():
    v = verdict_for(Alignment("q", "chr1", 0, "5M", mapq=19), min_mapq=20)
    assert not v.accepted and v.reason == RejectReason.LOW_MAPQ.value
    v2 = verdict_for(Alignment("q", "chr1", 0, "5M", mapq=20), min_mapq=20)
    assert v2.accepted


def test_out_of_bounds_end_exclusive():
    v = verdict_for(Alignment("q", "chr1", 18, "3M", mapq=60))  # [18,21)
    assert not v.accepted and v.reason == RejectReason.OUT_OF_BOUNDS.value
    v2 = verdict_for(Alignment("q", "chr1", 17, "3M", mapq=60))  # [17,20)
    assert v2.accepted


def test_unknown_reference_is_rejected_not_crashed():
    v = adjudicate(
        Alignment("q", "chrX", 0, "1M", mapq=60),
        FilterConfig(), ref_length=None, known_refs={"chr1"},
    )
    assert not v.accepted
    assert v.reason == RejectReason.UNKNOWN_REFERENCE.value


def test_invalid_cigar_category():
    v = verdict_for(Alignment("q", "chr1", 0, "5Q", mapq=60))
    assert not v.accepted and v.reason == RejectReason.INVALID_CIGAR.value
    assert "5Q" in v.detail


def test_gap_only_cigar_has_no_covered_bases():
    v = verdict_for(Alignment("q", "chr1", 0, "5D3N", mapq=60))
    assert not v.accepted
    assert v.reason == RejectReason.NO_COVERED_BASES.value


def test_flagged_duplicate_rejected():
    v = verdict_for(
        Alignment("q", "chr1", 0, "5M", mapq=60, is_duplicate=True)
    )
    assert not v.accepted and v.reason == RejectReason.DUPLICATE.value


def test_flagged_duplicate_can_be_allowed_by_config():
    v = verdict_for(
        Alignment("q", "chr1", 0, "5M", mapq=60, is_duplicate=True),
        reject_flagged_duplicates=False,
    )
    assert v.accepted


def test_check_order_duplicate_before_mapq():
    # Both conditions true; duplicate flag is evaluated first by design.
    v = verdict_for(
        Alignment("q", "chr1", 0, "5M", mapq=0, is_duplicate=True)
    )
    assert v.reason == RejectReason.DUPLICATE.value


def test_engine_end_to_end_rejection_inventory():
    alns = [
        Alignment("good", "chr1", 0, "5M", mapq=60),
        Alignment("lowq", "chr1", 0, "5M", mapq=5),
        Alignment("oob", "chr1", 19, "5M", mapq=60),
        Alignment("badcigar", "chr1", 0, "ZZ", mapq=60),
        Alignment("dupe", "chr1", 0, "2M", mapq=60, is_duplicate=True),
        Alignment("noref", "chr2", 0, "2M", mapq=60),
        Alignment("gaponly", "chr1", 0, "4N", mapq=60),
    ]
    report = analyze(alns, REFS, Settings(min_mapq=20))
    by_name = {v.query_name: v for v in report.verdicts}
    assert by_name["good"].accepted
    expected = {
        "lowq": RejectReason.LOW_MAPQ.value,
        "oob": RejectReason.OUT_OF_BOUNDS.value,
        "badcigar": RejectReason.INVALID_CIGAR.value,
        "dupe": RejectReason.DUPLICATE.value,
        "noref": RejectReason.UNKNOWN_REFERENCE.value,
        "gaponly": RejectReason.NO_COVERED_BASES.value,
    }
    for name, reason in expected.items():
        assert not by_name[name].accepted, name
        assert by_name[name].reason == reason, name
    assert len(report.accepted) == 1
    assert len(report.rejected) == 6
