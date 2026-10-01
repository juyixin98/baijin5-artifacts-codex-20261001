"""Input validation at the system boundary.

Nothing downstream of this module trusts raw arrays: treatment must be binary,
units unique, outcomes complete, covariates explicitly declared, leakage
fields rejected, and missing / zero-variance columns handled by an explicit,
caller-chosen strategy.
"""

from __future__ import annotations

import numpy as np

from app.core.contracts import (
    CovariateDeclaration,
    MissingStrategy,
    ZeroVarianceStrategy,
)
from app.core.errors import ErrorCode, ValidationError


def validate_arrays(
    unit_id: np.ndarray,
    treatment: np.ndarray,
    outcome: np.ndarray,
    X: np.ndarray,
    declarations: list[CovariateDeclaration],
) -> None:
    n = len(outcome)
    if n == 0:
        raise ValidationError(ErrorCode.EMPTY_DATA, "no observations supplied")

    if not (len(unit_id) == len(treatment) == X.shape[0] == n):
        raise ValidationError(
            ErrorCode.INVALID_REQUEST,
            "row count mismatch across unit_id/treatment/outcome/covariates",
            {"lengths": [len(unit_id), len(treatment), n, X.shape[0]]},
        )

    ids = unit_id.astype(str)
    unique_ids, counts = np.unique(ids, return_counts=True)
    if len(unique_ids) != n:
        duplicated = unique_ids[counts > 1].tolist()
        raise ValidationError(
            ErrorCode.DUPLICATE_UNIT,
            "unit_id values must be unique",
            {"n_rows": n, "n_unique": len(unique_ids), "duplicated": duplicated[:10]},
        )

    t_unique = np.unique(treatment)
    if not np.all(np.isin(t_unique, [0, 1])):
        raise ValidationError(
            ErrorCode.TREATMENT_NOT_BINARY,
            "treatment indicator must contain only {0,1}",
            {"observed": [float(v) for v in t_unique]},
        )

    for arm, label in ((1, "treatment"), (0, "control")):
        if int(np.sum(treatment == arm)) == 0:
            raise ValidationError(
                ErrorCode.GROUP_EMPTY,
                f"{label} arm contains no observations",
                {"arm": label},
            )

    if np.isnan(outcome.astype(float)).any():
        n_missing = int(np.isnan(outcome.astype(float)).sum())
        raise ValidationError(
            ErrorCode.OUTCOME_MISSING,
            "outcome must be fully observed (missing outcomes are never imputed)",
            {"n_missing": n_missing},
        )

    if X.shape[1] != len(declarations):
        raise ValidationError(
            ErrorCode.INVALID_REQUEST,
            "covariate matrix column count does not match declarations",
            {"n_columns": X.shape[1], "n_declarations": len(declarations)},
        )


def reject_leaked_covariates(declarations: list[CovariateDeclaration]) -> None:
    leaked = [d.name for d in declarations if not d.pre_treatment]
    if leaked:
        raise ValidationError(
            ErrorCode.LEAKED_COVARIATE,
            "post-treatment / leakage covariates are forbidden in adjustment",
            {"covariates": leaked},
        )


def handle_missing(
    X: np.ndarray,
    names: list[str],
    strategy: MissingStrategy,
) -> tuple[np.ndarray, list[int], list[int]]:
    """Return a *new* matrix with the missing-value strategy applied.

    Returns (X_filled, n_missing_per_column, n_imputed_per_column).
    """
    X = np.asarray(X, dtype=float)
    nan_mask = np.isnan(X)
    n_missing = [int(nan_mask[:, j].sum()) for j in range(X.shape[1])]

    if strategy is MissingStrategy.ERROR and nan_mask.any():
        bad = [names[j] for j in range(X.shape[1]) if n_missing[j] > 0]
        raise ValidationError(
            ErrorCode.COVARIATE_VALUE_MISSING,
            "covariate contains missing values and missing strategy is 'error'",
            {"columns": bad, "n_missing": {names[j]: n_missing[j] for j in range(X.shape[1]) if n_missing[j]}},
        )

    X_filled = X.copy()
    n_imputed = [0] * X.shape[1]
    for j in range(X.shape[1]):
        if n_missing[j] > 0:
            col = X[:, j]
            pooled_mean = float(np.nanmean(col))
            X_filled[nan_mask[:, j], j] = pooled_mean
            n_imputed[j] = n_missing[j]
    return X_filled, n_missing, n_imputed


def handle_zero_variance(
    X: np.ndarray,
    names: list[str],
    strategy: ZeroVarianceStrategy,
) -> tuple[np.ndarray, list[str], list[int]]:
    """Detect constant columns.

    Returns (X_reduced, kept_names, zero_variance_column_indices_in_input).
    """
    variances = np.var(X, axis=0, ddof=1) if X.shape[0] > 1 else np.zeros(X.shape[1])
    zero_idx = [j for j in range(X.shape[1]) if not np.isfinite(variances[j]) or variances[j] <= 0.0]

    if strategy is ZeroVarianceStrategy.ERROR and zero_idx:
        raise ValidationError(
            ErrorCode.ZERO_VARIANCE_COVARIATE,
            "covariate has zero variance and strategy is 'error'",
            {"columns": [names[j] for j in zero_idx]},
        )

    keep = [j for j in range(X.shape[1]) if j not in zero_idx]
    kept_names = [names[j] for j in keep]
    return X[:, keep], kept_names, zero_idx
