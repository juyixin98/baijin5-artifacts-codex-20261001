"""Per-fold standardization fit on training rows only.

Leakage control rule (acceptance criterion 2): the scaler for fold *k* is
estimated from rows ``fold != k`` alone and applied unchanged to the
validation rows.  Fitting one global scaler would leak validation-fold
distribution information (mean/variance) into every training matrix.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .errors import ComputationError


@dataclass(frozen=True)
class StandardScaler:
    mean: np.ndarray  # (p,)
    scale: np.ndarray  # (p,), strictly positive

    def transform(self, x: np.ndarray) -> np.ndarray:
        return (x - self.mean) / self.scale


def fit_scaler(x_train: np.ndarray) -> StandardScaler:
    """Fit on the training partition only."""
    mean = np.mean(x_train, axis=0)
    std = np.std(x_train, axis=0, ddof=0)
    # Constant column: divide by 1 instead of 0. Such a column is legal input
    # but carries no within-fold information after centering.
    scale = np.where(std > 1e-12, std, 1.0)
    if not np.all(np.isfinite(mean)) or not np.all(np.isfinite(scale)):
        raise ComputationError("non-finite values while fitting scaler")
    return StandardScaler(mean=mean, scale=scale)


def design_matrix(x: np.ndarray, scaler: StandardScaler) -> np.ndarray:
    """Standardize then prepend an intercept column."""
    z = scaler.transform(x)
    intercept = np.ones((z.shape[0], 1), dtype=np.float64)
    return np.hstack([intercept, z])
