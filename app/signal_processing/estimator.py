"""Regularized least-squares FIR estimation.

Pipeline (each stage logs its key intermediate state):

1. Validate the sample contract (1-D, finite, equal length, order bounds).
2. Guard against fitting the output from itself: if excitation and response
   are identical the "channel" would be a trivial identity fit, so we refuse.
3. Align explicitly (caller-provided delay or documented heuristic estimate).
4. Build the boundary-consistent design matrix / observation pair.
5. Split rows contiguously into train and holdout segments — time-series
   data is never shuffled, and the holdout rows never touch the fit.
6. Assess identifiability of the *training* design matrix.
7. Solve the ridge normal equations ``(X^T X + lambda I) h = X^T y`` on the
   training rows only.
8. Report train error and held-out prediction error separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import scipy.linalg

from ..config import AppConfig
from ..errors import (
    ComputationError,
    IdentifiabilityError,
    InputError,
    ResourceExhaustedError,
)
from ..runlog import RunLogger
from .alignment import apply_delay, estimate_delay
from .convolution import BoundaryMode, build_design_and_observation
from .identifiability import IdentifiabilityReport, assess_identifiability


@dataclass(frozen=True)
class EstimationResult:
    coefficients: list[float]
    order: int
    boundary: str
    delay: int
    delay_source: str
    regularization: float
    identifiable: bool
    identifiability: IdentifiabilityReport
    train_metrics: dict[str, Any]
    holdout_metrics: dict[str, Any] | None
    warnings: list[str]


def _validate_signal(values: Any, name: str) -> np.ndarray:
    arr = np.asarray(values, dtype=float)
    if arr.ndim != 1:
        raise InputError(f"{name} must be a 1-D sequence", reason=f"{name}_not_1d")
    if arr.size == 0:
        raise InputError(f"{name} must not be empty", reason=f"{name}_empty")
    if not np.all(np.isfinite(arr)):
        raise InputError(
            f"{name} must contain only finite values",
            reason=f"{name}_non_finite",
        )
    return arr


def _check_resources(n_samples: int, order: int, config: AppConfig) -> None:
    if n_samples > config.max_samples:
        raise ResourceExhaustedError(
            "signal length exceeds the configured maximum number of samples",
            reason="too_many_samples",
            detail={"n_samples": n_samples, "max_samples": config.max_samples},
        )
    if order > config.max_order:
        raise ResourceExhaustedError(
            "model order exceeds the configured maximum",
            reason="order_too_large",
            detail={"order": order, "max_order": config.max_order},
        )
    cells = n_samples * order
    if cells > config.max_matrix_cells:
        raise ResourceExhaustedError(
            "design matrix would exceed the configured cell budget",
            reason="matrix_too_large",
            detail={"cells": cells, "max_matrix_cells": config.max_matrix_cells},
        )


def _solve_ridge(
    matrix: np.ndarray, observation: np.ndarray, regularization: float
) -> np.ndarray:
    gram = matrix.T @ matrix
    rhs = matrix.T @ observation
    if regularization > 0.0:
        gram = gram + regularization * np.eye(gram.shape[0])
    try:
        solution = scipy.linalg.solve(gram, rhs, assume_a="pos")
    except (scipy.linalg.LinAlgError, ValueError) as exc:
        raise ComputationError(
            "normal equations could not be solved; the excitation is likely "
            "rank-deficient — use regularization > 0 or a richer excitation",
            reason="normal_equations_singular",
            detail={"regularization": regularization, "solver_error": str(exc)},
        ) from exc
    if not np.all(np.isfinite(solution)):
        raise ComputationError(
            "solver returned non-finite coefficients",
            reason="non_finite_solution",
        )
    return solution


def _metrics(observed: np.ndarray, predicted: np.ndarray) -> dict[str, Any]:
    residual = observed - predicted
    mse = float(np.mean(residual**2))
    return {
        "n_samples": int(observed.size),
        "mse": mse,
        "rmse": float(np.sqrt(mse)),
    }


def estimate_fir(
    excitation: Any,
    response: Any,
    *,
    order: int,
    regularization: float | None = None,
    delay: int = 0,
    estimate_delay_flag: bool = False,
    boundary: BoundaryMode = "valid",
    holdout_fraction: float = 0.25,
    require_identifiable: bool = False,
    config: AppConfig | None = None,
    logger: RunLogger | None = None,
) -> EstimationResult:
    """Estimate FIR coefficients from known excitation and measured response.

    Only the excitation is used to build the regression; the response is
    never used as a regressor, so a successful fit is a genuine statement
    about the excitation->response channel.
    """
    config = config or AppConfig()
    log = logger or RunLogger()
    lam = config.default_regularization if regularization is None else float(regularization)

    # --- 1. sample contract -------------------------------------------------
    x = _validate_signal(excitation, "excitation")
    y = _validate_signal(response, "response")
    if x.size != y.size:
        raise InputError(
            "excitation and response must have equal length",
            reason="length_mismatch",
            detail={"n_excitation": int(x.size), "n_response": int(y.size)},
        )
    if not isinstance(order, int) or isinstance(order, bool) or order < 1:
        raise InputError(
            "model order must be a positive integer",
            reason="order_invalid",
            detail={"order": order},
        )
    if order > x.size:
        raise InputError(
            "model order must not exceed the signal length",
            reason="order_exceeds_signal",
            detail={"order": order, "n_samples": int(x.size)},
        )
    if lam < 0.0 or not np.isfinite(lam):
        raise InputError(
            "regularization must be a finite value >= 0",
            reason="regularization_invalid",
            detail={"regularization": lam},
        )
    if not 0.0 <= holdout_fraction < 1.0:
        raise InputError(
            "holdout_fraction must satisfy 0 <= holdout_fraction < 1",
            reason="holdout_fraction_invalid",
            detail={"holdout_fraction": holdout_fraction},
        )
    if np.array_equal(x, y):
        raise InputError(
            "excitation and response are identical; refusing to fit the "
            "output from itself and report it as a channel estimate",
            reason="excitation_equals_response",
        )
    _check_resources(int(x.size), order, config)
    log.log(
        "input_validated",
        n_samples=int(x.size),
        order=order,
        boundary=boundary,
        regularization=lam,
        holdout_fraction=holdout_fraction,
    )

    # --- 2. explicit time alignment ----------------------------------------
    if estimate_delay_flag:
        max_delay = min(config.max_delay, x.size - 1)
        used_delay = estimate_delay(x, y, max_delay)
        delay_source = "estimated_cross_correlation"
    else:
        used_delay = int(delay)
        delay_source = "explicit"
    x_al, y_al = apply_delay(x, y, used_delay)
    log.log(
        "aligned",
        delay=used_delay,
        delay_source=delay_source,
        n_samples_aligned=int(x_al.size),
        reason="response lag removed before building the design matrix",
    )

    # --- 3. boundary-consistent design matrix -------------------------------
    matrix, observation = build_design_and_observation(x_al, y_al, order, boundary)
    log.log(
        "design_matrix_built",
        boundary=boundary,
        n_rows=int(matrix.shape[0]),
        n_columns=int(matrix.shape[1]),
        reason="matrix rows and observation length are consistent by construction",
    )

    # --- 4. contiguous train / holdout split --------------------------------
    n_rows = matrix.shape[0]
    n_holdout = int(n_rows * holdout_fraction)
    n_train = n_rows - n_holdout
    if n_train < 1:
        raise InputError(
            "not enough rows for training after the holdout split; provide "
            "more samples or a smaller holdout_fraction/order",
            reason="insufficient_rows",
            detail={"n_rows": n_rows, "holdout_fraction": holdout_fraction},
        )
    x_train, y_train = matrix[:n_train], observation[:n_train]
    x_hold, y_hold = matrix[n_train:], observation[n_train:]
    log.log(
        "split",
        n_train_rows=n_train,
        n_holdout_rows=n_rows - n_train,
        reason="contiguous time split; holdout rows are excluded from the fit",
    )

    # --- 5. identifiability --------------------------------------------------
    report = assess_identifiability(x_train)
    warnings: list[str] = []
    if not report.identifiable:
        warnings.append(
            f"training design matrix is rank-deficient "
            f"(rank {report.rank} < order {report.n_columns}); the excitation "
            "spectrum is degenerate and the channel is not identifiable "
            "without regularization"
        )
    if report.condition_number > 1e8:
        warnings.append(
            f"design matrix is ill-conditioned (cond={report.condition_number:.3e})"
        )
    log.log(
        "identifiability_assessed",
        rank=report.rank,
        n_columns=report.n_columns,
        condition_number=report.condition_number,
        tolerance=report.tolerance,
        identifiable=report.identifiable,
        reason="numerical rank via SVD with tolerance s_max*max(M,N)*eps",
    )
    if require_identifiable and not report.identifiable:
        raise IdentifiabilityError(
            "excitation spectrum is degenerate: design matrix rank "
            f"{report.rank} < model order {report.n_columns}",
            reason="rank_deficient_excitation",
            detail=report.to_dict(),
        )

    # --- 6. regularized least squares ---------------------------------------
    coefficients = _solve_ridge(x_train, y_train, lam)
    log.log(
        "solved",
        method="ridge_normal_equations",
        regularization=lam,
        coefficient_norm=float(np.linalg.norm(coefficients)),
    )

    # --- 7. separate train / holdout errors ----------------------------------
    train_metrics = _metrics(y_train, x_train @ coefficients)
    holdout_metrics = (
        _metrics(y_hold, x_hold @ coefficients) if x_hold.shape[0] > 0 else None
    )
    log.log(
        "metrics_computed",
        train_mse=train_metrics["mse"],
        holdout_mse=None if holdout_metrics is None else holdout_metrics["mse"],
        reason="train and held-out prediction errors reported separately",
    )

    return EstimationResult(
        coefficients=[float(c) for c in coefficients],
        order=order,
        boundary=boundary,
        delay=used_delay,
        delay_source=delay_source,
        regularization=lam,
        identifiable=report.identifiable,
        identifiability=report,
        train_metrics=train_metrics,
        holdout_metrics=holdout_metrics,
        warnings=warnings,
    )
