"""Fixed-seed Monte Carlo experiments: we measure false discoveries, we do
not assert a single-run guarantee.

All aggregate literals are deterministic under the frozen seeds (computed via
experiments.runner._seed_for).  Statistical assertions are deliberately
phrased as ranges / confidence checks rather than equality to a theorem.
"""

import json
import math

import pytest

from app.diagnostics import summarize_replications
from app.lord3 import run_sequence
from app.simulation import mixed_stream, null_stream
from experiments.runner import (
    ExperimentConfig,
    _seed_for,
    mixed_experiment,
    null_experiment,
    run_experiment,
)

BASE_SEED = 4242
N_REPS = 200
N_TESTS = 300


def _batch(stream_fn):
    stats = []
    for k in range(1, N_REPS + 1):
        stream = stream_fn(_seed_for(BASE_SEED, k))
        result = run_sequence(stream.hypothesis_ids, stream.p_values)
        from app.diagnostics import discovery_stats
        stats.append(discovery_stats(list(result.decisions), stream.is_null))
    return stats


def test_null_batch_fixed_seed_has_zero_rejections_anchor():
    stats = _batch(lambda seed: null_stream(N_TESTS, seed))
    summary = summarize_replications(
        stats, seed=BASE_SEED, stream_kind="null_uniform",
        stream_params={"n_tests": N_TESTS},
    )
    # Frozen literal for this exact seed set.
    assert summary.n_replications == 200
    assert summary.pr_any_rejection == 0.0
    assert summary.mean_rejections == 0.0
    assert summary.fdr_estimate == 0.0
    assert summary.target_fdr == 0.05


def test_mixed_batch_fixed_seed_aggregate_anchors():
    stats = _batch(
        lambda seed: mixed_stream(N_TESTS, 0.2, seed, beta_a=0.05)
    )
    summary = summarize_replications(
        stats, seed=BASE_SEED, stream_kind="mixed_uniform_beta",
        stream_params={"n_tests": N_TESTS, "nonnull_fraction": 0.2},
    )
    # Deterministic literals for this seed set.
    assert summary.pr_any_rejection == 1.0
    assert summary.mean_rejections == pytest.approx(45.765)
    assert summary.mean_false_discoveries == pytest.approx(1.005)
    assert summary.fdr_estimate == pytest.approx(0.021332286566429798)
    assert summary.mean_power == pytest.approx(0.746)
    # Empirical FDR estimate must be below target for THIS batch; documented
    # as an estimate, not a theorem.
    assert summary.fdr_estimate < 0.05


def test_wider_monte_carlo_null_fdr_is_below_alpha_with_margin():
    # Independent probabilistic check (different seed, more/larger runs):
    # under the complete null E[V/R] is tiny; mean FDP stays far under alpha.
    n_reps, n_tests, base = 100, 500, 987654321
    fdps = []
    for k in range(1, n_reps + 1):
        s = null_stream(n_tests, _seed_for(base, k))
        r = run_sequence(s.hypothesis_ids, s.p_values)
        from app.diagnostics import discovery_stats
        st = discovery_stats(list(r.decisions), s.is_null)
        fdps.append(st.fdp)
    assert sum(fdps) / n_reps < 0.01


def test_run_experiment_writes_replayable_jsonl_log(tmp_path):
    cfg = ExperimentConfig(
        name="small_fixed",
        n_replications=3,
        n_tests=50,
        base_seed=7,
        stream_factory=lambda seed: mixed_stream(50, 0.2, seed),
        stream_kind="mixed_uniform_beta",
        stream_params={"n_tests": 50, "nonnull_fraction": 0.2},
    )
    out = run_experiment(cfg, str(tmp_path))
    lines = (tmp_path / "small_fixed.jsonl").read_text().strip().splitlines()
    assert len(lines) == 3
    first = json.loads(lines[0])
    assert first["run_no"] == 1
    assert first["seed"] == _seed_for(7, 1)
    # The log carries replayable intermediate state and a judgement.
    assert {k["index"] for k in first["key_states"]} >= {1, 2, 5, 10, 25, 50}
    assert "threshold" in first["key_states"][0]
    assert first["judgement"]
    assert "stats" in first and "fdp" in first["stats"]
    summary = json.loads((tmp_path / "small_fixed.summary.json").read_text())
    assert summary["n_replications"] == 3
    assert "interpretation" in summary

    # Replay run_no=2 from its logged seed and reproduce every decision.
    rec2 = json.loads(lines[1])
    stream = mixed_stream(50, 0.2, rec2["seed"])
    rerun = run_sequence(stream.hypothesis_ids, stream.p_values)
    assert rerun.rejection_indicator() == rec2["rejection_indicator"]


def test_experiment_config_builders_are_frozen_shapes():
    ne = null_experiment(n_replications=2, n_tests=10)
    assert ne.stream_kind == "null_uniform"
    me = mixed_experiment(n_replications=2, block=True)
    assert me.stream_params["block"] is True


def test_summarize_rejects_empty_input():
    with pytest.raises(ValueError):
        summarize_replications([], seed=1, stream_kind="x", stream_params={})
