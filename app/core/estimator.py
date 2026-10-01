"""Estimation kernel: difference-in-means, CUPED and linear covariate adjustment.

Statistical contract implemented here
--------------------------------------
1. Unadjusted effect: two-sample difference in means with the Welch standard
   error ``s1^2/n1 + s0^2/n0``.
2. CUPED coefficient ``theta`` has an explicit source:
   * ``control``    - fit Cov(Y, X) / Var(X) on the control arm only, so the
                      post-randomization outcome of treated units never feeds
                      the coefficient (classical CUPED).
   * ``pooled_fwl`` - coefficient on X in a pooled OLS of Y on [1, T, X],
                      i.e. the Frisch-Waugh-Lovell residual coefficient.
3. Adjusted outcome ``Y_adj = Y - (X - Xbar) theta``; the adjusted effect uses
   the *same* two-group Welch formula, so unadjusted/adjusted SEs are
   comparable and track the randomized grouping design.
4. A fully independent OLS reference (see ``app.core.reference``) fits
   ``Y ~ 1 + T + X`` with model and HC1 standard errors as a cross-check.
"""

from __future__ import annotations

import numpy as np
import scipy
from scipy import stats

from app import __version__
from app.core import reference as reference_mod
from app.core import validation
from app.core.config import EstimationConfig
from app.core.contracts import (
    CovariateDeclaration,
    CovariateDiagnostic,
    EffectEstimate,
    EstimationResult,
    FailedRun,
    MissingStrategy,
    ThetaInfo,
    ThetaSource,
    ZeroVarianceStrategy,
)
from app.core.errors import ErrorCode, EstimationError, EstimationFailure
from app.core.logging_setup import get_logger, kv


def _versions() -> dict[str, str]:
    return {
        "app": __version__,
        "numpy": np.__version__,
        "scipy": scipy.__version__,
    }


def welch_effect(y: np.ndarray, t: np.ndarray, kind: str) -> EffectEstimate:
    """Two-independent-group difference in means with Welch inference.

    Both the p-value and the confidence interval use the t distribution with
    Welch-Satterthwaite degrees of freedom, matching the randomized two-group
    design.
    """
    y1 = y[t == 1].astype(float)
    y0 = y[t == 0].astype(float)
    n1, n0 = len(y1), len(y0)

    m1, m0 = float(y1.mean()), float(y0.mean())
    v1 = float(y1.var(ddof=1)) if n1 > 1 else 0.0
    v0 = float(y0.var(ddof=1)) if n0 > 1 else 0.0
    se = float(np.sqrt(v1 / n1 + v0 / n0))
    est = m1 - m0

    if se <= 0.0:
        raise EstimationFailure(
            ErrorCode.RANK_DEFICIENT_DESIGN,
            f"{kind}: zero standard error (degenerate outcome variance)",
            {"var_treatment": v1, "var_control": v0},
        )

    t_stat = est / se
    df = (v1 / n1 + v0 / n0) ** 2 / (
        (v1 / n1) ** 2 / (n1 - 1) + (v0 / n0) ** 2 / (n0 - 1)
    )
    p = float(2.0 * stats.t.sf(abs(t_stat), df))
    crit = float(stats.t.ppf(0.975, df))
    return EffectEstimate(
        kind=kind,
        estimate=est,
        se=se,
        t_stat=float(t_stat),
        p_value=p,
        ci_low=est - crit * se,
        ci_high=est + crit * se,
        df=float(df),
        n_treatment=n1,
        n_control=n0,
        mean_treatment=m1,
        mean_control=m0,
        var_treatment=v1,
        var_control=v0,
    )


