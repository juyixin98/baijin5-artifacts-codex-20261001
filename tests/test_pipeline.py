"""End-to-end pipeline tests: boundary validation, DR scenarios, cluster runs."""
from __future__ import annotations

import numpy as np
import pytest

from aipw.contract import (
    Config, Dataset, DOUBLE_ROBUST_CONDITION, InputError,
)
from aipw.pipeline import double_robust_condition, run_estimate, validate_dataset


def _payload(n: int = 100, p: int = 2, seed: int = 0, **overrides):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, p))
    a = (rng.uniform(size=n) > 0.5).astype(float)
    y = rng.normal(size=n)
    pay = {"x": x.tolist(), "a": a.tolist(), "y": y.tolist()}
    pay.update(overrides)
    return pay


# ------------------------------------------------------------------ #
# Input validation: concrete failure categories, not "it raised"
# ------------------------------------------------------------------ #
def test_missing_field_is_input_error():
    p = _payload()
    del p["y"]
    with pytest.raises(InputError, match="missing required field"):
        validate_dataset(p)


def test_non_numeric_is_input_error():
    p = _payload()
    p["a"][0] = "treated"
    with pytest.raises(InputError, match="numeric arrays"):
        validate_dataset(p)


def test_non_binary_treatment_is_input_error():
    p = _payload()
    p["a"][0] = 2.0
    with pytest.raises(InputError, match="only 0 and 1"):
        validate_dataset(p)


def test_row_count_mismatch_is_input_error():
    p = _payload(n=100)
    p["y"] = p["y"][:-1]
    with pytest.raises(InputError, match="same row count"):
        validate_dataset(p)


def test_non_finite_values_are_input_errors():
    p = _payload()
    p["x"][0][0] = float("nan")
    with pytest.raises(InputError, match="finite values"):
        validate_dataset(p)


def test_too_few_rows_is_input_error():
    with pytest.raises(InputError, match="at least 10 rows"):
        validate_dataset(_payload(n=6))


def test_single_treatment_stratum_is_input_error():
    p = _payload()
    p["a"] = [1.0] * len(p["a"])
    with pytest.raises(InputError, match="2 treated and 2 control"):
        validate_dataset(p)


def test_cluster_ids_must_align():
    p = _payload()
    p["cluster_id"] = [0, 1, 2]
    with pytest.raises(InputError, match="one entry per row"):
        validate_dataset(p)


# ------------------------------------------------------------------ #
# End-to-end runs
# ------------------------------------------------------------------ #
def test_run_is_reproducible_from_seed_and_carries_run_id(both_correct):
    cfg = Config.default()
    r1 = run_estimate(both_correct.dataset, cfg, seed=77, run_id="run-A")
    r2 = run_estimate(both_correct.dataset, cfg, seed=77, run_id="run-B")
    assert r1.point == r2.point and r1.se == r2.se
    assert r1.run_id == "run-A"
    r3 = run_estimate(both_correct.dataset, cfg, seed=78)
    assert r3.point != r1.point or r3.se != r1.se  # different seed => different split


def test_run_result_contains_components_and_diagnostics(both_correct):
    res = run_estimate(both_correct.dataset, Config.default(), seed=1)
    assert res.folds == 5
    assert len(res.fold_diagnostics) == 5
    assert res.n == both_correct.dataset.n
    assert res.method == "aipw_ht"
    assert res.clustered is False
    assert res.independent_units == res.n
    assert res.ci_lower < res.point < res.ci_upper
    # in-sample comparator exists and is labelled as such in docs, not used
    assert isinstance(res.insample_gcomp_point, float)


def test_both_correct_run_covers_truth(both_correct, known_tau):
    res = run_estimate(both_correct.dataset, Config.default(), seed=2024)
    assert res.ci_lower < known_tau < res.ci_upper
    assert abs(res.point - known_tau) < 0.05


def test_dr_guarantee_wording_does_not_claim_more_than_conditional():
    text = double_robust_condition()
    assert "AT LEAST ONE" in text
    assert "both are misspecified" in text
    assert text == DOUBLE_ROBUST_CONDITION


def test_cluster_run_independent_units_are_clusters(clustered):
    cfg = Config.from_dict({"cluster": {"enabled": True}})
    res = run_estimate(clustered.dataset, cfg, seed=0)
    assert res.clustered is True
    assert res.independent_units == clustered.n_clusters
    assert len(res.fold_diagnostics) == cfg.folds
    # cluster folds: whole clusters assigned together
    folds = None
    from aipw.pipeline import assign_folds
    folds = assign_folds(clustered.dataset, cfg, np.random.default_rng(0))
    for c in np.unique(clustered.dataset.clusters):
        assert np.unique(folds[clustered.dataset.clusters == c]).size == 1


def test_cluster_run_matches_independent_oracle(clustered):
    from oracle import ref_cluster_variance
    from aipw.pipeline import assign_folds
    cfg = Config.from_dict({"cluster": {"enabled": True}})
    ds = clustered.dataset
    folds = assign_folds(ds, cfg, np.random.default_rng(0))
    ref = ref_cluster_variance(ds, folds, cfg.folds)
    res = run_estimate(ds, cfg, seed=0, fold_id=folds)
    assert res.point == pytest.approx(ref["point"], abs=1e-9)
    assert res.se == pytest.approx(ref["cluster_se"], rel=1e-9)
    assert res.ci_lower < clustered.true_ate < res.ci_upper


def test_cluster_request_without_ids_is_input_error(both_correct):
    cfg = Config.from_dict({"cluster": {"enabled": True}})
    with pytest.raises(InputError, match="cluster_id"):
        # emulate the API-side guard directly
        from aipw.api import _config_for_data
        _config_for_data(cfg, both_correct.dataset)


def test_seeds_repeatedly_recover_truth_under_each_dr_scenario():
    # Monte-Carlo sanity over several seeds for the three informative scenarios.
    # Both-correct and one-correct: bias small AND empirical coverage high.
    from aipw.simulation import KNOWN_TAU, make_dataset
    cfg = Config.default()
    for scenario, expect_dr in (("both_correct", True),
                                ("ps_only", True),
                                ("outcome_only", True)):
        covered = 0
        trials = 12
        biases = []
        for s in range(trials):
            d = make_dataset(seed=1000 + s, n=3000, scenario=scenario)
            r = run_estimate(d.dataset, cfg, seed=31 * s)
            biases.append(r.point - KNOWN_TAU)
            covered += r.ci_lower < KNOWN_TAU < r.ci_upper
        assert abs(np.mean(biases)) < 0.06, scenario
        assert covered / trials >= 0.75, (scenario, covered)


def test_both_wrong_scenario_does_not_promise_unbiasedness():
    # The package must return an answer and a CI, but tests do NOT require the
    # answer to be on truth; this documents the boundary of the DR guarantee.
    from aipw.simulation import make_dataset
    d = make_dataset(seed=4242, n=4000, scenario="both_wrong")
    res = run_estimate(d.dataset, Config.default(), seed=1)
    assert np.isfinite(res.point) and res.se > 0
