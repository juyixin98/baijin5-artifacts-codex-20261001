"""AIPW estimation kernel.

Pipeline (all nuisance predictions are strictly out of fold):

    for fold k:
        scaler_k <- fit on rows with fold != k           (train-only fit)
        g_k      <- treatment model fit on train rows
        m1_k     <- outcome model fit on treated train rows
        m0_k     <- outcome model fit on control train rows
        fill predictions ONLY for rows with fold == k    (the valid rows)

    psi_i = mu1_i - mu0_i
            + A_i / e_i * (Y_i - mu1_i)
            - (1 - A_i) / (1 - e_i) * (Y_i - mu0_i)
    tau_ATE = mean_i psi_i

Inference uses the uncentered/centered influence function.  Under iid
sampling the independent unit is the individual; with cluster labels the
independent unit is the cluster, and influence contributions are summed
within cluster before computing the variance.

Double robustness (acceptance criterion 4): tau_ATE is consistent if
**either** the propensity model **or** both outcome regressions are
consistent (and standard regularity/positivity conditions hold).  If *all*
three are misspecified, the bias generally does not vanish - the tests
include such a fixture and assert bias is detectably present, rather than
claiming arbitrary misspecification is harmless.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import AipwConfig
from .contract import Dataset, check_positivity
from .errors import ComputationError, InputError
from .folds import FoldSplit, check_oo_alignment, make_folds
from .models import (
    FitResult,
    RidgeLogistic,
    RidgeOLS,
    WrongConstant,
)
from .scaling import StandardScaler, design_matrix, fit_scaler

_Z975 = 1.959963984540054  # scipy.stats.norm.ppf(0.975), pinned independent of scipy


@dataclass(frozen=True)
class FoldDiagnostics:
    fold: int
    n_train: int
    n_valid: int
    n_train_treated: int
    n_train_control: int
    scaler_mean: np.ndarray
    scaler_scale: np.ndarray
    logistic_iters: int
    logistic_coef_norm: float
    mu1_coef_norm: float
    mu0_coef_norm: float
    train_resid_mse1: float
    train_resid_mse0: float


@dataclass(frozen=True)
class AipwResult:
    estimand: str
    estimate: float
    se: float
    ci_low: float
    ci_high: float
    influence: np.ndarray  # per-row influence-function contribution
    pscore: np.ndarray  # out-of-fold e_hat
    mu1: np.ndarray  # out-of-fold outcome prediction under A=1
    mu0: np.ndarray  # out-of-fold outcome prediction under A=0
    fold_id: np.ndarray
    diagnostics: tuple[FoldDiagnostics, ...]
    n: int
    n_clusters: int | None
    cluster_totals: np.ndarray | None  # influence summed per independent unit
    trimming: float

    @property
    def ci(self) -> tuple[float, float]:
        return (self.ci_low, self.ci_high)


def _make_treatment_model(cfg):
    if cfg.kind == "logistic_ridge":
        return RidgeLogistic(cfg.penalty, cfg.max_iter, cfg.tol)
    if cfg.kind == "wrong_constant":
        return WrongConstant()
    raise InputError(f"unknown treatment model kind: {cfg.kind}")


def _make_outcome_model(cfg):
    if cfg.kind == "ols_ridge":
        return RidgeOLS(cfg.penalty)
    if cfg.kind == "wrong_constant":
        return WrongConstant()
    raise InputError(f"unknown outcome model kind: {cfg.kind}")


def _predict_pscore(model, fit: FitResult, x_valid: np.ndarray) -> np.ndarray:
    if hasattr(model, "predict_proba"):
        return model.predict_proba(x_valid, fit)
    return np.clip(np.asarray(model.predict(x_valid, fit)), 1e-15, 1 - 1e-15)


@dataclass
class _NuisanceArrays:
    pscore: np.ndarray
    mu1: np.ndarray
    mu0: np.ndarray


def crossfit_nuisances(
    data: Dataset, split: FoldSplit, config: AipwConfig
) -> tuple[_NuisanceArrays, tuple[FoldDiagnostics, ...]]:
    """Fit K triads of nuisance models, return out-of-fold predictions.

    A prediction slot is written exactly once (guarded by a boolean mask),
    which makes a duplicated or missed fold assignment fail loudly instead
    of silently overwriting answers.
    """
    n = data.n
    pscore = np.full(n, np.nan, dtype=np.float64)
    mu1 = np.full(n, np.nan, dtype=np.float64)
    mu0 = np.full(n, np.nan, dtype=np.float64)
    diagnostics: list[FoldDiagnostics] = []

    g_cfg = config.treatment_model
    m_cfg = config.outcome_model

    for k in range(split.n_splits):
        tr = split.train_idx[k]
        va = split.valid_idx[k]

        # Rule 2: every per-fold preprocessing statistic is train-only.
        scaler: StandardScaler = fit_scaler(data.x[tr])
        x_tr = design_matrix(data.x[tr], scaler)
        x_va = design_matrix(data.x[va], scaler)

        g_model = _make_treatment_model(g_cfg)
        g_fit = g_model.fit(x_tr, data.a[tr].astype(np.float64))
        e_va = _predict_pscore(g_model, g_fit, x_va)
        if not np.all(np.isfinite(e_va)):
            raise ComputationError(
                "non-finite out-of-fold propensity", details={"fold": k}
            )

        treated_tr = tr[data.a[tr] == 1]
        control_tr = tr[data.a[tr] == 0]
        if treated_tr.size == 0 or control_tr.size == 0:
            raise ComputationError(
                "a training fold lost a treatment arm", details={"fold": k}
            )

        x_tr1 = design_matrix(data.x[treated_tr], scaler)
        x_tr0 = design_matrix(data.x[control_tr], scaler)
        m1_model = _make_outcome_model(m_cfg)
        m0_model = _make_outcome_model(m_cfg)
        f1 = m1_model.fit(x_tr1, data.y[treated_tr])
        f0 = m0_model.fit(x_tr0, data.y[control_tr])
        m1_va = np.asarray(m1_model.predict(x_va, f1), dtype=np.float64)
        m0_va = np.asarray(m0_model.predict(x_va, f0), dtype=np.float64)

        # Training residual MSE, retained for the overfit/leakage diagnostic.
        r1 = data.y[treated_tr] - np.asarray(m1_model.predict(x_tr1, f1))
        r0 = data.y[control_tr] - np.asarray(m0_model.predict(x_tr0, f0))

        # One-writer guard: each row's nuisance predictions are produced by
        # exactly the fold that held that row out.
        if not np.all(np.isnan(pscore[va])):
            raise ComputationError(
                "fold predictions collided (fold numbering mismatch)",
                details={"fold": k},
            )
        pscore[va] = e_va
        mu1[va] = m1_va
        mu0[va] = m0_va

        diagnostics.append(
            FoldDiagnostics(
                fold=k,
                n_train=int(tr.size),
                n_valid=int(va.size),
                n_train_treated=int(treated_tr.size),
                n_train_control=int(control_tr.size),
                scaler_mean=np.asarray(scaler.mean, dtype=np.float64),
                scaler_scale=np.asarray(scaler.scale, dtype=np.float64),
                logistic_iters=int(g_fit.n_iter),
                logistic_coef_norm=float(np.linalg.norm(g_fit.coef)),
                mu1_coef_norm=float(np.linalg.norm(f1.coef)),
                mu0_coef_norm=float(np.linalg.norm(f0.coef)),
                train_resid_mse1=float(np.mean(r1**2)),
                train_resid_mse0=float(np.mean(r0**2)),
            )
        )

    if np.any(np.isnan(pscore)) or np.any(np.isnan(mu1)) or np.any(np.isnan(mu0)):
        raise ComputationError(
            "some rows never received out-of-fold predictions "
            "(fold assignment does not cover the sample)"
        )
    return _NuisanceArrays(pscore=pscore, mu1=mu1, mu0=mu0), tuple(diagnostics)


def _trim_pscore(e: np.ndarray, trim: float) -> np.ndarray:
    if trim > 0.0:
        return np.clip(e, trim, 1.0 - trim)
    return e


def _ate_influence(
    y: np.ndarray, a: np.ndarray, nuis: _NuisanceArrays
) -> np.ndarray:
    e, m1, m0 = nuis.pscore, nuis.mu1, nuis.mu0
    psi = m1 - m0
    psi = psi + (a / e) * (y - m1)
    psi = psi - ((1.0 - a) / (1.0 - e)) * (y - m0)
    return psi


def _att_influence(
    y: np.ndarray, a: np.ndarray, nuis: _NuisanceArrays
) -> np.ndarray:
    """Doubly robust ATT score (depends on e and mu0 only):

        phi_i = A_i (Y_i - mu0_i) - (1-A_i) * e_i/(1-e_i) * (Y_i - mu0_i)

    Normalized by the treated fraction.  See module docstring / docs.
    """
    e, m0 = nuis.pscore, nuis.mu0
    phi = a * (y - m0) - (1.0 - a) * (e / (1.0 - e)) * (y - m0)
    return phi


def _cluster_variance(
    psi: np.ndarray, cluster: np.ndarray, center_value: float
) -> tuple[np.ndarray, float]:
    """Variance with clusters as independent units.

    Per-row influence contributions are summed within cluster to form cluster
    totals ``S_g``.  With ``G`` clusters the variance of the global mean uses
    the classic cluster-robust denominator G/(G-1):

        Var(mean) = [1 / n^2] * G/(G-1) * sum_g (S_g - |g|*tau)^2

    Returns (cluster totals aligned to unique cluster ids, se).
    """
    ids, inverse = np.unique(cluster, return_inverse=True)
    g_count = ids.shape[0]
    if g_count < 2:
        raise ComputationError("need at least 2 clusters for variance estimation")
    sizes = np.bincount(inverse, minlength=g_count).astype(np.float64)
    totals = np.bincount(inverse, weights=psi, minlength=g_count)
    centered_total = totals - sizes * center_value
    n = psi.shape[0]
    var = (g_count / (g_count - 1.0)) * float(np.sum(centered_total**2)) / (n * n)
    if not np.isfinite(var) or var <= 0:
        raise ComputationError(
            "cluster variance non-finite or non-positive", details={"var": var}
        )
    return totals, float(np.sqrt(var))


def estimate_aipw(
    data: Dataset, config: AipwConfig, split: FoldSplit | None = None
) -> AipwResult:
    """Run cross-fitting, the AIPW point estimate and influence-function SE."""
    if split is None:
        split = make_folds(
            data.a,
            config.folds.n_splits,
            config.folds.seed,
            config.folds.stratified,
            cluster=data.cluster,
        )
    check_oo_alignment(split)
    if split.fold_id.shape[0] != data.n:
        raise InputError("fold assignment length does not match dataset")

    nuis, diagnostics = crossfit_nuisances(data, split, config)
    nuis.pscore = _trim_pscore(nuis.pscore, config.propensity_trim)
    check_positivity(nuis.pscore, data.a, config.estimand)

    n = data.n
    if config.estimand == "ATE":
        psi = _ate_influence(data.y, data.a.astype(np.float64), nuis)
        if not np.all(np.isfinite(psi)):
            raise ComputationError("non-finite AIPW influence contributions")
        tau = float(np.mean(psi))
        if data.cluster is not None:
            totals, se = _cluster_variance(psi, data.cluster, tau)
            n_clusters: int | None = totals.shape[0]
        else:
            se = float(np.std(psi, ddof=1) / np.sqrt(n))
            totals, n_clusters = None, None
    elif config.estimand == "ATT":
        phi = _att_influence(data.y, data.a.astype(np.float64), nuis)
        if not np.all(np.isfinite(phi)):
            raise ComputationError("non-finite ATT influence contributions")
        a_float = data.a.astype(np.float64)
        n1 = float(np.sum(data.a == 1))
        p1 = n1 / n
        # ATT is a ratio estimator: tau = sum phi / sum A = mean(phi)/P(A=1).
        # Stored score w = phi/P(A=1) has sample mean exactly tau; the
        # (efficient, mean-zero) influence function used for variance is
        # (phi - tau*A)/P(A=1).  The two differ only by centering on A.
        psi = phi / p1
        tau = float(np.mean(psi))
        ifac = (phi - tau * a_float) / p1
        np.testing.assert_allclose(np.mean(ifac), 0.0, atol=1e-10)
        if data.cluster is not None:
            _, se = _cluster_variance(ifac, data.cluster, 0.0)
            totals = np.bincount(
                np.unique(data.cluster, return_inverse=True)[1],
                weights=psi,
            )
            n_clusters: int | None = totals.shape[0]
        else:
            se = float(np.std(ifac, ddof=1) / np.sqrt(n))
            totals, n_clusters = None, None
    else:  # pragma: no cover - blocked by config validation
        raise InputError(f"unsupported estimand {config.estimand}")

    margin = _Z975 * se
    return AipwResult(
        estimand=config.estimand,
        estimate=tau,
        se=se,
        ci_low=tau - margin,
        ci_high=tau + margin,
        influence=psi,
        pscore=nuis.pscore,
        mu1=nuis.mu1,
        mu0=nuis.mu0,
        fold_id=split.fold_id,
        diagnostics=diagnostics,
        n=n,
        n_clusters=n_clusters,
        cluster_totals=(totals if data.cluster is not None else None),
        trimming=config.propensity_trim,
    )
