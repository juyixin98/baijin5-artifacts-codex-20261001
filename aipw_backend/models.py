"""Nuisance models, implemented independently from the estimator.

Two intentionally simple, inspectable parametric models are provided:

* :class:`RidgeLogistic`  - L2-penalized logistic regression via Newton/IRLS.
* :class:`RidgeOLS`       - L2-penalized linear regression, closed form.
* :class:`WrongConstant`  - deliberately misspecified model that ignores all
  features (sample-mean prediction); used by the misspecification fixtures to
  prove double robustness rather than to analyse real data.

These do not wrap scikit-learn: the acceptance rules require the reference
answer and the production core to be independent implementations, so both the
closed forms and the solver are written out here against NumPy/SciPy only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .errors import ComputationError

_CLIP = 30.0  # argument bound for exp to avoid overflow


def _sigmoid(eta: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(eta, -_CLIP, _CLIP)))


@dataclass(frozen=True)
class FitResult:
    coef: np.ndarray
    converged: bool
    n_iter: int


class RidgeLogistic:
    """Logistic regression with ridge penalty ``C' (beta^T beta) / 2``.

    The intercept is penalized together with the slopes here because the
    design already passes through per-fold standardization; the penalty is
    tiny by default.  Newton iterations stop at ``|beta_new-beta_inf| < tol``.
    """

    def __init__(self, penalty: float = 1e-6, max_iter: int = 100, tol: float = 1e-8):
        self.penalty = float(penalty)
        self.max_iter = int(max_iter)
        self.tol = float(tol)

    def fit(self, x: np.ndarray, a: np.ndarray) -> FitResult:
        d = x.shape[1]
        beta = np.zeros(d, dtype=np.float64)
        reg = self.penalty * np.eye(d, dtype=np.float64)
        converged = False
        n_iter = 0
        for it in range(1, self.max_iter + 1):
            n_iter = it
            eta = x @ beta
            p = _sigmoid(eta)
            w = p * (1.0 - p)
            # Degenerate weights (all predictions 0/1) make the system
            # singular; floor them so the Newton step stays defined.
            w = np.maximum(w, 1e-12)
            grad = x.T @ (a - p) - self.penalty * beta
            hess = (x.T * w) @ x + reg
            try:
                step = np.linalg.solve(hess, grad)
            except np.linalg.LinAlgError as exc:
                raise ComputationError(
                    "logistic Newton system is singular",
                    details={"fold_iteration": it},
                ) from exc
            beta = beta + step
            if not np.all(np.isfinite(beta)):
                raise ComputationError(
                    "non-finite logistic coefficients", details={"iteration": it}
                )
            if np.max(np.abs(step)) < self.tol:
                converged = True
                break
        if not converged:
            raise ComputationError(
                f"logistic regression did not converge in {self.max_iter} iters",
                details={"last_step_max": float(np.max(np.abs(step)))},
            )
        return FitResult(coef=beta, converged=True, n_iter=n_iter)

    @staticmethod
    def predict_proba(x: np.ndarray, fit: FitResult) -> np.ndarray:
        p = _sigmoid(x @ fit.coef)
        # Keep strictly inside (0,1); contract.check_positivity handles reports.
        return np.clip(p, 1e-15, 1.0 - 1e-15)


class RidgeOLS:
    """Ridge linear regression with the normal equations."""

    def __init__(self, penalty: float = 1e-6):
        self.penalty = float(penalty)

    def fit(self, x: np.ndarray, y: np.ndarray) -> FitResult:
        d = x.shape[1]
        reg = self.penalty * np.eye(d, dtype=np.float64)
        gram = x.T @ x + reg
        rhs = x.T @ y
        try:
            beta = np.linalg.solve(gram, rhs)
        except np.linalg.LinAlgError as exc:
            raise ComputationError("OLS normal equations are singular") from exc
        if not np.all(np.isfinite(beta)):
            raise ComputationError("non-finite OLS coefficients")
        return FitResult(coef=beta, converged=True, n_iter=1)

    @staticmethod
    def predict(x: np.ndarray, fit: FitResult) -> np.ndarray:
        out = x @ fit.coef
        if not np.all(np.isfinite(out)):
            raise ComputationError("non-finite outcome predictions")
        return out


class WrongConstant:
    """Misspecification probe: predict the training sample mean for everyone.

    As a *treatment* model it returns the treated fraction (constant
    propensity); as an *outcome* model the outcome mean.  It has a real,
    finite population limit and lets the double-robustness tests turn one
    nuisance channel off at a time.
    """

    def fit(self, x: np.ndarray, target: np.ndarray) -> FitResult:
        return FitResult(
            coef=np.asarray([float(np.mean(target))]), converged=True, n_iter=1
        )

    @staticmethod
    def _constant(fit: FitResult, n: int) -> np.ndarray:
        return np.full(n, fit.coef[0], dtype=np.float64)

    def predict(self, x: np.ndarray, fit: FitResult) -> np.ndarray:
        return self._constant(fit, x.shape[0])

    def predict_proba(self, x: np.ndarray, fit: FitResult) -> np.ndarray:
        q = float(np.clip(fit.coef[0], 1e-6, 1.0 - 1e-6))
        return np.full(x.shape[0], q, dtype=np.float64)
