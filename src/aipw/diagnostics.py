"""Independent diagnostics and evidence checks.

These functions do NOT participate in estimation. They re-derive quantities
through independent code paths so that tests (and the ``/runs/{id}/verify``
endpoint) can catch:

* train/validation leakage in standardization (EXACT stats comparison);
* fold-id misalignment / permuted out-of-fold predictions (independent refit);
* disagreement between AIPW and its two component estimators.

Every check returns a structured report; none of them silently logs.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .contract import Config, Dataset, StateConflictError
from .crossfit import OOFPredictions
from .models import LogisticRegression, OLSRidge

# Exact-equality tolerances. Scaler stats are one-pass mean/std on the same
# float64 rows, so a clean pipeline matches to machine precision; a scaler
# that saw even one extra row differs by far more than 1e-10 at n ~ 10^3.
SCALER_STATS_TOL = 1e-10
REFIT_TOL = 1e-8


@dataclass(frozen=True)
class ScalerStatCheck:
    fold: int
    arm: int                 # 0 = control outcome model, 1 = treated
    mean_max_abs_diff: float
    scale_max_abs_diff: float
    leaky_allrows_diff: float  # distance to stats fit on ALL arm rows; >0 proves
    passed: bool              # the test has power to distinguish the two sources


@dataclass(frozen=True)
class LeakageReport:
    folds: tuple[ScalerStatCheck, ...]
    passed: bool
    tolerance: float

    @property
    def leakage_suspected(self) -> bool:
        return not self.passed

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "tolerance": self.tolerance,
                "folds": [f.__dict__ for f in self.folds]}


def _arm_stats(x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    return x.mean(axis=0), np.where(x.std(axis=0) > 1e-12, x.std(axis=0), 1.0)


def audit_scaler_stats(dataset: Dataset, fold_id: np.ndarray, k: int,
                       oof: OOFPredictions,
                       *, standardize: bool = True,
                       tol: float = SCALER_STATS_TOL) -> LeakageReport:
    """Prove every per-fold scaler was fit on training rows of its arm only.

    For each fold and each outcome arm we independently recompute the mean and
    scale on exactly the rows the cross-fitter claims to have used
    (train fold ∩ treatment arm) and compare with the stats carried in OOF.
    We additionally recompute stats on ALL rows of that arm (the leaky
    alternative); those must differ, demonstrating the check has discriminating
    power rather than passing vacuously.
    """
    checks: list[ScalerStatCheck] = []
    all_pass = True
    p = dataset.x.shape[1]
    identity_mean, identity_scale = np.zeros(p), np.ones(p)
    for f in range(k):
        train = fold_id != f
        for arm, stored in ((0, oof.scalers0[f]), (1, oof.scalers1[f])):
            arm_rows = train & (dataset.a == arm)
            if standardize:
                mean_exp, scale_exp = _arm_stats(dataset.x[arm_rows])
                mean_all, scale_all = _arm_stats(dataset.x[dataset.a == arm])
            else:
                # no standardization: pipeline must carry identity stats
                mean_exp, scale_exp = identity_mean, identity_scale
                mean_all, scale_all = _arm_stats(dataset.x[dataset.a == arm])
            d_mean = float(np.max(np.abs(stored.mean - mean_exp)))
            d_scale = float(np.max(np.abs(stored.scale - scale_exp)))
            d_leak = float(max(np.max(np.abs(stored.mean - mean_all)),
                               np.max(np.abs(stored.scale - scale_all))))
            passed = max(d_mean, d_scale) <= tol
            all_pass &= passed
            checks.append(ScalerStatCheck(
                fold=f, arm=arm, mean_max_abs_diff=d_mean,
                scale_max_abs_diff=d_scale, leaky_allrows_diff=d_leak,
                passed=passed,
            ))
    report = LeakageReport(folds=tuple(checks), passed=all_pass, tolerance=tol)
    if not all_pass:
        worst = max(max(c.mean_max_abs_diff, c.scale_max_abs_diff)
                    for c in checks)
        raise StateConflictError(
            "a per-fold standardizer was not fit on its claimed training rows: "
            "train/validation leakage in standardization",
            details={"max_abs_diff": worst, "tolerance": tol},
        )
    # power assertion: the leaky alternative must actually look different
    if all(c.leaky_allrows_diff <= tol for c in checks):
        raise StateConflictError(
            "train-only and all-rows scaler stats are identical; the leakage "
            "audit has no discriminating power for this dataset"
        )
    return report


@dataclass(frozen=True)
class FoldRefitCheck:
    fold: int
    propensity_max_abs_diff: float
    mu0_max_abs_diff: float
    mu1_max_abs_diff: float
    passed: bool


@dataclass(frozen=True)
class OOFVerificationReport:
    passed: bool
    folds: tuple[FoldRefitCheck, ...]
    tolerance: float

    def to_dict(self) -> dict[str, Any]:
        return {"passed": self.passed, "tolerance": self.tolerance,
                "folds": [f.__dict__ for f in self.folds]}


def verify_oof_predictions(dataset: Dataset, config: Config,
                           fold_id: np.ndarray, oof: OOFPredictions,
                           *, tol: float = REFIT_TOL) -> OOFVerificationReport:
    """Independently refit every fold's nuisance models and compare OOF rows.

    A permuted propensity/mu array, a shifted fold numbering or any mismatch
    between the rows a model was trained on and the rows it predicts shows up
    here as a mismatch and raises (fatal: inference on misaligned OOF
    predictions is meaningless).
    """
    checks: list[FoldRefitCheck] = []
    all_pass = True
    for f in range(config.folds):
        valid = fold_id == f
        train = ~valid
        treated = dataset.a[train] == 1
        ps_model = LogisticRegression(
            l2_penalty=config.propensity_model.l2_penalty,
            max_iter=config.propensity_model.max_iter,
            tol=config.propensity_model.tol,
        ).fit(dataset.x[train], dataset.a[train])
        m0 = OLSRidge(standardize=config.outcome_models.standardize).fit(
            dataset.x[train][~treated], dataset.y[train][~treated])
        m1 = OLSRidge(standardize=config.outcome_models.standardize).fit(
            dataset.x[train][treated], dataset.y[train][treated])

        d_ps = float(np.max(np.abs(ps_model.predict_proba(dataset.x[valid])
                                   - oof.propensity[valid])))
        d0 = float(np.max(np.abs(m0.predict(dataset.x[valid])
                                 - oof.mu0[valid])))
        d1 = float(np.max(np.abs(m1.predict(dataset.x[valid])
                                 - oof.mu1[valid])))
        passed = max(d_ps, d0, d1) <= tol
        all_pass &= passed
        checks.append(FoldRefitCheck(f, d_ps, d0, d1, passed))

    report = OOFVerificationReport(passed=all_pass, folds=tuple(checks),
                                   tolerance=tol)
    if not all_pass:
        worst = max(max(c.propensity_max_abs_diff, c.mu0_max_abs_diff,
                        c.mu1_max_abs_diff) for c in checks)
        raise StateConflictError(
            "out-of-fold predictions do not match an independent per-fold "
            "refit: fold numbering or row alignment is wrong",
            details={"max_abs_diff": worst, "tolerance": tol},
        )
    return report


@dataclass(frozen=True)
class EstimatorAgreement:
    gcomp: float
    ipw: float
    aipw: float
    abs_spread: float
    note: str = field(default="")

    def to_dict(self) -> dict[str, Any]:
        return {"gcomp": self.gcomp, "ipw": self.ipw, "aipw": self.aipw,
                "abs_spread": self.abs_spread, "note": self.note}


def estimator_agreement(gcomp: float, ipw: float, aipw: float) -> EstimatorAgreement:
    spread = float(max(abs(gcomp - aipw), abs(ipw - aipw)))
    note = (
        "component estimators agree; consistent with both models behaving well, "
        "but NOT proof of correctness (AIPW is only guaranteed under the "
        "double-robust condition)"
        if spread < 0.05 else
        "component estimators disagree; if both models are wrong the AIPW "
        "point is not salvaged by augmentation"
    )
    return EstimatorAgreement(gcomp=gcomp, ipw=ipw, aipw=aipw,
                              abs_spread=spread, note=note)
