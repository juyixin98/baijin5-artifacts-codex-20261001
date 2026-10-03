"""Pipeline policy tests: brightness vs non-overlap, ambiguity, degenerate."""

import pytest

from app.kernel.pipeline import (
    AMBIGUOUS_PEAKS,
    DEGENERATE_SPECTRUM,
    INSUFFICIENT_OVERLAP,
    LOW_PSR,
    estimate_shift,
)


def test_integer_fixture_ok_and_accurate(fixtures, cfg):
    est = estimate_shift(fixtures["integer_shift"].img_a, fixtures["integer_shift"].img_b, cfg)
    assert est.status == "ok"
    assert est.shift == pytest.approx((12.0, -7.0), abs=0.15)
    assert est.failures == []
    assert est.overlap_fraction > cfg.min_overlap


def test_brightness_change_still_ok(fixtures, cfg):
    """Brightness change must NOT be confused with non-overlap."""
    fx = fixtures["brightness_change"]
    est = estimate_shift(fx.img_a, fx.img_b, cfg)
    assert est.status == "ok"
    assert est.shift == pytest.approx(fx.ground_truth_shift, abs=0.3)
    assert est.overlap_fraction > 0.85


def test_periodic_texture_flagged_ambiguous(fixtures, cfg):
    fx = fixtures["periodic_texture"]
    est = estimate_shift(fx.img_a, fx.img_b, cfg)
    assert est.status == "uncertain"
    assert AMBIGUOUS_PEAKS in est.uncertainties
    # Ambiguity peaks are reported, not hidden.
    assert len(est.peaks) >= 2
    assert est.second_peak_ratio > cfg.ambiguity_ratio


def test_constant_image_fails_degenerate(fixtures, cfg):
    fx = fixtures["constant_image"]
    est = estimate_shift(fx.img_a, fx.img_b, cfg)
    assert est.status == "failed"
    assert DEGENERATE_SPECTRUM in est.failures
    assert est.shift is None
    assert est.confidence == 0.0


def test_low_overlap_flagged(fixtures, cfg):
    fx = fixtures["low_overlap"]
    est = estimate_shift(fx.img_a, fx.img_b, cfg)
    assert est.status != "ok"
    assert INSUFFICIENT_OVERLAP in est.failures + [
        u.split(":")[0] for u in est.uncertainties
    ]
    assert est.overlap_fraction < cfg.min_overlap


def test_independent_pair_not_ok_low_psr(fixtures, cfg):
    """True non-overlap: no valid shift, distinct from brightness change."""
    fx = fixtures["independent_pair"]
    est = estimate_shift(fx.img_a, fx.img_b, cfg)
    assert est.status != "ok"
    assert LOW_PSR in est.failures + [u.split(":")[0] for u in est.uncertainties]


def test_diagnostics_present(fixtures, cfg):
    est = estimate_shift(fixtures["integer_shift"].img_a, fixtures["integer_shift"].img_b, cfg)
    for key in ("shape", "window", "pad_factor", "valid_shift_range",
                "degenerate_fraction", "eps", "kernel_versions", "elapsed_ms"):
        assert key in est.diagnostics
