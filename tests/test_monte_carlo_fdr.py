"""Monte Carlo evidence tests with fixed seeds.

We assert CONCRETE, frozen outcomes (run numbers, seeds, counts, aggregate
FDR) rather than probabilistic niceties. FDR is measured as the mean of
FDP = V/max(R,1) over independent replications; nothing here claims a single
run is controlled.

Reference numbers were generated once with the INDEPENDENT reference loop
(app/simulation.py, which shares no code with the kernel) and frozen below;
regenerating with the same seeds must reproduce them exactly.
"""

from __future__ import annotations

import math

import pytest

from app.diagnostics import account, aggregate_fdr
from app.simulation import (
    DEFAULT_SEED,
    evaluate_stream,
    fixed_signal_stream,
    gaussian_stream,
    null_stream,
    run_experiment,
    run_lordpp_reference,
)
from app.statistics import LordConfig
from app.storage import RunStore

REPS = 100
STEPS = 200
BASE_SEED = DEFAULT_SEED  # 20260927


# -------------------------------------------------------------- global null --


def test_global_null_frozen_aggregates():
    res = run_experiment(
        "null", STEPS, REPS, pi1=0.0, effect=0.0, stream_kind="uniform-null"
    )
    # Frozen aggregate for this exact (seed, reps, steps): exactly two of the
    # 100 all-null runs make any (false) rejection.
    assert res.fdr_estimate == pytest.approx(0.02, abs=1e-12)
    assert sum(r.rejections for r in res.replications) == 2
    assert sum(r.false_discoveries for r in res.replications) == 2
    # Every rejection under the global null is necessarily a false discovery.
    for r in res.replications:
        assert r.false_discoveries == r.rejections
        assert r.true_rejections == 0
    # Named, replayable offending runs with their seeds.
    offenders = {r.run_no: r for r in res.replications if r.rejections > 0}
    assert sorted(offenders) == [33, 40]
    assert offenders[33].seed == 20293215
    assert offenders[40].seed == 20300278
    assert offenders[33].false_discoveries == 1
    # Empirical control with a loose teaching CI on the frozen design.
    assert res.ci95_high < 0.05
    assert res.marginal_power == 0.0


def test_global_null_single_run_is_not_promised_control():
    """Demonstrates WHY we aggregate: run 33 alone has FDP = 1."""
    stream = null_stream(STEPS, seed=20293215)
    rep = evaluate_stream(stream, run_no=33)
    assert rep.rejections == 1
    assert rep.false_discoveries == 1
    assert rep.fdp == 1.0  # this single run "violates" the level; expectation does not


# ------------------------------------------------------------ mixed streams --


def test_mixed_gaussian_frozen_aggregates():
    res = run_experiment(
        "mixed", STEPS, REPS, pi1=0.30, effect=3.5, stream_kind="gaussian-two-sided"
    )
    # Frozen first replication (seed 20260927, 61 alternatives drawn).
    first = res.replications[0]
    assert first.seed == BASE_SEED
    assert first.n_alternative == 61
    assert first.rejections == 17
    assert first.true_rejections == 17
    assert first.false_discoveries == 0
    assert first.fdp == 0.0
    assert first.marginal_power == pytest.approx(17 / 61, rel=1e-12)

    # Across replications: FDR tightly below alpha, power clearly positive.
    assert res.fdr_estimate == pytest.approx(0.0003448275862068965, abs=1e-12)
    assert res.ci95_high < 0.01
    assert 0.30 < res.marginal_power < 0.42
    # Every one of the 100 mixed runs rejects at least one strong alternative.
    assert res.any_rejection_rate == 1.0
    # Exactly one false discovery in total over 100*200 decisions.
    assert sum(r.false_discoveries for r in res.replications) == 1


def test_fixed_signal_mixed_stream_decisions_are_exact():
    stream = fixed_signal_stream(10, [0, 4], seed=42)
    ref = run_lordpp_reference(stream.p_values.tolist())
    # Strong alternatives at positions 0 and 4 both rejected; no null rejected.
    assert ref["rejected"][0] is True
    assert ref["rejected"][4] is True
    for i in (1, 2, 3, 5, 6, 7, 8, 9):
        assert ref["rejected"][i] is False
    acct = account(ref["rejected"], stream.is_alternative.tolist())
    assert acct.rejections == 2
    assert acct.false_discoveries == 0
    assert acct.true_discoveries == 2
    assert acct.fdp == 0.0


# ------------------------------------ service path agrees on a seeded stream --


def test_persisted_service_matches_reference_on_seeded_stream(store: RunStore):
    stream = gaussian_stream(STEPS, 0.30, effect=3.5, seed=BASE_SEED)
    store.create_run(LordConfig.create(), run_id="svc")
    svc_rejected = []
    for i, p in enumerate(stream.p_values, start=1):
        hid = f"H{i:04d}"
        store.reserve("svc", hid)
        row = store.decide("svc", hid, float(p))
        svc_rejected.append(bool(row["rejected"]))
    ref = run_lordpp_reference(stream.p_values.tolist())
    assert svc_rejected == ref["rejected"]
    report = store.replay("svc")
    assert report["rejections"] == ref["n_rejections"] == 17
    assert report["decisions_checked"] == STEPS


# ---------------------------------------------------- diagnostics accounting --


def test_discovery_accounting_identities():
    rejected = [True, True, False, True, False]
    is_alt = [True, False, False, True, True]
    a = account(rejected, is_alt)
    assert a.rejections == 3
    assert a.false_discoveries == 1   # slot 2
    assert a.true_discoveries == 2
    assert a.fdp == 1 / 3
    agg = aggregate_fdr([a, account([False] * 5, [False] * 5)])
    assert agg["n_runs"] == 2
    assert agg["fdr_estimate"] == pytest.approx((1 / 3 + 0.0) / 2, rel=1e-12)
    assert agg["pooled_power"] == pytest.approx(2 / 3, rel=1e-12)


def test_accounting_rejects_mismatched_lengths():
    from app.errors import InputValidationError

    with pytest.raises(InputValidationError):
        account([True, False], [True])


def test_every_replication_is_replayable_with_seed_and_run_number():
    """The log contract: run_no, seed, n, counts, FDP for every replication."""
    res = run_experiment(
        "log", 20, 5, pi1=0.25, effect=3.0, stream_kind="gaussian-two-sided"
    )
    for r in res.replications:
        assert r.run_no >= 1
        assert isinstance(r.seed, int)
        # Replay the exact replication from its recorded seed.
        replay_stream = gaussian_stream(20, 0.25, effect=3.0, seed=r.seed)
        again = evaluate_stream(replay_stream, r.run_no)
        assert again.rejections == r.rejections
        assert again.false_discoveries == r.false_discoveries
        assert math.isclose(again.fdp, r.fdp, rel_tol=1e-12)
