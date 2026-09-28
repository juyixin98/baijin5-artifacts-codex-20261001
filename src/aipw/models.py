"""Nuisance models implemented directly on NumPy/SciPy.

Why hand-rolled rather than sklearn:
* the train-only standardization boundary is explicit and auditable;
* out-of-fold predictions are produced by the crossfit layer, so there is no
  ambient global state that could let validation rows leak into a fit;
* every numerical failure surfaces as ``ComputationFailure`` with context.

All models expose the same minimal surface: ``fit(X, y) -> self`` and
``predict(X) -> ndarray``. A fitted model is immutable from the caller's point
of view: ``fit`` returns the instance but never mutates the input arrays.
"""
from __future__ import annotations

import numpy as np

from .contract import ComputationFailure, InputError


# --------------------------------------------------------------------------- #
# Train-only standardization
# --------------------------------------------------------------------------- #
class Standardizer:
    """Column standardizer whose statistics are fit on TRAIN rows only.

    ``transform`` applied to validation rows deliberately uses the *training*
    mean/scale, so validation columns are generally NOT centered at zero.
    The diagnostics layer checks exactly this property to detect leakage.
    Constant columns are scaled to 1.0 (left centered at 0) instead of divided
    by zero.
    """

    def __init__(self) -> None:
        self.mean_: np.ndarray | None = None
        self.scale_: np.ndarray | None = None
        self.fitted_ = False

    def fit(self, x: np.ndarray) -> "Standardizer":
        x = np.asarray(x, dtype=float)
        if x.ndim != 2:
            raise InputError("Standardizer.fit expects a 2-D matrix")
        self.mean_ = x.mean(axis=0)
        scale = x.std(axis=0, ddof=0)
        self.scale_ = np.where(scale > 1e-12, scale, 1.0)
        self.fitted_ = True
        return self

    def transform(self, x: np.ndarray) -> np.ndarray:
        if not self.fitted_:
            raise ComputationFailure("Standardizer.transform called before fit")
        return (np.asarray(x, dtype=float) - self.mean_) / self.scale_

    def fit_transform(self, x: np.ndarray) -> np.ndarray:
        return self.fit(x).transform(x)


def _design(x: np.ndarray, intercept: bool) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    if x.ndim != 2:
        raise InputError("expected a 2-D covariate matrix")
    if intercept:
        return np.column_stack([np.ones(x.shape[0]), x])
    return x


# --------------------------------------------------------------------------- #
# Logistic regression (propensity) via L2-penalized IRLS
# --------------------------------------------------------------------------- #
class LogisticRegression:
    """Binary logistic regression fit by iteratively reweighted least squares.

    The L2 penalty acts on non-intercept coefficients only. Propensity outputs
    are clipped to an open interval strictly inside (0, 1) so the IPW weights
    can never be +inf; explicit trimming happens separately in the estimator.
    """

    _EPS = 1e-12

    def __init__(self, l2_penalty: float = 0.0, max_iter: int = 200, tol: float = 1e-10):
        self.l2_penalty = float(l2_penalty)
        self.max_iter = int(max_iter)
        self.tol = float(tol)
        self.beta_: np.ndarray | None = None
        self.fitted_ = False

    @staticmethod
    def _sigmoid(z: np.ndarray) -> np.ndarray:
        # stable elementwise logistic
        out = np.empty_like(z, dtype=float)
        pos = z >= 0
        out[pos] = 1.0 / (1.0 + np.exp(-z[pos]))
        ez = np.exp(z[~pos])
        out[~pos] = ez / (1.0 + ez)
        return out

    def fit(self, x: np.ndarray, a: np.ndarray) -> "LogisticRegression":
        x = _design(x, intercept=True)
        a = np.asarray(a, dtype=float)
        n, d = x.shape
        if a.shape != (n,):
            raise InputError("treatment vector shape does not match X")
        unique = np.unique(a)
        if not np.all(np.isin(unique, [0.0, 1.0])):
            raise InputError("treatment must be binary 0/1")
        if unique.size < 2:
            # A fold containing only treated or only controls is a positivity
            # failure, not a silent "fit mean of constant" situation.
            raise ComputationFailure(
                "cannot fit propensity on a fold with a single treatment level",
                details={"levels": unique.tolist(), "n": n},
            )

        beta = np.zeros(d)
        penalty = np.eye(d) * self.l2_penalty
        penalty[0, 0] = 0.0  # do not penalize the intercept

        for iteration in range(self.max_iter):
            eta = x @ beta
            p = self._sigmoid(eta)
            w = np.maximum(p * (1.0 - p), self._EPS)
            score = x.T @ (a - p) - penalty @ beta
            hessian = (x.T * w) @ x + penalty
            try:
                step = np.linalg.solve(hessian, score)
            except np.linalg.LinAlgError as exc:
                raise ComputationFailure(
                    "singular Hessian while fitting propensity model",
                    details={"iteration": iteration},
                ) from exc
            beta_new = beta + step
            if not np.all(np.isfinite(beta_new)):
                raise ComputationFailure(
                    "non-finite coefficient in propensity IRLS",
                    details={"iteration": iteration},
                )
            if np.max(np.abs(step)) < self.tol:
                beta = beta_new
                break
            beta = beta_new
        else:
            raise ComputationFailure(
                "propensity IRLS did not converge",
                details={"max_iter": self.max_iter, "tol": self.tol},
            )

        self.beta_ = beta
        self.fitted_ = True
        return self

    def predict_proba(self, x: np.ndarray) -> np.ndarray:
        if not self.fitted_:
            raise ComputationFailure("predict_proba called before fit")
        x = _design(x, intercept=True)
        p = self._sigmoid(x @ self.beta_)
        return np.clip(p, self._EPS, 1.0 - self._EPS)


