"""End-to-end pipeline tests on the hand-computed fixture.

Expected numbers come from tests/conftest.py, computed by hand from
tests/fixtures/alignments_small.tsv — not from the implementation.
"""

from coverage_depth.config import PipelineConfig
from coverage_depth.errors import DecisionStatus, ReasonCode
from coverage_depth.pipeline import run_pipeline

from conftest import (
    EXPECTED_COVERED,
    EXPECTED_HISTOGRAM,
    EXPECTED_SEGMENTS,
    EXPECTED_WEIGHTED,
    REF_LENGTH,
    REF_NAME,
)


def test_pipeline_matches_hand_computed(fixture_records, default_config, tmp_path):
    result = run_pipeline(
        fixture_records, REF_NAME, REF_LENGTH, default_config,
        spill_dir=str(tmp_path),
    )
    assert [(s.start, s.end, s.depth) for s in result.segments] == EXPECTED_SEGMENTS
    assert result.histogram == EXPECTED_HISTOGRAM
    assert result.covered_bases == EXPECTED_COVERED
    assert result.weighted_bases == EXPECTED_WEIGHTED
    assert result.mean_depth == EXPECTED_WEIGHTED / REF_LENGTH


def test_pipeline_decisions_are_explicit(fixture_records, default_config, tmp_path):
    result = run_pipeline(
        fixture_records, REF_NAME, REF_LENGTH, default_config,
        spill_dir=str(tmp_path),
    )
    by_read = {}
    for d in result.decisions:
        by_read.setdefault(d.read_id, []).append(d)

    # 8 records: r1, r2, r3 x2 accepted; r4 duplicate; r5 low mapq;
    # r6 undecidable; r7 out of bounds.
    assert len(result.decisions) == 8
    for read in ("r1", "r2"):
        (d,) = by_read[read]
        assert d.status is DecisionStatus.ACCEPTED
    assert len(by_read["r3"]) == 2
    assert all(d.status is DecisionStatus.ACCEPTED for d in by_read["r3"])

    (d4,) = by_read["r4"]
    assert (d4.status, d4.reason) is not None
    assert d4.status is DecisionStatus.REJECTED
    assert d4.reason is ReasonCode.DUPLICATE

    (d5,) = by_read["r5"]
    assert d5.status is DecisionStatus.REJECTED
    assert d5.reason is ReasonCode.LOW_MAPQ

    (d6,) = by_read["r6"]
    assert d6.status is DecisionStatus.UNDECIDABLE
    assert d6.reason is ReasonCode.MAPQ_UNKNOWN

    (d7,) = by_read["r7"]
    assert d7.status is DecisionStatus.REJECTED
    assert d7.reason is ReasonCode.OUT_OF_BOUNDS


def test_paired_overlap_not_double_counted(fixture_records, default_config, tmp_path):
    # r3's mates overlap [25,30); union is [20,35) = 15 bases, not 20.
    result = run_pipeline(
        fixture_records, REF_NAME, REF_LENGTH, default_config,
        spill_dir=str(tmp_path),
    )
    seg = [s for s in result.segments if s.start == 20]
    assert [(s.start, s.end, s.depth) for s in seg] == [(20, 35, 1)]


def test_forced_spilling_gives_identical_result(fixture_records, tmp_path):
    in_memory = run_pipeline(fixture_records, REF_NAME, REF_LENGTH,
                             config=PipelineConfig(sort_chunk_size=50_000),
                             spill_dir=str(tmp_path / "a"))
    spilled = run_pipeline(fixture_records, REF_NAME, REF_LENGTH,
                           config=PipelineConfig(sort_chunk_size=2),
                           spill_dir=str(tmp_path / "b"))
    assert in_memory.segments == spilled.segments
    assert in_memory.histogram == spilled.histogram
    assert in_memory.weighted_bases == spilled.weighted_bases
