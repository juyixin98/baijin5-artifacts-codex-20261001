"""Quality-filter decision tests: every verdict and reason is asserted."""

import pytest

from coverage_depth.config import FilterConfig
from coverage_depth.errors import DecisionStatus, ReasonCode
from coverage_depth.filtering import evaluate_record
from coverage_depth.models import AlignmentRecord


def make_record(**overrides) -> AlignmentRecord:
    base = dict(
        record_index=0, read_id="r", ref="chrS", start=0,
        mapq=30, flags=0, cigar="10M",
    )
    base.update(overrides)
    return AlignmentRecord(**base)


class TestFlagRejections:
    @pytest.mark.parametrize(
        "flag,reason",
        [
            (0x4, ReasonCode.UNMAPPED),
            (0x100, ReasonCode.SECONDARY),
            (0x800, ReasonCode.SUPPLEMENTARY),
            (0x200, ReasonCode.QC_FAIL),
            (0x400, ReasonCode.DUPLICATE),
        ],
    )
    def test_flagged_records_rejected(self, flag, reason):
        decision = evaluate_record(make_record(flags=flag), FilterConfig())
        assert decision.status is DecisionStatus.REJECTED
        assert decision.reason is reason
        assert decision.record_index == 0
        assert decision.read_id == "r"

    def test_unmapped_wins_over_other_flags(self):
        decision = evaluate_record(make_record(flags=0x4 | 0x400), FilterConfig())
        assert decision.reason is ReasonCode.UNMAPPED


class TestMapqThreshold:
    def test_below_threshold_rejected(self):
        decision = evaluate_record(make_record(mapq=19), FilterConfig(min_mapq=20))
        assert decision.status is DecisionStatus.REJECTED
        assert decision.reason is ReasonCode.LOW_MAPQ
        assert "19 < min_mapq 20" in decision.detail

    def test_at_threshold_accepted(self):
        decision = evaluate_record(make_record(mapq=20), FilterConfig(min_mapq=20))
        assert decision.status is DecisionStatus.ACCEPTED
        assert decision.reason is ReasonCode.ACCEPTED

    def test_unknown_mapq_is_undecidable_not_rejected(self):
        decision = evaluate_record(make_record(mapq=None), FilterConfig())
        assert decision.status is DecisionStatus.UNDECIDABLE
        assert decision.reason is ReasonCode.MAPQ_UNKNOWN


class TestConfigSwitches:
    def test_duplicate_kept_when_exclusion_disabled(self):
        cfg = FilterConfig(exclude_duplicates=False)
        decision = evaluate_record(make_record(flags=0x400), cfg)
        assert decision.status is DecisionStatus.ACCEPTED
