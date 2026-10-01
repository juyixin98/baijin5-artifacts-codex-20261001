"""End-to-end pipeline tests: concrete answers and explicit failure classes.

These assert specific numerical results against known truth, not merely that
an interface is callable. They also pin the exact ``status`` /
``error_code`` for each failure mode so an unknown/exception state can never
be silently returned as success.
"""
from __future__ import annotations

import numpy as np
import pytest
from pydantic import ValidationError

from app.contract import (
    BandwidthMethod,
    DataPoint,
    ErrorCode,
    InferenceMethod,
    KernelName,
    RDRequest,
    RunStatus,
    Severity,
)
from app.datasets import (
    density_sorting,
    no_jump,
    sharp_jump,
    sparse_boundary,
)
from app.pipeline import run_analysis


def _req(x, y, label, *, method=BandwidthMethod.MANUAL, bandwidth=0.25, **kw):
    kw.setdefault("bootstrap_reps", 0)
    return RDRequest(
        input_label=label,
        data=[DataPoint(x=float(a), y=float(b)) for a, b in zip(x, y)],
        bandwidth_method=method,
        bandwidth=bandwidth,
        **kw,
    )


@pytest.mark.integration
def test_sharp_jump_recovers_known_jump_with_ci_contains_truth(settings) -> None:
    dgp = sharp_jump(n=3000, seed=101)
    resp = run_analysis(_req(dgp.x, dgp.y, "sharp_jump"), settings)
    assert resp.status is RunStatus.OK
    assert resp.estimate.tau == pytest.approx(3.0, abs=0.15)
    assert resp.estimate.ci_low < 3.0 < resp.estimate.ci_high
    assert resp.estimate.p_value < 0.001
    # the two sides are fit independently -> distinct slopes recovered
    assert resp.left_fit.slope != pytest.approx(resp.right_fit.slope, abs=0.2)
    assert resp.versions["app"]
    assert "O(h^2)" in resp.estimate.bias_notes


@pytest.mark.integration
def test_no_jump_does_not_manufacture_signal(settings) -> None:
    dgp = no_jump(n=3000, seed=202)
    resp = run_analysis(_req(dgp.x, dgp.y, "no_jump"), settings)
    assert resp.status is RunStatus.OK
    assert abs(resp.estimate.tau) < 0.2
    assert resp.estimate.ci_low < 0.0 < resp.estimate.ci_high
    assert resp.estimate.p_value > 0.05


@pytest.mark.integration
def test_density_sorting_flagged_but_outcome_analysis_stays_null(settings) -> None:
    # Larger sample + wider window so the null outcome fit is precise; the
    # density jump is large and detected regardless.
    dgp = density_sorting(n=6000, seed=303)
    resp = run_analysis(_req(dgp.x, dgp.y, "density_sorting", bandwidth=0.4), settings)
    assert resp.status is RunStatus.OK
    # outcome jump absent even though the density jumps
    assert abs(resp.estimate.tau) < 0.4
    assert resp.estimate.ci_low < 0.0 < resp.estimate.ci_high
    density = next(
        d for d in resp.diagnostics if d.code.value == "density_discontinuity"
    )
    assert density.severity is Severity.WARNING
    assert density.details["theta"] == pytest.approx(np.log(2.5), abs=0.3)


@pytest.mark.integration
def test_sparse_boundary_marked_unidentified_with_explicit_reason(settings) -> None:
    dgp = sparse_boundary(n=600, seed=404)
    resp = run_analysis(
        _req(
            dgp.x, dgp.y, "sparse_boundary",
            method=BandwidthMethod.IK_ROT, bandwidth=None,
        ),
        settings,
    )
    assert resp.status is RunStatus.UNIDENTIFIED
    assert resp.error_code in {
        ErrorCode.INSUFFICIENT_DATA,
        ErrorCode.BANDWIDTH_FAILED,
        ErrorCode.SINGULAR_FIT,
    }
    assert resp.estimate is None
    codes = {d.code.value for d in resp.diagnostics}
    assert "sparse_side" in codes or "identification_range" in codes