def _fit_theta(
    y: np.ndarray,
    t: np.ndarray,
    Xc: np.ndarray,
    source: ThetaSource,
    logger,
    run_id: str,
) -> tuple[np.ndarray, ThetaInfo]:
    k = Xc.shape[1]
    if source is ThetaSource.CONTROL:
        X0, y0 = Xc[t == 0], y[t == 0]
        if len(y0) <= k + 1:
            raise EstimationFailure(
                ErrorCode.INSUFFICIENT_SOURCE_SAMPLE,
                "control arm too small to fit theta",
                {"n_control": int(len(y0)), "min_required": k + 2},
            )
        Z = np.column_stack([np.ones(len(y0)), X0])
        beta, _, rank, _ = np.linalg.lstsq(Z, y0, rcond=None)
        if rank < k + 1:
            raise EstimationFailure(
                ErrorCode.RANK_DEFICIENT_DESIGN,
                "covariate design rank-deficient within control arm",
                {"rank": int(rank), "required": k + 1},
            )
        theta = beta[1:]
        info = ThetaInfo(
            source=source.value,
            coefficients={},  # filled by caller with names
            n_units_used=int(len(y0)),
            n_arms_used="control",
        )
        logger.info("theta fit | source=control n=%d rank=%d", len(y0), rank)
        return theta, info

    # Pooled Frisch-Waugh-Lovell: theta from OLS Y on [1, T, X].
    n = len(y)
    if n <= k + 2:
        raise EstimationFailure(
            ErrorCode.INSUFFICIENT_SOURCE_SAMPLE,
            "pooled sample too small to fit theta",
            {"n": n, "min_required": k + 3},
        )
    Z = np.column_stack([np.ones(n), t.astype(float), Xc])
    beta, _, rank, _ = np.linalg.lstsq(Z, y, rcond=None)
    if rank < k + 2:
        raise EstimationFailure(
            ErrorCode.RANK_DEFICIENT_DESIGN,
            "pooled covariate design rank-deficient",
            {"rank": int(rank), "required": k + 2},
        )
    theta = beta[2:]
    info = ThetaInfo(
        source=source.value,
        coefficients={},
        n_units_used=int(n),
        n_arms_used="pooled",
    )
    logger.info("theta fit | source=pooled_fwl n=%d rank=%d", n, rank)
    return theta, info


def _covariate_diagnostics(
    X_filled: np.ndarray,
    kept_names: list[str],
    all_names: list[str],
    declarations: list[CovariateDeclaration],
    zero_idx: list[int],
    n_missing: list[int],
    n_imputed: list[int],
    y: np.ndarray,
    t: np.ndarray,
) -> list[CovariateDiagnostic]:
    diag: list[CovariateDiagnostic] = []
    kept_lookup = {name: X_filled[:, j] for j, name in enumerate(kept_names)}
    decl_by_name = {d.name: d for d in declarations}

    for j, name in enumerate(all_names):
        col = kept_lookup.get(name)
        decl = decl_by_name[name]
        warnings: list[str] = []
        if j in zero_idx:
            diag.append(
                CovariateDiagnostic(
                    name=name,
                    pre_treatment=decl.pre_treatment,
                    n_missing=n_missing[j],
                    n_imputed=n_imputed[j],
                    variance=0.0,
                    zero_variance=True,
                    dropped=True,
                    mean_control=float("nan"),
                    mean_treatment=float("nan"),
                    standardized_mean_diff=float("nan"),
                    balance_z=float("nan"),
                    balance_p_value=float("nan"),
                    leakage_suspected=False,
                    corr_outcome_control=float("nan"),
                    warnings=("ZERO_VARIANCE_COLUMN_DROPPED",),
                )
            )
            continue

        x1, x0 = col[t == 1], col[t == 0]
        m1, m0 = float(x1.mean()), float(x0.mean())
        var = float(col.var(ddof=1))
        v1, v0 = float(x1.var(ddof=1)), float(x0.var(ddof=1))
        pooled_sd = float(np.sqrt((v1 + v0) / 2.0))
        smd = (m1 - m0) / pooled_sd if pooled_sd > 0 else float("nan")
        bal = stats.ttest_ind(x1, x0, equal_var=False)
        z_bal = float(bal.statistic)
        p_bal = float(bal.pvalue)

        if abs(z_bal) > 3.0:
            warnings.append(f"LARGE_ARM_IMBALANCE(|z|={abs(z_bal):.2f})")

        x0c = x0 - x0.mean()
        y0c = y[t == 0] - y[t == 0].mean()
        denom = float(np.sqrt((x0c**2).sum() * (y0c**2).sum()))
        corr = float((x0c * y0c).sum() / denom) if denom > 0 else float("nan")

        # Heuristic only: the hard leakage gate is the pre_treatment flag.
        leakage = bool(np.isfinite(corr) and abs(corr) > 0.95 and abs(z_bal) > 3.0)
        if leakage:
            warnings.append("LEAKAGE_SUSPECTED(high outcome+assignment association)")
        if n_imputed[j] > 0:
            warnings.append(f"MISSING_IMPUTED(n={n_imputed[j]})")

        diag.append(
            CovariateDiagnostic(
                name=name,
                pre_treatment=decl.pre_treatment,
                n_missing=n_missing[j],
                n_imputed=n_imputed[j],
                variance=var,
                zero_variance=False,
                dropped=False,
                mean_control=m0,
                mean_treatment=m1,
                standardized_mean_diff=float(smd),
                balance_z=z_bal,
                balance_p_value=p_bal,
                leakage_suspected=leakage,
                corr_outcome_control=corr,
                warnings=tuple(warnings),
            )
        )
    return diag


