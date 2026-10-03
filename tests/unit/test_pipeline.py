"""Pipeline: round-trip verification, zero-energy path, Toeplitz cross-check."""
from __future__ import annotations

import numpy as np

from app.config import Settings
from app.lpc.filters import AnalysisFilter
from app.lpc.pipeline import (
    analyze_frame,
    reconstruction_metrics,
    roundtrip_frame,
    synthesize_frame,
)
from tests.conftest import load_fixture

SETTINGS = Settings()


def test_roundtrip_ar_frame_is_verified_against_original():
    x = np.asarray(load_fixture("ar_process.json")["samples"][:320])
    analysis, synthesis, metrics = roundtrip_frame(x, 10, "hann", SETTINGS)
    assert metrics.verified
    assert metrics.relative_error <= SETTINGS.reconstruction_tol
    assert metrics.residual_energy_ratio < 0.2  # predictor works, but...
    # ...verification must be based on the reconstruction error, and the
    # note must say so explicitly.
    assert "residual" in metrics.note
    assert analysis.result.stable
    assert analysis.result.errors == []


def test_roundtrip_silence_uses_zero_energy_definition():
    x = np.asarray(load_fixture("silence.json")["samples"])
    analysis, synthesis, metrics = roundtrip_frame(x, 10, "hann", SETTINGS)
    r = analysis.result
    np.testing.assert_array_equal(r.coefficients, [1.0] + [0.0] * 10)
    assert r.gain == 0.0
    assert np.all(r.residual == 0.0)
    assert np.all(synthesis.samples == 0.0)
    assert metrics.verified
    assert metrics.residual_energy_ratio is None  # undefined for zero energy
    assert [d.code for d in r.diagnostics] == ["ZERO_ENERGY_FRAME"]


def test_small_residual_alone_is_not_lossless_evidence():
    # Analyse the second frame of a stream (state carried), then synthesize
    # its residual with a WRONG (zero) initial state: the residual is
    # unchanged and small, yet reconstruction must be reported as failed.
    x = np.asarray(load_fixture("ar_process.json")["samples"])
    boundary = 320
    stream_filter = AnalysisFilter(10)
    stream_filter.process(x[:boundary], _coeffs(x[:boundary]))
    coeffs = _coeffs(x[boundary : boundary + 320])
    residual = stream_filter.process(x[boundary : boundary + 320], coeffs)

    wrong = synthesize_frame(coeffs, residual, initial_state=None)
    metrics = reconstruction_metrics(
        x[boundary : boundary + 320],
        wrong.samples,
        residual_energy=float(np.dot(residual, residual)),
        frame_energy=float(np.dot(x[boundary : boundary + 320], x[boundary : boundary + 320])),
        settings=SETTINGS,
    )
    assert metrics.residual_energy_ratio < 0.2  # residual looks "good"
    assert not metrics.verified  # but reconstruction is wrong and we say so
    assert metrics.relative_error > 1e-3


def _coeffs(frame: np.ndarray) -> np.ndarray:
    return analyze_frame(frame, 10, "hann", SETTINGS).result.coefficients


def test_toeplitz_crosscheck_agrees_for_well_conditioned_frame():
    x = np.asarray(load_fixture("ar_process.json")["samples"][:320])
    outcome = analyze_frame(x, 10, "hann", SETTINGS, verify_toeplitz=True)
    cc = outcome.toeplitz_crosscheck
    assert cc is not None and cc.agrees
    assert cc.max_abs_deviation < 1e-6


def test_toeplitz_crosscheck_flags_order_too_high_fixture():
    # Two sinusoids: effective rank ~4, order 20 is far too high; the
    # independent Toeplitz solve must disagree with the recursion and the
    # discrepancy must surface as a warning, not be silently accepted.
    x = np.asarray(load_fixture("rank_deficient.json")["samples"])
    outcome = analyze_frame(x, 20, "hann", SETTINGS, verify_toeplitz=True)
    cc = outcome.toeplitz_crosscheck
    assert cc is not None and not cc.agrees
    codes = [d.code for d in outcome.result.diagnostics]
    assert "TOEPLITZ_CROSSCHECK_MISMATCH" in codes
    assert outcome.result.warnings, "uncertain conclusion must be listed"


def test_order_must_be_smaller_than_frame_length():
    try:
        analyze_frame(np.ones(8), 8, "hann", SETTINGS)
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError when order >= frame length")