@pytest.mark.integration
def test_manual_bandwidth_too_narrow_is_unidentified(settings) -> None:
    # nearest data to the cutoff sits at 0.35; h=0.1 reaches nothing
    dgp = sparse_boundary(n=600, seed=404)
    resp = run_analysis(_req(dgp.x, dgp.y, "narrow_h", bandwidth=0.1), settings)
    assert resp.status is RunStatus.UNIDENTIFIED
    assert resp.error_code is ErrorCode.SINGULAR_FIT
    assert resp.estimate is None


def test_invalid_input_no_data_on_side_is_error_not_success(settings) -> None:
    x = np.linspace(0.05, 1, 30)  # every point strictly above cutoff
    y = np.zeros(30)
    resp = run_analysis(_req(x, y, "one_sided", bandwidth=0.5), settings)
    assert resp.status is RunStatus.ERROR
    assert resp.error_code is ErrorCode.INVALID_INPUT
    assert resp.estimate is None
    assert resp.error_message  # explicit reason, never a blank success


def test_invalid_manual_bandwidth_missing_rejected_at_boundary() -> None:
    with pytest.raises(ValidationError):
        RDRequest(
            input_label="badbw",
            data=[DataPoint(x=float(v), y=0.0) for v in np.linspace(-1, 1, 50)],
            bandwidth_method=BandwidthMethod.MANUAL,  # bandwidth omitted
        )


def test_nan_input_rejected_at_boundary() -> None:
    with pytest.raises(ValidationError):
        RDRequest(
            input_label="nan",
            data=[DataPoint(x=float(v), y=np.nan) for v in np.linspace(-1, 1, 50)],
        )


def test_cluster_length_mismatch_is_error(settings) -> None:
    dgp = sharp_jump(n=200, seed=1)
    req = _req(
        dgp.x, dgp.y, "cluster_bad",
        cluster_var=[0, 1, 2],  # wrong length
    )
    resp = run_analysis(req, settings)
    assert resp.status is RunStatus.ERROR
    assert resp.error_code is ErrorCode.INVALID_INPUT


@pytest.mark.integration
def test_hc1_hc3_ordering_and_sign_flip(settings) -> None:
    dgp = sharp_jump(n=2000, seed=505)
    r3 = run_analysis(
        _req(dgp.x, dgp.y, "hc3", inference=InferenceMethod.HC3), settings
    )
    r1 = run_analysis(
        _req(dgp.x, dgp.y, "hc1", inference=InferenceMethod.HC1), settings
    )
    assert r3.estimate.se >= r1.estimate.se
    flipped = run_analysis(
        _req(dgp.x, dgp.y, "flip", treatment_above=False), settings
    )
    assert flipped.estimate.tau == pytest.approx(-r3.estimate.tau, abs=1e-9)


@pytest.mark.integration
def test_bandwidth_multiplier_sweep_increases_effective_n(settings) -> None:
    dgp = sharp_jump(n=3000, seed=606)
    eff = []
    for mult in (0.5, 1.0, 2.0):
        resp = run_analysis(
            _req(dgp.x, dgp.y, f"mult{mult}", bandwidth=0.2,
                 bandwidth_multiplier=mult),
            settings,
        )
        assert resp.status is RunStatus.OK
        eff.append(resp.right_fit.effective_n)
    assert eff[0] < eff[1] < eff[2]


@pytest.mark.integration
def test_all_three_kernels_run_and_agree_on_direction(settings) -> None:
    dgp = sharp_jump(n=3000, seed=707)
    taus = {}
    for kernel in KernelName:
        resp = run_analysis(
            _req(dgp.x, dgp.y, f"kernel_{kernel.value}", kernel=kernel), settings
        )
        assert resp.status is RunStatus.OK
        taus[kernel.value] = resp.estimate.tau
    assert all(t == pytest.approx(3.0, abs=0.25) for t in taus.values())


@pytest.mark.integration
def test_clustered_bootstrap_report(settings) -> None:
    dgp = sharp_jump(n=1200, seed=808)
    # round x to a lattice so clustering by value is meaningful
    x = np.round(dgp.x, 1)
    resp = run_analysis(
        _req(
            x, dgp.y, "clustered",
            cluster_var=list(x),
            bootstrap_reps=199,
            bootstrap_seed=11,
        ),
        settings,
    )
    assert resp.status is RunStatus.OK
    assert resp.bootstrap is not None
    assert resp.bootstrap.clustered is True
    assert resp.bootstrap.reps == 199
    assert resp.bootstrap.p_value <= 0.01
