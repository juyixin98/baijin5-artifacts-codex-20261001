"""Synthetic data-generating process (reusable fixtures).

The DGP is shared by the test suite and the replication experiment but is
**never imported by the estimation kernel**: the kernel only sees the
generated arrays, while the true nuisance functions live here.  The reference
module (:mod:`aipw_backend.reference`) uses these true functions to build
answers the kernel cannot have produced itself.

Structural model
----------------
* X1, X2 independent standard normals.
* e(X)  = sigmoid(gamma0 + gamma1 X1 + gamma2 X2)           (logistic, correct
  specification for ``logistic_ridge`` on [1, X1, X2]).
* mu_a(X) = alpha0 + alpha1 X1 + alpha2 X2
            + a * (tau + delta * X1)                        (linear, correct
  specification for separate per-arm ``ols_ridge`` fits).
* Y = mu_A(X) (+ cluster effect) + epsilon, epsilon ~ N(0, sigma_v^2).

ATE = tau (E[X1] = 0 removes the heterogeneous term delta*X1 in average).

Misspecification switches are *estimator-side* (which model class is used in
the config), not DGP-side: the same true law backs all four scenarios so
bias differences isolate the estimator's behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

_SIGMOID_CLIP = 30.0


def sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -_SIGMOID_CLIP, _SIGMOID_CLIP)))


@dataclass(frozen=True)
class DGPParams:
    gamma0: float = 0.2
    gamma1: float = 0.8
    gamma2: float = 0.6
    alpha0: float = 1.0
    alpha1: float = 1.0
    alpha2: float = -0.8
    tau: float = 2.0
    delta: float = 0.6  # heterogeneous treatment effect on X1
    sigma: float = 1.0  # idiosyncratic residual SD


@dataclass(frozen=True)
class SyntheticSample:
    x: np.ndarray
    a: np.ndarray
    y: np.ndarray
    cluster: np.ndarray | None
    params: DGPParams
    seed: int

    def true_effect(self) -> float:
        return float(self.params.tau)


def true_propensity(x: np.ndarray, p: DGPParams) -> np.ndarray:
    z = p.gamma0 + p.gamma1 * x[:, 0] + p.gamma2 * x[:, 1]
    return sigmoid(z)


def true_outcome(x: np.ndarray, a: np.ndarray, p: DGPParams) -> np.ndarray:
    return (
        p.alpha0
        + p.alpha1 * x[:, 0]
        + p.alpha2 * x[:, 1]
        + a * (p.tau + p.delta * x[:, 0])
    )


def asymptotic_bias_both_wrong(p: DGPParams) -> float:
    """Approximate bias of the unadjusted mean difference (the both-wrong
    estimator's probability limit).

    With constant mu models the AIPW limit is E[Y|A=1]-E[Y|A=0].  Linearizing
    the sigmoid around its midpoint gives covariate imbalance
    E[Xj|A=1]-E[Xj|A=0] ~= gamma_j, hence bias ~= alpha1*gamma1
    + alpha2*gamma2.  Used only to choose fixtures / sign-check the test.
    """
    return float(p.alpha1 * p.gamma1 + p.alpha2 * p.gamma2)


def generate_sample(
    n: int,
    seed: int,
    *,
    tau: float = 2.0,
    params: DGPParams | None = None,
    n_clusters: int | None = None,
    cluster_size: int | None = None,
    icc: float = 0.0,
    cluster_randomized: bool = False,
) -> SyntheticSample:
    """Draw one iid (default) or clustered sample.

    Clustered mode requires ``n = n_clusters * cluster_size``.  Cluster
    effects enter through the residual ``c_g + v_i`` with ``Var(c_g) = icc``
    and ``Var(v_i) = 1 - icc`` (total residual variance 1).

    Randomization:

    * ``cluster_randomized=False``: A_i | X_i ~ Bernoulli(e(X_i)) (default);
    * ``cluster_randomized=True``: treatment is assigned to whole clusters
      from a cluster-level logistic model driven by the cluster MEAN
      covariates, ``A_g ~ Bernoulli(sigmoid(gamma0 + gamma1*meanX1_g + ...))``.
      This is the canonical design in which ignoring clusters under-covers:
      the common cluster shock does not cancel because a cluster lies wholly
      in one arm.
    """
    p = DGPParams(tau=tau) if params is None else DGPParams(
        **{**params.__dict__, "tau": tau}
    )
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((n, 2))
    a: np.ndarray
    if n_clusters is not None and cluster_randomized:
        a = np.empty(n, dtype=np.int8)
    else:
        e = true_propensity(x, p)
        a = (rng.random(n) < e).astype(np.int8)

    cluster: np.ndarray | None = None
    residual = rng.standard_normal(n) * p.sigma
    if n_clusters is not None:
        if cluster_size is None or n_clusters * cluster_size != n:
            raise ValueError("clustered sample requires n = n_clusters*cluster_size")
        sigma_c = float(np.sqrt(icc))
        sigma_v = float(np.sqrt(max(1.0 - icc, 0.0)))
        cluster = np.repeat(np.arange(n_clusters, dtype=np.int64), cluster_size)
        c_g = rng.standard_normal(n_clusters) * sigma_c
        residual = c_g[cluster] + rng.standard_normal(n) * sigma_v
        if cluster_randomized:
            # Cluster-level propensity from mean covariates, then whole
            # clusters assigned; repair tiny arm imbalance by flips only if a
            # fold feasibility bound is violated (rare at 150 clusters).
            mean_x = np.stack(
                [x[cluster == g].mean(axis=0) for g in range(n_clusters)]
            )
            e_g = true_propensity(mean_x, p)
            a_g = (rng.random(n_clusters) < e_g).astype(np.int8)
            a = a_g[cluster]
            a = _repair_homogeneous_cluster_arms(a_g, cluster, a, rng)
        else:
            a = _repair_cluster_arms(cluster, a, rng)

    y = true_outcome(x, a, p) + residual
    return SyntheticSample(
        x=x, a=a, y=y, cluster=cluster, params=p, seed=int(seed)
    )


def _repair_homogeneous_cluster_arms(
    a_g: np.ndarray,
    cluster: np.ndarray,
    a: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    """Ensure both arms exist (and at least a handful of clusters each) for a
    cluster-randomized draw; flips whole clusters, never individuals."""
    min_per_arm = 5
    for target_arm in (1, 0):
        while int(np.sum(a_g == target_arm)) < min_per_arm:
            other = np.flatnonzero(a_g != target_arm)
            g = int(other[int(rng.integers(other.shape[0]))])
            a_g[g] = target_arm
            a[cluster == g] = target_arm
    return a


def _repair_cluster_arms(
    cluster: np.ndarray, a: np.ndarray, rng: np.random.Generator
) -> np.ndarray:
    a = a.copy()
    for g in np.unique(cluster):
        idx = np.flatnonzero(cluster == g)
        arms = a[idx]
        if np.all(arms == 1) or np.all(arms == 0):
            # Flip one row so the cluster contains both arms.
            flip = idx[int(rng.integers(idx.shape[0]))]
            a[flip] = 1 - a[flip]
    return a
