"""Evidence and diagnostics.

Every adjusted result is accompanied by:

* per-covariate balance tables (standardised mean differences),
* an explicit post-treatment / leakage screen combining declared provenance
  with a statistical imbalance test (a *pre*-treatment variable measured
  before randomisation must be balanced in expectation),
* cross-check identities (CUPED adjusted-difference vs ANCOVA coefficient;
  achieved vs predicted variance reduction).

Diagnostics never change estimates silently: they produce warnings or, under
``leakage_policy='fail'``, a typed error.
"""
from __future__ import annotations

import numpy as np
from scipy import stats

from .contracts import (
    CovariateDiagnostic,
    CrossCheck,
    ErrorCode,
    Estimate,
    EstimationError,
    LeakagePolicy,
    ThetaSource,
)
from .data import PreparedData
from .estimators import ThetaFit


def covariate_diagnostics(
    data: PreparedData,
    smd_threshold: float,
    alpha: float,
    declared_pre_treatment: dict[str, bool],
) -> tuple[list[CovariateDiagnostic], list[str]]:
    """Balance and leakage diagnostics for every *retained* covariate."""
    y, t, x = data.y, data.t, data.x
    y_sd = float(y.std(ddof=1))
    warnings: list[str] = []
    out: list[CovariateDiagnostic] = []

    for j, name in enumerate(data.covariate_names):
        xj = x[:, j]
        x1, x0 = xj[t == 1], xj[t == 0]
        m1, m0 = float(x1.mean()), float(x0.mean())
        v1, v0 = float(x1.var(ddof=1)), float(x0.var(ddof=1))
        n1, n0 = len(x1), len(x0)
        pooled_sd = float(np.sqrt(((n1 - 1) * v1 + (n0 - 1) * v0) / (n1 + n0 - 2)))
        smd = (m1 - m0) / pooled_sd if pooled_sd > 0 else 0.0

        # Welch test of covariate balance across arms.
        if pooled_sd > 0:
            welch_se = np.sqrt(v1 / n1 + v0 / n0)
            z = (m1 - m0) / welch_se
            df = ((v1 / n1 + v0 / n0) ** 2
                  / ((v1 / n1) ** 2 / (n1 - 1) + (v0 / n0) ** 2 / (n0 - 1)))
            p_balance = float(2 * stats.t.sf(abs(z), df))
        else:
            p_balance = 1.0

        corr_y = float(np.corrcoef(xj, y)[0, 1]) if xj.std(ddof=1) > 0 and y_sd > 0 else 0.0
        corr_t = float(np.corrcoef(xj, t)[0, 1]) if xj.std(ddof=1) > 0 else 0.0

        is_declared_pre = declared_pre_treatment.get(name, True)
        leakage, reason, from_provenance = _leakage_assessment(
            name=name,
            is_declared_pre=is_declared_pre,
            smd=smd,
            p_balance=p_balance,
            smd_threshold=smd_threshold,
            alpha=alpha,
        )
        if leakage:
            warnings.append(f"covariate {name!r}: {reason}")
        if abs(smd) > smd_threshold:
            warnings.append(
                f"covariate {name!r}: |SMD|={abs(smd):.3f} exceeds threshold "
                f"{smd_threshold:.2f} (chance imbalance or post-treatment field)"
            )

        out.append(CovariateDiagnostic(
            name=name,
            included=True,
            reason_excluded=None,
            overall_mean=float(xj.mean()),
            overall_std=float(xj.std(ddof=1)),
            mean_treatment=m1,
            mean_control=m0,
            smd=float(smd),
            smd_threshold=float(smd_threshold),
            balance_ok=abs(smd) <= smd_threshold,
            corr_with_outcome=corr_y,
            corr_with_treatment=corr_t,
            missing_count=int(data.covariate_missing_counts[j]),
            zero_variance=False,
            leakage_flag=leakage,
            leakage_reason=reason,
            provenance_post_treatment=from_provenance,
        ))

    # Diagnostics rows for dropped constant covariates (evidence retained).
    # The exclusion warning itself is emitted once, in data.prepare_data;
    # here we only keep the diagnostic row with the real missing count.
    for name, miss in zip(data.dropped_covariates,
                          data.dropped_covariate_missing_counts):
        out.append(CovariateDiagnostic(
            name=name, included=False, reason_excluded="zero_variance",
            overall_mean=None, overall_std=0.0,
            mean_treatment=None, mean_control=None,
            smd=0.0, smd_threshold=float(smd_threshold), balance_ok=True,
            corr_with_outcome=0.0, corr_with_treatment=0.0,
            missing_count=int(miss), zero_variance=True,
            leakage_flag=False, leakage_reason=None,
        ))

    return out, warnings


def _leakage_assessment(
    *, name: str, is_declared_pre: bool, smd: float, p_balance: float,
    smd_threshold: float, alpha: float,
) -> tuple[bool, str | None, bool]:
    """Return (is_flagged, reason, from_authoritative_provenance).

    Hard signal: the request itself declares the field as not pre-treatment —
    this is authoritative and the third element is True.
    Statistical signal: a large AND significant arm imbalance is implausible
    for a genuine baseline variable but can still be chance, so the third
    element is False (a hint, not provenance).
    """
    if not is_declared_pre:
        return True, (
            "declared post-treatment: field is not certified as measured "
            "before treatment assignment"
        ), True
    if abs(smd) > smd_threshold and p_balance < alpha:
        return True, (
            f"suspected post-treatment information: |SMD|={abs(smd):.3f} > "
            f"{smd_threshold:.2f} and balance test p={p_balance:.4f} < alpha={alpha} "
            "(statistical hint; field kept under flag policy, review provenance)"
        ), False
    return False, None, False


