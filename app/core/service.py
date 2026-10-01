"""Service layer: orchestrates prepare -> estimate -> diagnose -> assemble.

A run carries an identity (run_id, UTC timestamp, data fingerprint) so every
log line and stored result can be traced back to the exact input.
"""
from __future__ import annotations

import datetime as dt
import dataclasses
import hashlib
import json
import platform
import uuid

import numpy as np
import scipy

from .. import __version__
from .contracts import (
    CovariateDiagnostic,
    ErrorCode,
    EstimationError,
    GroupSummary,
    LeakagePolicy,
    RunResult,
    SEType,
    Settings,
    ThetaSource,
)
from .data import prepare_data
from .diagnostics import (
    covariate_diagnostics,
    cross_checks,
    enforce_leakage_policy,
)
from .estimators import cuped, estimate_theta, group_difference, lin_ancova


def data_fingerprint(payload: dict, settings: Settings | None = None) -> str:
    """Stable SHA-256 over everything that can change the numerical answer.

    That means the analytical data and column roles AND the effective analysis
    choices (resolved from ``payload`` overrides over ``settings``): theta
    source, supplied theta, SE family, adjustment structure and the
    data-handling policies. Two runs differing in any of them get distinct
    fingerprints.
    """
    cfg = settings.to_dict() if settings is not None else {}
    analysis = {**cfg}
    for key in (
        "theta_source", "given_theta", "se_type", "regression_interactions",
        "missing_policy", "zero_variance_policy", "leakage_policy",
        "smd_threshold", "alpha", "ci_level",
    ):
        if key in payload and payload[key] is not None:
            analysis[key] = payload[key]
    canonical = json.dumps(
        {
            "outcome_column": payload["outcome_column"],
            "treatment_column": payload["treatment_column"],
            "covariates": payload.get("covariates", []),
            "pre_treatment_covariates": payload.get("pre_treatment_covariates", []),
            "data": payload["data"],
            "analysis": analysis,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def run_analysis(payload: dict, settings: Settings,
                 run_id: str | None = None) -> RunResult:
    """Execute the full statistical pipeline for one experiment run."""
    run_id = run_id or uuid.uuid4().hex[:12]
    fingerprint = data_fingerprint(payload, settings)
    created_at = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

    outcome_col = payload["outcome_column"]
    treatment_col = payload["treatment_column"]
    requested = list(payload.get("covariates", []))
    if not requested:
        raise EstimationError(
            ErrorCode.INVALID_PAYLOAD,
            "at least one covariate column is required for adjusted estimators",
        )
    # The global se_type governs the two grouping-design estimators
    # (unadjusted difference and CUPED). The regression (Lin) headline SE is
    # always HC1 and reported with its own se_type; mixing families silently
    # is forbidden by the contract.
    if settings.se_type not in (SEType.WELCH, SEType.POOLED):
        raise EstimationError(
            ErrorCode.CONFIG_ERROR,
            f"se_type={settings.se_type.value!r} is a regression-family SE; "
            "the unadjusted/CUPED estimators require 'welch' or 'pooled' "
            "(the Lin result already reports HC1 separately)",
        )
    declared_pre = {
        name: bool(is_pre)
        for name, is_pre in payload.get("pre_treatment_covariates", {}).items()
    }
    # Any covariate without an explicit declaration is assumed pre-treatment
    # but recorded so the default is auditable.
    for name in requested:
        declared_pre.setdefault(name, True)

    # 1. prepare ------------------------------------------------------------ #
    prepared = prepare_data(
        columns=payload["data"],
        outcome_column=outcome_col,
        treatment_column=treatment_col,
        requested_covariates=requested,
        missing_policy=settings.missing_policy,
        zero_variance_policy=settings.zero_variance_policy,
    )
    warnings = list(prepared.warnings)

    # 2. diagnostics + leakage policy BEFORE estimation -------------------- #
    diagnostics, diag_warnings = covariate_diagnostics(
        prepared,
        smd_threshold=settings.smd_threshold,
        alpha=settings.alpha,
        declared_pre_treatment=declared_pre,
    )
    warnings.extend(diag_warnings)

    if settings.leakage_policy is LeakagePolicy.FAIL:
        enforce_leakage_policy(settings.leakage_policy, diagnostics)

    # Provenance-declared post-treatment covariates must never enter the
    # estimator: under 'flag' they are excluded (with a warning), under
    # 'ignore' the caller knowingly keeps them. A purely STATISTICAL imbalance
    # hint (leakage_flag but not provenance) is NOT treated as proof — the
    # covariate is retained but prominently flagged for human review, since
    # chance imbalance in a genuine baseline variable is expected sometimes.
    # 'fail' rejects either kind above.
    excluded_leakage: list[str] = []
    if settings.leakage_policy is LeakagePolicy.FLAG:
        excluded_leakage = [d.name for d in diagnostics
                            if d.leakage_flag and d.included
                            and d.provenance_post_treatment]
        hinted = [d.name for d in diagnostics
                  if d.leakage_flag and d.included
                  and not d.provenance_post_treatment]
        if hinted:
            warnings.append(
                "statistical imbalance hint(s) on "
                f"{hinted}: retained in the adjustment because provenance "
                "declares them pre-treatment; review their measurement timing "
                "(set leakage_policy='fail' to reject on any hint)"
            )
        if excluded_leakage:
            warnings.append(
                "excluded declared post-treatment covariate(s) from the "
                f"adjustment: {excluded_leakage} (leakage_policy='flag'; "
                "set 'ignore' to force inclusion or 'fail' to reject runs)"
            )
            diagnostics = [
                _exclude(d, "post_treatment_provenance")
                if d.name in set(excluded_leakage) else d
                for d in diagnostics
            ]
            clean_requested = [c for c in requested
                               if c not in set(excluded_leakage)]
            prepared = prepare_data(
                columns=payload["data"],
                outcome_column=outcome_col,
                treatment_column=treatment_col,
                requested_covariates=clean_requested,
                missing_policy=settings.missing_policy,
                zero_variance_policy=settings.zero_variance_policy,
            )

    # 3. estimates ---------------------------------------------------------- #
    if prepared.x.shape[1] == 0:
        raise EstimationError(
            ErrorCode.ZERO_VARIANCE_COVARIATE,
            "no usable covariates remain (all dropped as constant or "
            "suspected post-treatment); adjusted estimators require at least "
            "one valid pre-treatment covariate",
            {
                "requested": requested,
                "dropped_constant": list(prepared.dropped_covariates),
                "excluded_leakage": excluded_leakage,
            },
        )

    # Unadjusted always uses the grouping-design SE family (Welch/pooled).
    unadj = group_difference(prepared, settings.se_type, settings.ci_level)

    raw_var_control = float(prepared.y[prepared.t == 0].var(ddof=1))

    # CUPED: theta from the declared source (default: control arm, pre-period).
    given = payload.get("given_theta")
    given_arr = np.asarray(given, dtype=np.float64) if given is not None else None
    if settings.theta_source is ThetaSource.GIVEN:
        k = prepared.x.shape[1]
        if given_arr is None or given_arr.shape != (k,):
            raise EstimationError(
                ErrorCode.CONFIG_ERROR,
                f"theta_source='given' requires 'given_theta' as a list of "
                f"{k} number(s), aligned with covariates {list(prepared.covariate_names)}",
                {"expected_length": k, "received": list(given_arr.shape) if given_arr is not None else None},
            )
    theta = estimate_theta(prepared, settings.theta_source, given=given_arr)
    cuped_est = cuped(prepared, theta, settings.se_type, settings.ci_level)

    # Identity check needs pooled within-arm theta too; no re-fitting in
    # diagnostics — use a second, explicitly labelled theta fit.
    theta_pooled = estimate_theta(prepared, ThetaSource.POOLED_PRE)
    cuped_pooled = cuped(prepared, theta_pooled, SEType.POOLED, settings.ci_level)

    # Regression reference: main-effects ANCOVA (classical SE matching the
    # algebraic identity) and the headline Lin estimator (HC1 robust SE).
    ancova_main = lin_ancova(prepared, interactions=False,
                             se_type=SEType.CLASSICAL, ci_level=settings.ci_level)
    lin = lin_ancova(prepared, interactions=settings.regression_interactions,
                     se_type=SEType.HC1, ci_level=settings.ci_level)

    checks = cross_checks(
        unadj=unadj,
        cuped_est=cuped_est,
        lin_main_estimate=ancova_main,
        cuped_pooled_estimate=cuped_pooled,
        theta=theta,
        raw_var_control=raw_var_control,
    )

    # Failed hard identities are surfaced as warnings, never hidden; callers
    # can fail the run themselves by inspecting cross_checks.
    for check in checks:
        if not check.passed:
            warnings.append(f"cross-check failed: {check.name}: {check.detail}")

    groups = _group_summaries(prepared)

    return RunResult(
        run_id=run_id,
        data_fingerprint=fingerprint,
        created_at=created_at,
        experiment_name=str(payload.get("name", "unnamed_experiment")),
        outcome_column=outcome_col,
        treatment_column=treatment_col,
        requested_covariates=tuple(requested),
        used_covariates=prepared.covariate_names,
        dropped_covariates=tuple(prepared.dropped_covariates)
        + tuple(excluded_leakage),
        n_rows=prepared.n_rows,
        n_complete_rows=prepared.n_complete_rows,
        n_dropped_rows=prepared.n_dropped_rows,
        missing_policy=settings.missing_policy.value,
        theta_source=settings.theta_source.value,
        se_type=settings.se_type.value,
        unadjusted=unadj,
        cuped=cuped_est,
        lin=lin,
        groups=tuple(groups),
        diagnostics=tuple(diagnostics),
        cross_checks=tuple(checks),
        warnings=tuple(warnings),
        versions={
            "python": platform.python_version(),
            "numpy": np.__version__,
            "scipy": scipy.__version__,
            "backend": __version__,
        },
        config_snapshot=settings.to_dict(),
    )


def _exclude(diag: CovariateDiagnostic, reason: str) -> CovariateDiagnostic:
    return dataclasses.replace(diag, included=False, reason_excluded=reason)


def _group_summaries(prepared) -> list[GroupSummary]:
    out: list[GroupSummary] = []
    for arm in (0, 1):
        vals = prepared.y[prepared.t == arm]
        n = len(vals)
        mean = float(vals.mean())
        var = float(vals.var(ddof=1)) if n > 1 else float("nan")
        out.append(GroupSummary(
            arm=arm, n=n, mean=mean, variance=var,
            se_mean=float(np.sqrt(var / n)) if n > 1 else float("nan"),
        ))
    return out