# --------------------------------------------------------------------------- #
# Ordinary least squares (outcome models) with train-only standardization
# --------------------------------------------------------------------------- #
class OLSRidge:
    """OLS (optionally ridge) outcome regression on standardized covariates.

    Standardization stats come from the training fold only. An L2 penalty is
    available for numerically degenerate designs but defaults to zero (plain
    OLS), which is what the correctness fixtures expect.
    """

    def __init__(self, intercept: bool = True, standardize: bool = True,
                 l2_penalty: float = 0.0):
        self.intercept = intercept
        self.standardize = standardize
        self.l2_penalty = float(l2_penalty)
        self.scaler_: Standardizer | None = None
        self.beta_: np.ndarray | None = None
        self.fitted_ = False

    def fit(self, x: np.ndarray, y: np.ndarray) -> "OLSRidge":
        x = np.asarray(x, dtype=float)
        y = np.asarray(y, dtype=float)
        if x.ndim != 2 or y.shape != (x.shape[0],):
            raise InputError("OLS got mismatched X/y shapes")
        if x.shape[0] <= x.shape[1] + (1 if self.intercept else 0) and self.l2_penalty == 0.0:
            raise ComputationFailure(
                "fewer observations than coefficients in outcome model; "
                "use more folds or a regularized model",
                details={"n": int(x.shape[0]), "p": int(x.shape[1])},
            )

        if self.standardize:
            self.scaler_ = Standardizer().fit(x)
            z = self.scaler_.transform(x)
        else:
            z = x
        z = _design(z, intercept=self.intercept)
        d = z.shape[1]
        gram = z.T @ z
        if self.l2_penalty > 0:
            penalty = np.eye(d) * self.l2_penalty
            if self.intercept:
                penalty[0, 0] = 0.0
            gram = gram + penalty
        try:
            self.beta_ = np.linalg.solve(gram, z.T @ y)
        except np.linalg.LinAlgError as exc:
            raise ComputationFailure("singular design in outcome OLS") from exc
        if not np.all(np.isfinite(self.beta_)):
            raise ComputationFailure("non-finite outcome OLS coefficients")
        self.fitted_ = True
        return self

    def predict(self, x: np.ndarray) -> np.ndarray:
        if not self.fitted_:
            raise ComputationFailure("predict called before fit")
        x = np.asarray(x, dtype=float)
        z = self.scaler_.transform(x) if self.standardize and self.scaler_ else x
        z = _design(z, intercept=self.intercept)
        return z @ self.beta_

    def training_residual_sum_squares(self, x: np.ndarray, y: np.ndarray) -> float:
        resid = np.asarray(y, dtype=float) - self.predict(x)
        return float(resid @ resid)
