"""Cross-check the engine against the independent brute-force oracle.

The oracle (tests/oracle.py) is a separate hand-written implementation.
These tests compare concrete arrays over hand cases and many randomized
fixtures, under both dedup policies and across quality thresholds.
"""

from depthcov.config import Settings
from depthcov.coverage import PER_RECORD, UNION_PER_QUERY
from depthcov.engine import Reference, analyze
from depthcov.models import Alignment
from depthcov.synthetic import synthetic_random_alignments

from oracle import oracle_depth, oracle_walk

REF_NAME = "chrR"
REF_LEN = 60


def _run(alignments, policy, min_mapq=20):
    settings = Settings(min_mapq=min_mapq, dedup_policy=policy)
    report = analyze(
        alignments, [Reference(REF_NAME, REF_LEN)], settings,
    )
    return report.results[REF_NAME].per_base_depth.tolist()


def test_hand_case_matches_oracle_with_gap():
    alns = [
        Alignment("a", REF_NAME, 0, "5M2D5M", mapq=60),
        Alignment("b", REF_NAME, 4, "8M", mapq=60),
        Alignment("c", REF_NAME, 30, "4M", mapq=60),
    ]
    for policy in (UNION_PER_QUERY, PER_RECORD):
        got = _run(alns, policy)
        want = oracle_depth(
            alns, {REF_NAME: REF_LEN},
            union_per_query=(policy == UNION_PER_QUERY),
        )[REF_NAME]
        assert got == want


def test_randomized_fixtures_match_oracle_both_policies():
    for seed in range(30):
        alns = synthetic_random_alignments(
            REF_NAME, REF_LEN, n=40, seed=seed,
        )
        for policy in (UNION_PER_QUERY, PER_RECORD):
            got = _run(alns, policy)
            want = oracle_depth(
                alns, {REF_NAME: REF_LEN},
                union_per_query=(policy == UNION_PER_QUERY),
            )[REF_NAME]
            assert got == want, f"seed={seed} policy={policy}"


def test_randomized_with_quality_thresholds():
    alns = synthetic_random_alignments(REF_NAME, REF_LEN, n=50, seed=77)
    for min_mapq in (0, 20, 31, 61):
        got = _run(alns, UNION_PER_QUERY, min_mapq=min_mapq)
        want = oracle_depth(
            alns, {REF_NAME: REF_LEN}, min_mapq=min_mapq,
        )[REF_NAME]
        assert got == want, f"min_mapq={min_mapq}"


def test_engine_and_oracle_agree_on_rejection_sets():
    from oracle import oracle_accepted

    alns = synthetic_random_alignments(REF_NAME, REF_LEN, n=60, seed=9)
    report = analyze(alns, [Reference(REF_NAME, REF_LEN)],
                     Settings(min_mapq=20))
    engine_rejected = {
        (v.query_name, v.reason) for v in report.verdicts if not v.accepted
    }
    _, oracle_rejected = oracle_accepted(alns, {REF_NAME: REF_LEN})
    oracle_rej = {(a.query_name, reason) for a, reason in oracle_rejected}
    # The two implementations reject the same records for the same reasons.
    assert engine_rejected == oracle_rej


def test_oracle_walk_independent_semantics():
    # Direct check of gap exclusion and half-open end.
    positions, end = oracle_walk(2, "3M2D3M")
    assert positions == [2, 3, 4, 7, 8, 9]
    assert end == 10
