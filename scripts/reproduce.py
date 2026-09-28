#!/usr/bin/env python3
"""Reusable reproduction & verification script.

Runs, independently of the test suite:
  1. the four misspecification scenarios (known true ATE = 0.5) over several
     seeds, reporting bias, mean SE and 95% CI coverage;
  2. an exact numerical-agreement check against the independent oracle
     (scipy L-BFGS-B propensity + lstsq outcome + explicit-loop AIPW) for a
     fixed split;
  3. the leakage and fold-misalignment audits, proving each FAILS when the
     pipeline is tampered with (detection has power);
  4. the clustered DGP, asserting independent units are clusters and the
     cluster SE matches the oracle;
  5. the four error categories (input / state / resource / computation).

Outputs:
  stdout : human-readable PASS/FAIL lines with run numbers and key values
  report : JSON written to --out (default artifacts/reproduction_report.json)

Exit status 0 only if every check passes.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from aipw.contract import (  # noqa: E402
    Config, Estimand, ErrorCategory, StateConflictError,
)
from aipw.crossfit import (  # noqa: E402
    OOFPredictions, cross_fit, make_stratified_folds,
)
from aipw.diagnostics import audit_scaler_stats, verify_oof_predictions  # noqa: E402
from aipw.estimators import estimate  # noqa: E402
from aipw.pipeline import assign_folds, run_estimate  # noqa: E402
from aipw.simulation import KNOWN_TAU, make_clustered_dataset, make_dataset  # noqa: E402

from fixture_utils import fake_scaler_stats  # noqa: E402
from oracle import ref_aipw_ate, ref_cluster_variance  # noqa: E402

SCENARIOS = ["both_correct", "ps_only", "outcome_only", "both_wrong"]


class Recorder:
    def __init__(self) -> None:
        self.checks: list[dict] = []
        self.run_no = 0

    def check(self, name: str, passed: bool, detail: dict) -> bool:
        self.run_no += 1
        self.checks.append({"run_no": self.run_no, "name": name,
                            "passed": bool(passed), "detail": detail})
        flag = "PASS" if passed else "FAIL"
        print(f"[{self.run_no:03d}] {flag} {name} :: "
              f"{json.dumps(detail, sort_keys=True)[:220]}")
        return bool(passed)

    @property
    def all_passed(self) -> bool:
        return all(c["passed"] for c in self.checks)


def monte_carlo(rec: Recorder, trials: int, n: int) -> None:
    cfg = Config.default()
    for scenario in SCENARIOS:
        biases, ses, covered = [], [], 0
        t0 = time.time()
        for s in range(trials):
            d = make_dataset(seed=10_000 + s, n=n, scenario=scenario)
            res = run_estimate(d.dataset, cfg, seed=7919 * (s + 1))
            biases.append(res.point - d.true_ate)
            ses.append(res.se)
            covered += res.ci_lower < d.true_ate < res.ci_upper
        coverage = covered / trials
        bias = float(np.mean(biases))
        # DR scenarios (all but both_wrong) must be essentially unbiased with
        # good coverage; both_wrong has no guarantee and is reported, not gated.
        passed = (abs(bias) < 0.08 and coverage >= 0.75) if scenario != "both_wrong" \
            else True
        rec.check(f"mc:{scenario}", passed, {
            "trials": trials, "n": n, "true_ate": KNOWN_TAU,
            "bias": bias, "rmse": float(np.sqrt(np.mean(np.square(biases)))),
            "mean_se": float(np.mean(ses)), "coverage95": coverage,
            "seconds": round(time.time() - t0, 2),
            "dr_guaranteed": scenario != "both_wrong",
        })


def oracle_agreement(rec: Recorder) -> None:
    cfg = Config.default()
    for scenario in SCENARIOS:
        d = make_dataset(seed=2025, n=3000, scenario=scenario)
        folds = make_stratified_folds(d.dataset, cfg.folds,
                                      np.random.default_rng(555))
        oof = cross_fit(d.dataset, cfg, folds)
        comp = estimate(d.dataset.a, d.dataset.y, oof.propensity, oof.mu0,
                        oof.mu1, cfg.trim_propensity, Estimand.ATE, False)
        ref = ref_aipw_ate(d.dataset, folds, cfg.folds)
        diff_point = abs(comp.point - ref["point"])
        diff_ipw = abs(comp.ipw - ref["ipw"])
        diff_gc = abs(comp.gcomp - ref["gcomp"])
        # independent numerical paths (lstsq vs standardized solve, L-BFGS-B
        # vs IRLS) agreeing to ~1e-7 is strong evidence without demanding
        # bitwise identity across different floating-point orderings
        ok = max(diff_point, diff_ipw, diff_gc) < 1e-6
        rec.check(f"oracle-agreement:{scenario}", ok, {
            "max_abs_diff": max(diff_point, diff_ipw, diff_gc),
            "tolerance": 1e-6,
            "aipw": comp.point, "oracle": ref["point"],
        })


def audits_have_power(rec: Recorder) -> None:
    cfg = Config.default()
    d = make_dataset(seed=31, n=2000, scenario="both_correct")
    folds = make_stratified_folds(d.dataset, cfg.folds,
                                  np.random.default_rng(4))
    oof = cross_fit(d.dataset, cfg, folds)

    # clean pipeline must pass both audits
    leak = audit_scaler_stats(d.dataset, folds, cfg.folds, oof)
    refit = verify_oof_predictions(d.dataset, cfg, folds, oof)
    rec.check("audit:clean-pipeline-passes", leak.passed and refit.passed,
              {"scaler_checks": len(leak.folds),
               "refit_max_diff": max(c.propensity_max_abs_diff
                                     for c in refit.folds)})

    # tampered (all-rows) scaler stats MUST be rejected
    tampered = OOFPredictions(
        fold_id=oof.fold_id, propensity=oof.propensity, mu0=oof.mu0,
        mu1=oof.mu1, diagnostics=oof.diagnostics,
        scalers0=fake_scaler_stats(folds, d.dataset.x, d.dataset.a, 0,
                                   cfg.folds, leaky=True),
        scalers1=fake_scaler_stats(folds, d.dataset.x, d.dataset.a, 1,
                                   cfg.folds, leaky=True))
    detected = False
    detail = ""
    try:
        audit_scaler_stats(d.dataset, folds, cfg.folds, tampered)
    except StateConflictError as exc:
        detected, detail = True, exc.message
    rec.check("audit:leakage-detected", detected, {"message": detail})

    # cyclic fold misalignment MUST be rejected by the independent refit
    k = cfg.folds
    bad = [np.empty_like(oof.propensity) for _ in range(3)]
    for f in range(k):
        dst = np.flatnonzero(folds == f)
        src = np.flatnonzero(folds == (f + 1) % k)
        pick = src[np.arange(dst.size) % src.size]
        bad[0][dst] = oof.propensity[pick]
        bad[1][dst] = oof.mu0[pick]
        bad[2][dst] = oof.mu1[pick]
    swapped = OOFPredictions(fold_id=oof.fold_id, propensity=bad[0],
                             mu0=bad[1], mu1=bad[2],
                             diagnostics=oof.diagnostics,
                             scalers0=oof.scalers0, scalers1=oof.scalers1)
    detected = False
    try:
        verify_oof_predictions(d.dataset, cfg, folds, swapped)
    except StateConflictError as exc:
        detected, detail = True, exc.message
    rec.check("audit:fold-misalignment-detected", detected, {"message": detail})


def cluster_case(rec: Recorder) -> None:
    cfg = Config.from_dict({"cluster": {"enabled": True}})
    d = make_clustered_dataset(seed=7, n_clusters=80, members_per_cluster=10)
    folds = assign_folds(d.dataset, cfg, np.random.default_rng(0))
    res = run_estimate(d.dataset, cfg, seed=0, fold_id=folds)
    ref = ref_cluster_variance(d.dataset, folds, cfg.folds)
    folds_by_cluster = all(
        np.unique(folds[d.dataset.clusters == g]).size == 1
        for g in np.unique(d.dataset.clusters))
    ok = (abs(res.point - ref["point"]) < 1e-6
          and abs(res.se - ref["cluster_se"]) / ref["cluster_se"] < 1e-7
          and res.independent_units == d.n_clusters and folds_by_cluster
          and res.ci_lower < d.true_ate < res.ci_upper)
    rec.check("cluster:independent-units-and-oracle-se", ok, {
        "point": res.point, "oracle_point": ref["point"],
        "cluster_se": res.se, "oracle_cluster_se": ref["cluster_se"],
        "independent_units": res.independent_units,
        "n_rows": d.dataset.n, "fold_within_cluster": folds_by_cluster,
    })


def error_taxonomy(rec: Recorder) -> None:
    from fastapi.testclient import TestClient
    from aipw.api import create_app
    from aipw.simulation import make_dataset as mk

    with TestClient(create_app(":memory:")) as client:
        d = mk(seed=0, n=100)
        good = {"data": {"x": d.dataset.x.tolist(), "a": d.dataset.a.tolist(),
                         "y": d.dataset.y.tolist()}, "run_id": "r"}
        # 400 input
        bad = json.loads(json.dumps(good))
        bad["data"]["a"][0] = 9
        r1 = client.post("/estimate", json=bad)
        # 409 state (reuse run id)
        client.post("/estimate", json=good)
        r2 = client.post("/estimate", json=good)
        # 507 resource
        big = json.loads(json.dumps(good))
        big["run_id"] = "big"
        with TestClient(create_app(":memory:")) as c2:
            c2.app.state.max_rows = 10
            r3 = c2.post("/estimate", json=big)
        # 422 computation (deterministic treatment -> separation)
        rng = np.random.default_rng(0)
        x = rng.normal(size=(300, 2))
        a = (x[:, 0] > 0).astype(float)
        sep = {"data": {"x": x.tolist(), "a": a.tolist(),
                        "y": (rng.normal(size=300) + a).tolist()},
               "run_id": "sep"}
        r4 = client.post("/estimate", json=sep)

    mapping = [("input", r1.status_code, 400, r1.json()["error"]["category"],
                ErrorCategory.INPUT.value),
               ("state", r2.status_code, 409, r2.json()["error"]["category"],
                ErrorCategory.STATE.value),
               ("resource", r3.status_code, 507, r3.json()["error"]["category"],
                ErrorCategory.RESOURCE.value),
               ("computation", r4.status_code, 422,
                r4.json()["error"]["category"],
                ErrorCategory.COMPUTATION.value)]
    for name, got, want_status, got_cat, want_cat in mapping:
        rec.check(f"error:{name}", got == want_status and got_cat == want_cat,
                  {"http": got, "expected_http": want_status,
                   "category": got_cat})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--trials", type=int, default=15)
    parser.add_argument("--n", type=int, default=3000)
    parser.add_argument("--out", type=Path,
                        default=ROOT / "artifacts" / "reproduction_report.json")
    args = parser.parse_args()

    rec = Recorder()
    print(f"true ATE on every synthetic scenario = {KNOWN_TAU}")
    monte_carlo(rec, args.trials, args.n)
    oracle_agreement(rec)
    audits_have_power(rec)
    cluster_case(rec)
    error_taxonomy(rec)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    report = {"true_ate": KNOWN_TAU, "trials": args.trials, "n": args.n,
              "checks": rec.checks,
              "all_passed": rec.all_passed}
    args.out.write_text(json.dumps(report, indent=2))
    print(f"\n{sum(c['passed'] for c in rec.checks)}/{len(rec.checks)} "
          f"checks passed; report -> {args.out}")
    return 0 if rec.all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