def enforce_leakage_policy(
    policy: LeakagePolicy, diagnostics: list[CovariateDiagnostic]
) -> None:
    if policy is LeakagePolicy.IGNORE or policy is LeakagePolicy.FLAG:
        return
    flagged = [d.name for d in diagnostics if d.leakage_flag]
    if flagged:
        raise EstimationError(
            ErrorCode.LEAKAGE_DETECTED,
            f"leakage_policy='fail': {len(flagged)} covariate(s) failed the "
            f"pre-treatment screen: {flagged}",
            {"covariates": flagged},
        )


# --------------------------------------------------------------------------- #
# Cross-checks
# --------------------------------------------------------------------------- #
def cross_checks(
    unadj: Estimate,
    cuped_est: Estimate,
    lin_main_estimate: Estimate,
    cuped_pooled_estimate: Estimate,
    theta: ThetaFit,
    raw_var_control: float,
    identity_tol: float = 1e-8,
) -> list[CrossCheck]:
    """Algebraic and variance-reduction identities, with explicit pass/fail."""
    checks: list[CrossCheck] = []

    # 1. CUPED/pooled-theta estimate must equal main-effects ANCOVA tau.
    #    Both are (ybar1-ybar0) - (xbar1-xbar0)' theta_within.
    diff = abs(cuped_pooled_estimate.estimate - lin_main_estimate.estimate)
    scale = max(abs(lin_main_estimate.estimate), abs(cuped_pooled_estimate.estimate), 1.0)
    rel = diff / scale
    checks.append(CrossCheck(
        name="cuped_ancova_identity",
        passed=bool(rel < identity_tol),
        detail=("CUPED adjusted difference (pooled within-arm theta) equals "
                "main-effects ANCOVA treatment coefficient"),
        values={
            "cuped_pooled_estimate": float(cuped_pooled_estimate.estimate),
            "ancova_estimate": float(lin_main_estimate.estimate),
            "abs_difference": float(diff),
            "relative_difference": float(rel),
            "tolerance": float(identity_tol),
        },
    ))

    # Variance-reduction identities only make a promise for a FITTED theta.
    # With theta_source='given' the coefficient is external (e.g. estimated
    # in a prior experiment), so these checks are omitted rather than faked.
    fitted_theta = theta.source is not ThetaSource.GIVEN
    if fitted_theta:
        # 2. SE comparison is informational: a pre-treatment covariate is
        #    *expected* to reduce SE asymptotically, but in very small samples
        #    an unrelated covariate can inflate it by chance. This is therefore
        #    not a hard pass/fail identity — it records the achieved ratio.
        se_ratio = cuped_est.se / unadj.se
        checks.append(CrossCheck(
            name="standard_error_reduction",
            passed=True,
            detail=("informational: achieved CUPED/unadjusted SE ratio "
                    "(<1 means variance removed; small-sample overshoot is "
                    "possible by chance and is not an algebraic failure)"),
            values={
                "unadjusted_se": float(unadj.se),
                "cuped_se": float(cuped_est.se),
                "se_ratio": float(se_ratio),
                "variance_reduction_pct": float((1 - se_ratio**2) * 100),
            },
        ))

        # 3. The exact R^2 identity holds only when theta is fitted ON THE
        #    CONTROL ARM with an intercept: then in-sample control residual
        #    fraction is 1 - R^2. For pooled theta the reference quantity
        #    would be the pooled within-arm residual, not the control variance,
        #    so the check is scoped to control_pre rather than faked.
        if theta.source is ThetaSource.CONTROL_PRE and theta.r_squared is not None:
            achieved = 1.0 - (cuped_est.extra["adjusted_var_control"]
                              / max(raw_var_control, 1e-300))
            checks.append(CrossCheck(
                name="variance_reduction_vs_rsquared",
                passed=bool(abs(achieved - theta.r_squared) < 5e-2),
                detail=("control-arm in-sample variance reduction matches the "
                        "control-theta regression R^2 (OLS-with-intercept identity)"),
                values={
                    "theta_r_squared": float(theta.r_squared),
                    "achieved_reduction": float(achieved),
                    "abs_difference": float(abs(achieved - theta.r_squared)),
                },
            ))

    # 4. Estimator agreement: all point estimates target the same ATE.
    spread = max(abs(cuped_est.estimate - unadj.estimate),
                 abs(lin_main_estimate.estimate - unadj.estimate))
    checks.append(CrossCheck(
        name="estimator_agreement",
        passed=bool(spread < 3.0 * unadj.se),
        detail=("adjusted and unadjusted point estimates differ by less than "
                "3 unadjusted SEs; large divergence signals imbalance/leakage"),
        values={
            "unadjusted": float(unadj.estimate),
            "cuped_control_theta": float(cuped_est.estimate),
            "ancova_main_effects": float(lin_main_estimate.estimate),
            "max_abs_spread": float(spread),
        },
    ))
    return checks
