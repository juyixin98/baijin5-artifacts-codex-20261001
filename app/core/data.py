"""Input validation and preparation for experiment data.

Policies (declared in :class:`app.core.contracts.Settings`) are applied
explicitly; the counts of every dropped row and column are reported back so
nothing is silently discarded.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .contracts import (
    ErrorCode,
    EstimationError,
    MissingPolicy,
    ZeroVariancePolicy,
)

EPS = 1e-12


def _spread_is_zero(arr: np.ndarray) -> bool:
    """Scale-relative zero-variance test on the observed values.

    A constant is defined relative to the column's own magnitude
    (``sd <= EPS * max(1, |mean|)``) so neither a genuinely varying 1e-10-scale
    column is dropped nor a near-constant 1e9-scale column is fed to OLS.
    An all-missing column has no spread information and counts as zero.
    """
    finite = arr[~np.isnan(arr)]
    if finite.size == 0:
        return True
    sd = float(finite.std(ddof=1)) if finite.size > 1 else 0.0
    return sd <= EPS * max(1.0, abs(float(finite.mean())))


@dataclass(frozen=True)
class PreparedData:
    y: np.ndarray                    # outcome, shape (n,)
    t: np.ndarray                    # 0/1 treatment indicator, shape (n,)
    x: np.ndarray                    # covariates, shape (n, k)
    covariate_names: tuple[str, ...]
    n_rows: int
    n_complete_rows: int
    n_dropped_rows: int
    dropped_covariates: tuple[str, ...]
    dropped_covariate_missing_counts: tuple[int, ...]
    covariate_missing_counts: tuple[int, ...]  # per retained covariate
    outcome_missing_count: int
    constant_columns: tuple[str, ...]
    warnings: tuple[str, ...]


def prepare_data(
    columns: dict[str, list],
    outcome_column: str,
    treatment_column: str,
    requested_covariates: list[str],
    missing_policy: MissingPolicy,
    zero_variance_policy: ZeroVariancePolicy,
) -> PreparedData:
    """Validate raw columns and build numeric ``(y, t, X)`` arrays.

    ``columns`` maps column name -> list of JSON numbers / nulls.
    """
    warnings: list[str] = []

    # --- structural validation ------------------------------------------- #
    if outcome_column not in columns:
        raise EstimationError(
            ErrorCode.INVALID_PAYLOAD,
            f"outcome column {outcome_column!r} not found in data",
        )
    if treatment_column not in columns:
        raise EstimationError(
            ErrorCode.INVALID_PAYLOAD,
            f"treatment column {treatment_column!r} not found in data",
        )
    missing = [c for c in requested_covariates if c not in columns]
    if missing:
        raise EstimationError(
            ErrorCode.UNKNOWN_COVARIATE,
            f"covariate column(s) not found in data: {missing}",
            {"missing_columns": missing},
        )
    duplicates = _duplicates(requested_covariates)
    if duplicates:
        raise EstimationError(
            ErrorCode.DUPLICATE_COLUMN,
            f"duplicated covariate column(s): {sorted(duplicates)}",
            {"duplicates": sorted(duplicates)},
        )

    lengths = {name: len(vals) for name, vals in columns.items()}
    n_rows = lengths[outcome_column]
    if n_rows == 0:
        raise EstimationError(ErrorCode.EMPTY_DATA, "experiment data has zero rows")
    bad_len = {name: n for name, n in lengths.items() if n != n_rows}
    if bad_len:
        raise EstimationError(
            ErrorCode.INVALID_PAYLOAD,
            f"all columns must have the same length; mismatches: {bad_len}",
            {"lengths": bad_len},
        )

    y = _to_float_array(columns[outcome_column], outcome_column)
    t_raw = columns[treatment_column]
    t, arm_label = _coerce_treatment(t_raw)

    outcome_missing = int(np.isnan(y).sum())
    cov_missing_counts_raw: dict[str, int] = {}
    x_raw: dict[str, np.ndarray] = {}
    for name in requested_covariates:
        arr = _to_float_array(columns[name], name)
        cov_missing_counts_raw[name] = int(np.isnan(arr).sum())
        x_raw[name] = arr

    # --- missing-value policy -------------------------------------------- #
    candidate = [y] + [x_raw[name] for name in requested_covariates]
    missing_mask = np.zeros(n_rows, dtype=bool)
    for arr in candidate:
        missing_mask |= np.isnan(arr)
    n_missing_rows = int(missing_mask.sum())

    if n_missing_rows > 0 and missing_policy is MissingPolicy.FAIL:
        raise EstimationError(
            ErrorCode.MISSING_VALUES_PRESENT,
            f"{n_missing_rows} row(s) contain missing outcome/covariate "
            "values; choose missing_policy 'complete_cases' or 'mean_impute'",
            {"rows_with_missing": n_missing_rows},
        )

    if missing_policy is MissingPolicy.COMPLETE_CASES and n_missing_rows > 0:
        keep = ~missing_mask
        y = y[keep]
        t = t[keep]
        for name in list(x_raw):
            x_raw[name] = x_raw[name][keep]
        warnings.append(
            f"complete_cases: dropped {n_missing_rows} of {n_rows} rows "
            "due to missing values"
        )
    elif missing_policy is MissingPolicy.MEAN_IMPUTE and n_missing_rows > 0:
        # Impute outcome too (declared policy); mean is computed from observed
        # values BEFORE any row deletion.
        y = _impute_mean(y)
        for name in list(x_raw):
            x_raw[name] = _impute_mean(x_raw[name])
        warnings.append(
            f"mean_impute: filled {n_missing_rows} row(s) containing "
            "missing values with observed column means"
        )

    n_eff = y.shape[0]
    if n_eff == 0:
        raise EstimationError(ErrorCode.EMPTY_DATA, "no rows remain after missing policy")

    # --- treatment arms ---------------------------------------------------- #
    arms_present = set(np.unique(t[~np.isnan(t)]).astype(int).tolist())
    if not arms_present.issubset({0, 1}):
        raise EstimationError(
            ErrorCode.NON_BINARY_TREATMENT,
            f"treatment column must be binary 0/1 (mapping {arm_label}); "
            f"found values {sorted(arms_present)}",
        )
    n1 = int((t == 1).sum())
    n0 = int((t == 0).sum())
    if n0 == 0 or n1 == 0:
        raise EstimationError(
            ErrorCode.MISSING_ARM,
            f"both arms required after preparation: control n={n0}, treatment n={n1}",
            {"n_control": n0, "n_treatment": n1},
        )
    if n0 < 2 or n1 < 2:
        # A within-arm variance (and Welch SE) needs at least two units;
        # reject before diagnostics so no NaN SMD/correlation is produced.
        raise EstimationError(
            ErrorCode.INSUFFICIENT_SAMPLE,
            f"each arm needs >= 2 observations after preparation: "
            f"control n={n0}, treatment n={n1}",
            {"n_control": n0, "n_treatment": n1},
        )

    # --- zero-variance covariates ----------------------------------------- #
    retained: list[str] = []
    dropped: list[str] = []
    constants: list[str] = []
    for name in requested_covariates:
        arr = x_raw[name]
        if _spread_is_zero(arr):
            constants.append(name)
            n_obs = int((~np.isnan(arr)).sum())
            why = ("contains no observed values" if n_obs == 0
                   else "has (near-)zero variance")
            if zero_variance_policy is ZeroVariancePolicy.FAIL:
                raise EstimationError(
                    ErrorCode.ZERO_VARIANCE_COVARIATE,
                    f"covariate {name!r} {why} and "
                    "zero_variance_policy='fail'",
                    {"covariate": name, "observed": n_obs},
                )
            if zero_variance_policy is ZeroVariancePolicy.DROP:
                dropped.append(name)
                warnings.append(f"dropped covariate {name!r}: {why}")
                continue
        retained.append(name)

    x = (
        np.column_stack([x_raw[name] for name in retained])
        if retained
        else np.empty((n_eff, 0))
    )

    # Outcome zero variance is fatal regardless (effect is not estimable).
    if _spread_is_zero(y):
        raise EstimationError(
            ErrorCode.ZERO_VARIANCE_OUTCOME,
            "outcome has (near-)zero variance or no observed values; "
            "standard error undefined",
        )

    return PreparedData(
        y=y,
        t=t,
        x=x,
        covariate_names=tuple(retained),
        n_rows=n_rows,
        n_complete_rows=n_eff,
        n_dropped_rows=n_rows - n_eff,
        dropped_covariates=tuple(dropped),
        dropped_covariate_missing_counts=tuple(
            cov_missing_counts_raw[n] for n in dropped),
        covariate_missing_counts=tuple(cov_missing_counts_raw[n] for n in retained),
        outcome_missing_count=outcome_missing,
        constant_columns=tuple(constants),
        warnings=tuple(warnings),
    )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _to_float_array(values: list, name: str) -> np.ndarray:
    out = np.empty(len(values), dtype=np.float64)
    for i, v in enumerate(values):
        if v is None:
            out[i] = np.nan
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError) as exc:
            raise EstimationError(
                ErrorCode.INVALID_PAYLOAD,
                f"column {name!r} row {i}: value {v!r} is not numeric or null",
            ) from exc
        if not np.isfinite(fv) and not np.isnan(fv):
            raise EstimationError(
                ErrorCode.INVALID_PAYLOAD,
                f"column {name!r} row {i}: non-finite value {v!r}",
            )
        out[i] = fv
    return out


def _coerce_treatment(values: list) -> tuple[np.ndarray, dict]:
    """Accept 0/1 numbers or booleans; reject everything else incl. null."""
    out = np.empty(len(values), dtype=np.float64)
    for i, v in enumerate(values):
        if isinstance(v, bool):
            out[i] = int(v)
        elif v in (0, 1):
            out[i] = v
        elif v is None:
            raise EstimationError(
                ErrorCode.INVALID_PAYLOAD,
                f"treatment row {i}: assignment is null; arm membership must "
                "be known (missingness policies apply to outcome/covariates, "
                "not to treatment assignment)",
                {"row": i},
            )
        else:
            raise EstimationError(
                ErrorCode.NON_BINARY_TREATMENT,
                f"treatment row {i}: value {v!r} is not binary (0/1 or bool); "
                "multi-arm designs are not supported by this backend",
                {"value": v, "row": i},
            )
    label = {"0": "control", "1": "treatment"}
    return out, label


def _impute_mean(a: np.ndarray) -> np.ndarray:
    mask = np.isnan(a)
    if not mask.any():
        return a
    filled = a.copy()
    if mask.all():
        # No observed mean exists; leave NaNs so the zero-variance guard
        # rejects/ drops the column explicitly instead of emitting a warning.
        return filled
    filled[mask] = np.nanmean(a)
    return filled


def _duplicates(items: list[str]) -> set[str]:
    seen: set[str] = set()
    dup: set[str] = set()
    for item in items:
        if item in seen:
            dup.add(item)
        seen.add(item)
    return dup
