"""Local-linear estimation kernel.

The threshold is estimated by fitting *two separate* weighted regressions,
one on each side, and taking the difference of their intercepts evaluated at
the cutoff:

    tau = mu_+(c) - mu_-(c),   mu_s(c) = intercept of the side-s fit.

This is deliberately **not** the raw mean difference in a window: each side
is a local *linear* projection so that slope on the side is absorbed rather
than leaking into the jump.

Variance uses the heteroskedasticity-robust sandwich (HC3 by default). The
weighted design and hat matrix are retained here so the inference layer and
the wild bootstrap can reuse them without reconstructing the fit.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.contract import KernelName
from app.errors import SingularFitError
from app.kernels import weights as kernel_weights

LEFT = -1
RIGHT = +1

# Observations exactly at the cutoff are assigned to the treated side under
# the ``x >= cutoff`` rule (and to the left when treatment is coded below).
# Their count is surfaced separately by the mass-at-cutoff diagnostic.
CONDITION_NUMBER_TOL = 1e10


@dataclass
class SideResult:
    side: int
    x: np.ndarray  # raw x values used in this fit
    y: np.ndarray
    w: np.ndarray  # kernel weights (non-negative)
    design: np.ndarray  # [1, x-c] rows
    beta: np.ndarray  # [intercept, slope]
    fitted: np.ndarray
    resid: np.ndarray
    hat: np.ndarray  # leverages h_ii of the weighted projection
    bandwidth: float
    cutoff: float
    condition_number: float
    cluster_labels: np.ndarray | None = None

    @property
    def n(self) -> int:
        return int(self.x.size)

    @property
    def effective_n(self) -> float:
        return float(np.sum(self.w))

    @property
    def intercept(self) -> float:
        return float(self.beta[0])

    @property
    def slope(self) -> float:
        return float(self.beta[1])

    @property
    def max_distance(self) -> float:
        if self.x.size == 0:
            return 0.0
        return float(np.max(np.abs(self.x - self.cutoff)))


def _select_side_mask(
    x: np.ndarray, cutoff: float, side: int, at_cutoff_side: int
) -> np.ndarray:
    if side == at_cutoff_side:
        if side == RIGHT:
            return x >= cutoff
        return x <= cutoff
    if side == RIGHT:
        return x > cutoff
    return x < cutoff


def _select_side(
    x: np.ndarray, y: np.ndarray, cutoff: float, side: int, at_cutoff_side: int
) -> tuple[np.ndarray, np.ndarray]:
    mask = _select_side_mask(x, cutoff, side, at_cutoff_side)
    return x[mask], y[mask]


def fit_side(
    x: np.ndarray,
    y: np.ndarray,
    cutoff: float,
    bandwidth: float,
    kernel: KernelName,
    side: int,
    at_cutoff_side: int,
    clusters: np.ndarray | None = None,
) -> SideResult:
    """Weighted local-linear fit on one side within ``bandwidth``.

    Raises ``SingularFitError`` if the weighted design is rank deficient or
    numerically ill-conditioned beyond ``CONDITION_NUMBER_TOL``.
    """
    xs, ys = _select_side(x, y, cutoff, side, at_cutoff_side)
    cs = None if clusters is None else np.asarray(clusters)[
        _select_side_mask(x, cutoff, side, at_cutoff_side)
    ]
    w = kernel_weights(xs, cutoff, bandwidth, kernel)
    inside = w > 0.0
    xs, ys, w = xs[inside], ys[inside], w[inside]
    if cs is not None:
        cs = cs[inside]
    if xs.size < 3:
        raise SingularFitError(
            f"side {side:+d}: need >= 3 points in window for an identifiable "
            f"local-linear fit with residual degrees of freedom, found {xs.size}",
            details={"side": side, "n_in_window": int(xs.size)},
        )

    d = xs - cutoff
    X = np.column_stack([np.ones_like(d), d])
    sw = np.sqrt(w)
    Xw = X * sw[:, None]
    yw = ys * sw

    # SVD solve: reports rank/condition honestly, unlike a raw inverse.
    beta, residuals_arr, rank, sv = np.linalg.lstsq(Xw, yw, rcond=None)
    if rank < X.shape[1]:
        raise SingularFitError(
            f"side {side:+d}: rank-deficient weighted design (rank={rank})",
            details={"side": side, "rank": int(rank)},
        )
    cond = float(sv[0] / sv[-1]) if sv[-1] > 0 else np.inf
    if not np.isfinite(cond) or cond > CONDITION_NUMBER_TOL:
        raise SingularFitError(
            f"side {side:+d}: weighted design ill-conditioned (kappa={cond:.3e})",
            details={"side": side, "condition_number": cond},
        )

    fitted = X @ beta
    resid = ys - fitted

    # Leverages of the WLS fit: h_i = w_i * x_i' (X'WX)^-1 x_i.
    XtWX = Xw.T @ Xw
    XtWX_inv = np.linalg.inv(XtWX)
    hat = w * np.einsum("ij,jk,ik->i", X, XtWX_inv, X)
    # Guard the (1 - h) denominator in HC3 against rounding at h ~ 1.
    hat = np.clip(hat, -1.0, 0.999999)

    return SideResult(
        side=side,
        x=xs,
        y=ys,
        w=w,
        design=X,
        beta=beta,
        fitted=fitted,
        resid=resid,
        hat=hat,
        bandwidth=float(bandwidth),
        cutoff=float(cutoff),
        condition_number=cond,
        cluster_labels=cs,
    )


def jump(left: SideResult, right: SideResult, treatment_above: bool) -> float:
    """Signed jump consistent with the requested treatment coding."""
    raw = right.intercept - left.intercept
    return raw if treatment_above else -raw