def estimate(
    experiment_id: str,
    unit_id: np.ndarray,
    treatment: np.ndarray,
    outcome: np.ndarray,
    covariate_columns: dict[str, np.ndarray],
    declarations: list[CovariateDeclaration],
    config: EstimationConfig,
    run_id: str,
    *,
    missing_strategy: MissingStrategy = MissingStrategy.MEAN_IMPUTE,
    zero_variance_strategy: ZeroVarianceStrategy = ZeroVarianceStrategy.DROP,
    theta_source: ThetaSource = ThetaSource.CONTROL,
) -> EstimationResult:
    logger = get_logger("estimator", run_id)
    requested = [d.name for d in declarations]
    logger.info("estimate start | %s", kv({"experiment": experiment_id, "n": len(outcome), "covariates": requested}))

    try:
        unit_id = np.asarray(unit_id)
        treatment = np.asarray(treatment, dtype=float)
        outcome = np.asarray(outcome, dtype=float)

        validation.validate_arrays(
            unit_id, treatment, outcome,
            np.column_stack([np.asarray(covariate_columns[d.name], dtype=float) for d in declarations])
            if declarations else np.empty((len(outcome), 0)),
            declarations,
        )
        validation.reject_leaked_covariates(declarations)

        all_warnings: list[str] = []
        n = len(outcome)
        n1, n0 = int((treatment == 1).sum()), int((treatment == 0).sum())
        logger.info("groups | n1=%d n0=%d", n1, n0)

        # --- Unadjusted baseline (always reported) ---
        unadj = welch_effect(outcome, treatment, "unadjusted")
        logger.info(
            "unadjusted | est=%.6f se=%.6f t=%.4f p=%.4g",
            unadj.estimate, unadj.se, unadj.t_stat, unadj.p_value,
        )

        if not declarations:
            logger.info("no covariates requested; returning unadjusted-only result")
            return EstimationResult(
                run_id=run_id,
                experiment_id=experiment_id,
                status="completed",
                n=n, n_treatment=n1, n_control=n0,
                covariates_requested=tuple(requested),
                covariates_used=(),
                theta=None,
                unadjusted=unadj,
                adjusted=None,
                se_reduction=None,
                variance_reduction=None,
                hc1_regression_se=None,
                reference_regression=None,
                diagnostics=(),
                warnings=tuple(all_warnings),
                versions=_versions(),
            )

        names = [d.name for d in declarations]
        X = np.column_stack([np.asarray(covariate_columns[name], dtype=float) for name in names])

        # --- Explicit missing-value policy ---
        X_filled, n_missing, n_imputed = validation.handle_missing(X, names, missing_strategy)
        # --- Explicit zero-variance policy ---
        X_reduced, kept_names, zero_idx = validation.handle_zero_variance(
            X_filled, names, zero_variance_strategy
        )
        if zero_idx:
            all_warnings.append(
                f"ZERO_VARIANCE_DROPPED:{','.join(names[j] for j in zero_idx)}"
            )
        if not kept_names:
            raise EstimationFailure(
                ErrorCode.ZERO_VARIANCE_COVARIATE,
                "all covariates dropped due to zero variance",
                {"columns": names},
            )

        # Rank check on [1, X] using singular values.
        Xc = X_reduced - X_reduced.mean(axis=0)
        rank = int(np.linalg.matrix_rank(np.column_stack([np.ones(n), Xc])))
        if rank < Xc.shape[1] + 1:
            raise EstimationFailure(
                ErrorCode.RANK_DEFICIENT_DESIGN,
                "covariate design matrix is rank deficient (collinear columns)",
                {"rank": rank, "required": Xc.shape[1] + 1},
            )

        # --- Theta with explicit provenance ---
        theta, theta_info = _fit_theta(outcome, treatment, Xc, theta_source, logger, run_id)
        theta_info = ThetaInfo(
            source=theta_info.source,
            coefficients={name: float(c) for name, c in zip(kept_names, theta)},
            n_units_used=theta_info.n_units_used,
            n_arms_used=theta_info.n_arms_used,
        )
        logger.info("theta coefficients | %s", kv(theta_info.coefficients))

        # --- Adjusted outcome and same-grouping-design inference ---
        y_adj = outcome - Xc @ theta
        adj = welch_effect(y_adj, treatment, "cuped_adjusted")
        logger.info(
            "adjusted | est=%.6f se=%.6f t=%.4f p=%.4g",
            adj.estimate, adj.se, adj.t_stat, adj.p_value,
        )

        # --- Independent OLS reference: Y ~ 1 + T + X (model SE + HC1 SE) ---
        Z_ref = np.column_stack([np.ones(n), treatment.astype(float), Xc])
        fit = reference_mod.ols_fit(outcome, Z_ref, hc1=config.hc1_correction)
        ref_inf = reference_mod.treatment_inference(fit, treatment_index=1, z_alpha=config.z_alpha)
        reference_payload = {
            "model": "y ~ 1 + treatment + covariates",
            "beta_treatment": ref_inf["beta_treatment"],
            "se_hc1": ref_inf["se_treatment"],
            "se_model": float(fit["se_model"][1]),
            "z_stat": ref_inf["z_stat"],
            "p_value": ref_inf["p_value"],
            "ci_low": ref_inf["ci_low"],
            "ci_high": ref_inf["ci_high"],
            "r_squared": fit["r_squared"],
            "beta_covariates": {name: float(b) for name, b in zip(kept_names, fit["beta"][2:])},
            "n": fit["n"],
            "k": fit["k"],
        }
        logger.info(
            "reference OLS | beta_T=%.6f se_hc1=%.6f se_model=%.6f R2=%.4f",
            ref_inf["beta_treatment"], ref_inf["se_treatment"], fit["se_model"][1], fit["r_squared"],
        )

        # The adjusted DiM equals the OLS treatment coefficient by the normal
        # equations exactly when theta is the pooled FWL coefficient; for the
        # control-arm theta they may differ, so the equality gate only applies
        # to pooled_fwl.
        if theta_source is ThetaSource.POOLED_FWL and abs(
            adj.estimate - ref_inf["beta_treatment"]
        ) > 1e-6 * max(1.0, abs(ref_inf["beta_treatment"])):
            all_warnings.append("ADJUSTED_DIM_VS_OLS_COEFFICIENT_MISMATCH")
            logger.warning(
                "adjusted DiM (%.8f) differs from OLS beta_T (%.8f)",
                adj.estimate, ref_inf["beta_treatment"],
            )
        elif theta_source is ThetaSource.CONTROL:
            logger.info(
                "cross-check | adjusted DiM=%.8f vs OLS beta_T=%.8f (independent fits, expected to differ)",
                adj.estimate, ref_inf["beta_treatment"],
            )

        diagnostics = _covariate_diagnostics(
            X_filled, kept_names, names, declarations, zero_idx,
            n_missing, n_imputed, outcome, treatment,
        )

        se_reduction = 1.0 - adj.se / unadj.se
        var_reduction = 1.0 - (adj.se / unadj.se) ** 2
        logger.info(
            "variance change | se_reduction=%.4f variance_reduction=%.4f",
            se_reduction, var_reduction,
        )

        return EstimationResult(
            run_id=run_id,
            experiment_id=experiment_id,
            status="completed",
            n=n, n_treatment=n1, n_control=n0,
            covariates_requested=tuple(requested),
            covariates_used=tuple(kept_names),
            theta=theta_info,
            unadjusted=unadj,
            adjusted=adj,
            se_reduction=float(se_reduction),
            variance_reduction=float(var_reduction),
            hc1_regression_se=float(ref_inf["se_treatment"]),
            reference_regression=reference_payload,
            diagnostics=tuple(diagnostics),
            warnings=tuple(all_warnings),
            versions=_versions(),
        )

    except EstimationError as exc:
        logger.error("estimation failed | code=%s message=%s details=%s", exc.code.value, exc.message, exc.details)
        raise


def failed_run(run_id: str, experiment_id: str, exc: EstimationError) -> FailedRun:
    """Package a caught EstimationError into a stored failed-run record."""
    return FailedRun(
        run_id=run_id,
        experiment_id=experiment_id,
        status="failed",
        error_code=exc.code.value,
        message=exc.message,
        details=exc.details,
        versions=_versions(),
    )
