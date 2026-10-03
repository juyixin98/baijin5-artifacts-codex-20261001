"""Regularized least-squares FIR estimation.

Model: response ~= convolution(excitation, h) with h of explicit length
`model_order`. The estimate solves the ridge problem

    min_h ||X h - y||^2 + lambda * ||h||^2

via the augmented least-squares system [X; sqrt(lambda) I] h = [y; 0],
which stays well-defined at lambda = 0 and never forms X^T X explicitly.

Honesty rules enforced here:

- Training error and held-out prediction error are computed on disjoint
  sample ranges and reported separately. The holdout tail is never used
  to fit.
- Predictions are always excitation-convolved-with-estimate. The
  observed response is never used as a predictor of itself.
- Identifiability is reported, not assumed: if the excitation spectrum
  is degenerate (rank-deficient or badly conditioned convolution
  matrix), the result carries identifiable=False with the reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .alignment import align_pair
from .contracts import (
    DEFAULT_MAX_SAMPLES,
    EstimateParams,
    validate_request,
)
from .convolution import convolution_matrix
from .errors import ComputationError
from .runlog import RunLogger, new_run_id

# Condition number above which the excitation is reported as spectrally
# degenerate even if the formal rank is full.
ILL_CONDITIONED_THRESHOLD = 1e10

# Relative singular-value threshold for the effective rank. Modes whose
# singular value is below EFFECTIVE_RANK_RTOL * sv_max carry too little
# excitation energy to identify the corresponding tap combinations.
# Rationale: for a zero-padded boundary design matrix, exact rank
# deficiency never occurs for n > L (boundary rows always contribute),
# so identifiability must be judged by effective rank, not formal rank.
EFFECTIVE_RANK_RTOL = 1e-2


@dataclass(frozen=True)
class FitDiagnostics:
    model_order: int
    delay: int
    regularization: float
    n_samples_aligned: int
    n_train: int
    n_holdout: int
    rank: int
    effective_rank: int
    singular_values: tuple[float, ...]
    condition_number: float
    identifiable: bool
    unidentifiable_reasons: tuple[str, ...]
    train_rmse: float
    holdout_rmse: float | None


@dataclass(frozen=True)
class EstimateResult:
    run_id: str
    coefficients: tuple[float, ...]
    diagnostics: FitDiagnostics

    def coefficients_array(self) -> np.ndarray:
        return np.asarray(self.coefficients, dtype=float)


def _split_counts(n: int, holdout_fraction: float, model_order: int) -> tuple[int, int]:
    n_holdout = int(round(n * holdout_fraction))
    n_train = n - n_holdout
    if n_train < model_order:
        raise ComputationError(
            "training segment smaller than model_order after holdout split",
            detail={
                "n_samples": n,
                "n_train": n_train,
                "n_holdout": n_holdout,
                "model_order": model_order,
            },
        )
    return n_train, n_holdout


def _rmse(residual: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(residual))))


def estimate_fir(
    excitation: object,
    response: object,
    params: EstimateParams,
    *,
    max_samples: int = DEFAULT_MAX_SAMPLES,
    logger: RunLogger | None = None,
    run_id: str | None = None,
) -> EstimateResult:
    """Estimate FIR coefficients from aligned excitation/response pairs.

    Raises FirBackendError subclasses on contract violations (INPUT),
    limit violations (RESOURCE) and numerical failures (COMPUTATION);
    every failure is logged with its category before being raised.
    """
    log = logger or RunLogger()
    rid = run_id or new_run_id()
    try:
        request = validate_request(
            excitation, response, params, max_samples=max_samples
        )
    except Exception as exc:
        category = getattr(exc, "category", None)
        log.log(
            rid,
            "validation_failed",
            category=category.value if hasattr(category, "value") else "unknown",
            message=str(exc),
        )
        raise
    log.log(
        rid,
        "inputs_validated",
        n_samples=request.samples.n_samples,
        model_order=params.model_order,
        delay=params.delay,
        regularization=params.regularization,
        holdout_fraction=params.holdout_fraction,
    )

    aligned = align_pair(request.samples, params.delay)
    if params.delay:
        log.log(
            rid,
            "alignment_applied",
            delay=params.delay,
            n_samples_aligned=aligned.n_samples,
            rationale="explicit delay removed before fitting",
        )

    x = aligned.excitation
    y = aligned.response
    n = aligned.n_samples
    order = params.model_order
    n_train, n_holdout = _split_counts(n, params.holdout_fraction, order)
    log.log(
        rid,
        "split",
        n_train=n_train,
        n_holdout=n_holdout,
        rationale="contiguous tail reserved for held-out prediction error",
    )

    design = convolution_matrix(x, order, mode="zero_pad")
    x_train = design[:n_train]
    y_train = y[:n_train]

    coefficients = _solve_ridge(x_train, y_train, params.regularization, log, rid)
    diagnostics = _diagnostics(
        x_train,
        design,
        y,
        coefficients,
        params,
        n,
        n_train,
        n_holdout,
        log,
        rid,
    )
    log.log(rid, "estimate_complete", identifiable=diagnostics.identifiable)
    return EstimateResult(
        run_id=rid,
        coefficients=tuple(float(c) for c in coefficients),
        diagnostics=diagnostics,
    )


def _solve_ridge(
    x_train: np.ndarray,
    y_train: np.ndarray,
    regularization: float,
    log: RunLogger,
    run_id: str,
) -> np.ndarray:
    order = x_train.shape[1]
    if regularization > 0:
        augmented_a = np.vstack([x_train, np.sqrt(regularization) * np.eye(order)])
        augmented_b = np.concatenate([y_train, np.zeros(order)])
    else:
        augmented_a = x_train
        augmented_b = y_train
    try:
        solution, _, _, _ = np.linalg.lstsq(augmented_a, augmented_b, rcond=None)
    except np.linalg.LinAlgError as exc:
        log.log(run_id, "computation_failed", category="computation_failure", message=str(exc))
        raise ComputationError(
            f"least-squares solver failed: {exc}", detail={"solver": "lstsq"}
        ) from exc
    if not np.all(np.isfinite(solution)):
        log.log(
            run_id,
            "computation_failed",
            category="computation_failure",
            message="solver returned non-finite coefficients",
        )
        raise ComputationError("solver returned non-finite coefficients")
    return solution


def _diagnostics(
    x_train: np.ndarray,
    design_full: np.ndarray,
    y_full: np.ndarray,
    coefficients: np.ndarray,
    params: EstimateParams,
    n_aligned: int,
    n_train: int,
    n_holdout: int,
    log: RunLogger,
    run_id: str,
) -> FitDiagnostics:
    order = x_train.shape[1]
    singular_values = np.linalg.svd(x_train, compute_uv=False)
    if singular_values.size == 0 or singular_values[0] == 0.0:
        rank = 0
        effective_rank = 0
        condition = float("inf")
    else:
        tol = singular_values[0] * max(x_train.shape) * np.finfo(float).eps
        rank = int(np.count_nonzero(singular_values > tol))
        effective_rank = int(
            np.count_nonzero(singular_values > singular_values[0] * EFFECTIVE_RANK_RTOL)
        )
        smallest = singular_values[-1]
        condition = (
            float(singular_values[0] / smallest) if smallest > 0 else float("inf")
        )

    reasons: list[str] = []
    if effective_rank < order:
        reasons.append(
            f"excitation spectrum degenerate: effective rank {effective_rank} < model order {order}"
        )
    if condition > ILL_CONDITIONED_THRESHOLD:
        reasons.append(
            f"design matrix ill-conditioned: condition number {condition:.3e} > {ILL_CONDITIONED_THRESHOLD:.0e}"
        )
    identifiable = not reasons

    train_pred = x_train @ coefficients
    train_rmse = _rmse(y_full[:n_train] - train_pred)
    holdout_rmse: float | None = None
    if n_holdout > 0:
        # Held-out prediction uses the excitation only; the observed
        # response of the holdout segment never enters the prediction.
        holdout_pred = design_full[n_train:] @ coefficients
        holdout_rmse = _rmse(y_full[n_train:] - holdout_pred)

    log.log(
        run_id,
        "solve_diagnostics",
        rank=rank,
        effective_rank=effective_rank,
        model_order=order,
        condition_number=condition,
        singular_values=singular_values,
        regularization=params.regularization,
        identifiable=identifiable,
        unidentifiable_reasons=reasons,
    )
    log.log(
        run_id,
        "metrics",
        train_rmse=train_rmse,
        holdout_rmse=holdout_rmse,
        rationale="train and holdout errors computed on disjoint sample ranges",
    )
    return FitDiagnostics(
        model_order=order,
        delay=params.delay,
        regularization=params.regularization,
        n_samples_aligned=n_aligned,
        n_train=n_train,
        n_holdout=n_holdout,
        rank=rank,
        effective_rank=effective_rank,
        singular_values=tuple(float(s) for s in singular_values),
        condition_number=condition,
        identifiable=identifiable,
        unidentifiable_reasons=tuple(reasons),
        train_rmse=train_rmse,
        holdout_rmse=holdout_rmse,
    )
