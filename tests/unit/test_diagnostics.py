"""Diagnostics tests: balance SMD, leakage screen policies, cross-checks."""
from __future__ import annotations

import numpy as np
import pytest

from app.core.contracts import (
    ErrorCode,
    EstimationError,
    LeakagePolicy,
    Settings,
    ThetaSource,
)
from app.core.diagnostics import covariate_diagnostics
from app.core.service import run_analysis
from tests.conftest import hand_dataset, prepare


def _diags(payload, settings=None):
    settings = settings or Settings()
    prepared = prepare(payload)
    declared = {n: bool(v) for n, v in
                payload.get("pre_treatment_covariates", {}).items()}
    for n in payload["covariates"]:
        declared.setdefault(n, True)
    diags, warnings = covariate_diagnostics(
        prepared, smd_threshold=settings.smd_threshold,
        alpha=settings.alpha, declared_pre_treatment=declared)
    return prepared, diags, warnings


def test_smd_is_zero_for_balanced_hand_covariate_means():
    # A covariate with identical arm means -> SMD 0, balance_ok True.
    payload = hand_dataset()
    payload["data"]["z"] = [1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0]
    payload["covariates"] = ["z"]
    _, diags, _ = _diags(payload)
    d = diags[0]
    assert d.smd == pytest.approx(0.0, abs=1e-12)
    assert d.balance_ok is True


def test_declared_post_treatment_field_is_flagged():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 1, 400)
    t = (rng.uniform(size=400) < 0.5).astype(int)
    y = t + x + rng.normal(0, 0.1, 400)
    post = y + 10.0 * t  # strongly arm-imbalanced post-treatment field
    payload = {
        "outcome_column": "y", "treatment_column": "t",
        "covariates": ["pre", "post"],
        "pre_treatment_covariates": {"pre": True, "post": False},
        "data": {"y": y.tolist(), "t": t.tolist(),
                 "pre": x.tolist(), "post": post.tolist()},
    }
    _, diags, _ = _diags(payload)
    by_name = {d.name: d for d in diags}
    assert by_name["post"].leakage_flag is True
    assert "declared post-treatment" in by_name["post"].leakage_reason
    assert by_name["pre"].leakage_flag is False


def test_statistical_imbalance_flags_undeclared_suspicious_field():
    # Even if the field is (wrongly) declared pre-treatment, a huge and
    # statistically decisive arm imbalance triggers the statistical screen.
    rng = np.random.default_rng(1)
    n = 400
    x = rng.normal(0, 1, n)
    t = (rng.uniform(size=n) < 0.5).astype(int)
    y = t.astype(float) + rng.normal(0, 0.1, n)
    suspicious = x + 5.0 * t
    payload = {
        "outcome_column": "y", "treatment_column": "t",
        "covariates": ["suspicious"],
        "pre_treatment_covariates": {"suspicious": True},
        "data": {"y": y.tolist(), "t": t.tolist(),
                 "suspicious": suspicious.tolist()},
    }
    _, diags, _ = _diags(payload, Settings(smd_threshold=0.2, alpha=0.01))
    d = diags[0]
    assert d.leakage_flag is True
    assert "suspected post-treatment" in d.leakage_reason
    assert d.balance_ok is False


def test_leakage_fail_policy_rejects_run():
    payload = _leakage_payload(seed=3)
    with pytest.raises(EstimationError) as exc:
        run_analysis(payload, Settings(leakage_policy=LeakagePolicy.FAIL))
    assert exc.value.code is ErrorCode.LEAKAGE_DETECTED
    assert "post" in exc.value.details["covariates"]


def test_leakage_flag_policy_excludes_field_and_keeps_estimate_clean():
    payload = _leakage_payload(seed=3)
    result = run_analysis(payload, Settings(leakage_policy=LeakagePolicy.FLAG))
    assert "post" in result.dropped_covariates
    assert "post" not in result.used_covariates
    # True effect is 1.0; the adjusted estimate must be close, not biased by
    # the post-treatment field.
    assert abs(result.cuped.estimate - 1.0) < 0.15
    assert any("excluded declared post-treatment" in w for w in result.warnings)


def test_leakage_ignore_policy_keeps_field_and_report_biases_it():
    payload = _leakage_payload(seed=3)
    result = run_analysis(payload, Settings(leakage_policy=LeakagePolicy.IGNORE))
    assert "post" in result.used_covariates
    diag = next(d for d in result.diagnostics if d.name == "post")
    # Evidence is retained even though the caller forced inclusion.
    assert diag.leakage_flag is True


def test_no_covariates_is_invalid_payload():
    payload = hand_dataset()
    payload["covariates"] = []
    with pytest.raises(EstimationError) as exc:
        run_analysis(payload, Settings())
    assert exc.value.code is ErrorCode.INVALID_PAYLOAD


def test_regression_se_type_rejected_for_grouping_estimators():
    from app.core.contracts import SEType
    with pytest.raises(EstimationError) as exc:
        run_analysis(hand_dataset(), Settings(se_type=SEType.HC1))
    assert exc.value.code is ErrorCode.CONFIG_ERROR


def test_all_covariates_flagged_leaves_none_and_reports_both_categories():
    # Only a declared post-treatment covariate: under FLAG it is removed and
    # nothing remains -> explicit error naming the excluded field.
    payload = hand_dataset()
    payload["pre_treatment_covariates"] = {"x": False}
    with pytest.raises(EstimationError) as exc:
        run_analysis(payload, Settings(leakage_policy=LeakagePolicy.FLAG))
    assert exc.value.code is ErrorCode.ZERO_VARIANCE_COVARIATE
    assert exc.value.details["excluded_leakage"] == ["x"]


