"""Reproducible experiment driver.

Runs the full estimator against the synthetic scenarios with known truth and
prints progress, computation steps, the verdict for every check, and a final
PASS/FAIL summary with a non-zero process exit code on any failure.

Usage:
    python scripts/run_reproducibility.py [--n 4000] [--seed 20260927]
"""

from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from app.core import estimator, synthetic
from app.core.config import PROJECT_ROOT, EstimationConfig, load_config
from app.core.contracts import CovariateDeclaration, ThetaSource
from app.core.errors import EstimationError
from app.core.logging_setup import configure_logging


def _verdict(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=4000)
    parser.add_argument("--seed", type=int, default=20260927)
    parser.add_argument("--true-effect", type=float, default=2.0)
    args = parser.parse_args()

    run_id = f"repro-{uuid.uuid4().hex[:12]}"
    cfg = load_config()
    configure_logging(cfg.absolute_log_dir, cfg.log_level, run_id=run_id)
    print(f"=== CUPED reproducibility run {run_id} ===")
    print(f"n={args.n} seed={args.seed} true_effect={args.true_effect}")
    print(f"numpy={np.__version__}")
    print()

    checks: list[tuple[str, bool, str]] = []

    # 1. Balanced experiment with informative covariate.
    print("[1/5] balanced + correlated covariate")
    ds = synthetic.generate("balanced", n=args.n, true_effect=args.true_effect, seed=args.seed)
    res = estimator.estimate(
        "balanced", ds.unit_id, ds.treatment, ds.outcome, ds.covariates,
        ds.declarations, EstimationConfig(), run_id, theta_source=ThetaSource.CONTROL,
    )
    print(f"  unadjusted: est={res.unadjusted.estimate:.4f} se={res.unadjusted.se:.4f}")
    print(f"  adjusted:   est={res.adjusted.estimate:.4f} se={res.adjusted.se:.4f}")
    print(f"  theta={ {k: round(v,4) for k,v in res.theta.coefficients.items()} } "
          f"(source={res.theta.source}, n_used={res.theta.n_units_used})")
    print(f"  variance_reduction={res.variance_reduction:.4f} "
          f"reference OLS beta_T={res.reference_regression['beta_treatment']:.4f} "
          f"se_hc1={res.reference_regression['se_hc1']:.4f} R2={res.reference_regression['r_squared']:.4f}")
    c1 = res.variance_reduction > 0.80
    c2 = abs(res.adjusted.estimate - args.true_effect) < 0.15
    c3 = abs(res.theta.coefficients["x_pre"] - 3.0) < 0.15
    checks.append((f"[1] variance reduction > 0.80 (got {res.variance_reduction:.3f})", c1, ""))
    checks.append((f"[1] adjusted effect within 0.15 of truth (err={abs(res.adjusted.estimate-args.true_effect):.3f})", c2, ""))
    checks.append((f"[1] theta within 0.15 of beta=3 (got {res.theta.coefficients['x_pre']:.3f})", c3, ""))
    print(f"  -> {_verdict(all([c1, c2, c3]))}\n")

    # 2. Uncorrelated covariate: no variance gain.
    print("[2/5] uncorrelated covariate")
    ds2 = synthetic.generate("no_correlate", n=args.n, true_effect=args.true_effect, seed=args.seed + 1)
    res2 = estimator.estimate(
        "no_correlate", ds2.unit_id, ds2.treatment, ds2.outcome, ds2.covariates,
        ds2.declarations, EstimationConfig(), run_id, theta_source=ThetaSource.CONTROL,
    )
    print(f"  se unadjusted={res2.unadjusted.se:.4f} adjusted={res2.adjusted.se:.4f} "
          f"variance_reduction={res2.variance_reduction:.4f}")
    ok = abs(res2.variance_reduction) < 0.10
    checks.append((f"[2] |variance reduction| < 0.10 (got {res2.variance_reduction:.3f})", ok, ""))
    print(f"  -> {_verdict(ok)}\n")

    # 3. Imbalanced realized draw with moderate n.
    print("[3/5] covariate imbalance realization")
    ds3 = synthetic.generate(
        "imbalanced", n=500, true_effect=1.0, beta_pre=3.0,
        seed=args.seed + 2, imbalance_smd=0.30,
    )
    res3 = estimator.estimate(
        "imbalanced", ds3.unit_id, ds3.treatment, ds3.outcome, ds3.covariates,
        ds3.declarations, EstimationConfig(), run_id, theta_source=ThetaSource.CONTROL,
    )
    smd = next(d.standardized_mean_diff for d in res3.diagnostics if d.name == "x_pre")
    unadj_err = abs(res3.unadjusted.estimate - 1.0)
    adj_err = abs(res3.adjusted.estimate - 1.0)
    print(f"  realized |SMD|={abs(smd):.3f}")
    print(f"  unadjusted est={res3.unadjusted.estimate:.4f} (error {unadj_err:.4f})")
    print(f"  adjusted   est={res3.adjusted.estimate:.4f} (error {adj_err:.4f})")
    ok = abs(smd) >= 0.25 and adj_err < unadj_err
    checks.append((f"[3] adjustment reduces error under imbalance ({unadj_err:.3f} -> {adj_err:.3f})", ok, ""))
    print(f"  -> {_verdict(ok)}\n")

    # 4. Leakage field must be rejected with the typed failure.
    print("[4/5] leakage field rejection")
    ds4 = synthetic.generate("leakage", n=2000, true_effect=args.true_effect, seed=args.seed + 3)
    try:
        estimator.estimate(
            "leakage", ds4.unit_id, ds4.treatment, ds4.outcome, ds4.covariates,
            ds4.declarations, EstimationConfig(), run_id,
        )
        ok = False
        detail = "estimator accepted a post-treatment covariate"
    except EstimationError as exc:
        ok = exc.code.value == "LEAKED_COVARIATE"
        detail = f"rejected with code={exc.code.value}"
    print(f"  {detail}")
    checks.append(("[4] leakage covariate -> LEAKED_COVARIATE", ok, detail))
    print(f"  -> {_verdict(ok)}\n")

    # 5. Missing + zero variance policies.
    print("[5/5] explicit missing and zero-variance policies")
    ds5 = synthetic.generate("balanced", n=2000, seed=args.seed + 4, missing_fraction=0.08)
    ds5.covariates["constant"] = np.full(len(ds5.outcome), 4.0)
    decls5 = ds5.declarations + [CovariateDeclaration("constant", True)]
    res5 = estimator.estimate(
        "policies", ds5.unit_id, ds5.treatment, ds5.outcome, ds5.covariates,
        decls5, EstimationConfig(), run_id,
    )
    imputed = sum(d.n_imputed for d in res5.diagnostics)
    dropped = [d.name for d in res5.diagnostics if d.dropped]
    print(f"  imputed={imputed} dropped_constant={dropped} status={res5.status}")
    ok = imputed > 0 and dropped == ["constant"] and res5.status == "completed"
    checks.append(("[5] mean-impute missing + drop constant column", ok, ""))
    print(f"  -> {_verdict(ok)}\n")

    n_pass = sum(1 for _, ok, _ in checks if ok)
    print("=== summary ===")
    for name, ok, _ in checks:
        print(f"  [{_verdict(ok)}] {name}")
    print(f"\n{n_pass}/{len(checks)} checks passed")
    return 0 if n_pass == len(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