def test_given_theta_service_path_recovers_truth_and_omits_fit_checks():
    payload = hand_dataset()
    payload["given_theta"] = [3.0]
    settings = Settings(theta_source=ThetaSource.GIVEN,
                        leakage_policy=LeakagePolicy.IGNORE)
    result = run_analysis(payload, settings)
    assert result.cuped.estimate == pytest.approx(2.0, abs=1e-12)
    assert result.cuped.theta_se is None
    assert result.cuped.theta_source == "given"
    names = {c.name for c in result.cross_checks}
    assert "cuped_ancova_identity" in names       # identity always applies
    assert "standard_error_reduction" not in names  # fitted-only
    assert "variance_reduction_vs_rsquared" not in names
    # Strict JSON must not contain NaN from the null theta_se.
    import json
    json.dumps(result.to_dict(), allow_nan=False)


def test_given_theta_wrong_length_is_config_error():
    payload = hand_dataset()
    payload["given_theta"] = [3.0, 1.0]
    settings = Settings(theta_source=ThetaSource.GIVEN,
                        leakage_policy=LeakagePolicy.IGNORE)
    with pytest.raises(EstimationError) as exc:
        run_analysis(payload, settings)
    assert exc.value.code is ErrorCode.CONFIG_ERROR


def test_given_theta_missing_is_config_error():
    settings = Settings(theta_source=ThetaSource.GIVEN,
                        leakage_policy=LeakagePolicy.IGNORE)
    with pytest.raises(EstimationError) as exc:
        run_analysis(hand_dataset(), settings)
    assert exc.value.code is ErrorCode.CONFIG_ERROR


def test_variance_identity_scoped_to_control_theta():
    # Review M1: the exact R^2 identity is valid ONLY for control-fitted theta.
    from app.core.contracts import ThetaSource as TS
    names_control = _check_names(hand_dataset(), TS.CONTROL_PRE)
    names_pooled = _check_names(hand_dataset(), TS.POOLED_PRE)
    assert "variance_reduction_vs_rsquared" in names_control
    assert "variance_reduction_vs_rsquared" not in names_pooled
    # SE comparison remains informational (present, never a hard fail).
    assert "standard_error_reduction" in names_pooled


def _check_names(payload, source):
    result = run_analysis(
        payload,
        Settings(theta_source=source, leakage_policy=LeakagePolicy.IGNORE),
    )
    return {c.name: c for c in result.cross_checks}


def test_statistical_imbalance_hint_keeps_covariate_under_flag():
    # Review L5: declared pre-treatment but statistically imbalanced ->
    # retained (hint, not provenance), prominent warning emitted.
    rng = np.random.default_rng(1)
    n = 400
    x = rng.normal(0, 1, n)
    t = (rng.uniform(size=n) < 0.5).astype(int)
    y = t.astype(float) + rng.normal(0, 0.1, n)
    suspicious = x + 5.0 * t
    payload = {
        "outcome_column": "y", "treatment_column": "t",
        "covariates": ["suspicious"],
        "pre_treatment_covariates": {"suspicious": True},
        "data": {"y": y.tolist(), "t": t.tolist(),
                 "suspicious": suspicious.tolist()},
    }
    result = run_analysis(payload, Settings(leakage_policy=LeakagePolicy.FLAG))
    assert "suspicious" in result.used_covariates
    diag = next(d for d in result.diagnostics if d.name == "suspicious")
    assert diag.leakage_flag is True
    assert diag.provenance_post_treatment is False
    assert any("statistical imbalance hint" in w for w in result.warnings)


def test_cross_check_identity_passes_on_hand_data():
    # The hand dataset is CONSTRUCTED with extreme baseline imbalance
    # (control X=1..4 vs treatment X=5..8, SMD ~ 3.1), which the statistical
    # leakage screen flags by design. X is nonetheless truly pre-treatment
    # here (it defines the simulation), so the identity test runs under
    # 'ignore' and still asserts the flag evidence is retained.
    result = run_analysis(hand_dataset(),
                          Settings(leakage_policy=LeakagePolicy.IGNORE))
    assert result.diagnostics[0].leakage_flag is True
    by_name = {c.name: c for c in result.cross_checks}
    identity = by_name["cuped_ancova_identity"]
    assert identity.passed is True
    assert identity.values["abs_difference"] == pytest.approx(0.0, abs=1e-12)
    assert by_name["variance_reduction_vs_rsquared"].passed is True
    assert by_name["standard_error_reduction"].passed is True


def _leakage_payload(seed: int) -> dict:
    rng = np.random.default_rng(seed)
    n = 600
    x = rng.normal(0, 1, n)
    t = (rng.uniform(size=n) < 0.5).astype(int)
    y = 1.0 * t + 0.8 * x + rng.normal(0, 1, n)
    post = y + 0.5 * rng.normal(0, 1, n)
    return {
        "name": "leakage_case",
        "outcome_column": "y", "treatment_column": "t",
        "covariates": ["pre", "post"],
        "pre_treatment_covariates": {"pre": True, "post": False},
        "data": {"y": y.tolist(), "t": t.tolist(),
                 "pre": x.tolist(), "post": post.tolist()},
    }
