"""Synthetic data-generating processes (local fixtures, no external data).

Every scenario has a KNOWN true ATE so tests can assert numerical answers, not
just "the endpoint responds". Treatment effect is constant ``tau`` on all
scenarios; the scenarios differ in which nuisance model is correct:

* ``both_correct``   : logistic-linear propensity, linear outcome surfaces
* ``ps_only``        : propensity correct; outcome surfaces nonlinear in X
                        (linear OLS outcome models are misspecified)
* ``outcome_only``   : outcome surfaces linear; propensity is nonlinear in X
                        (the linear logistic model is misspecified)
* ``both_wrong``     : both sides nonlinear

The nonlinearities live in squared/interaction terms that are deliberately
absent from the fitted design (the models see raw x1, x2 only), so
misspecification is structural rather than a matter of noise.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

from .contract import Dataset

Scenario = Literal["both_correct", "ps_only", "outcome_only", "both_wrong"]
KNOWN_TAU = 0.5  # constant, homogeneous treatment effect in every scenario


def _sigmoid(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-z))


def _propensity_logit(x1: np.ndarray, x2: np.ndarray, scenario: Scenario) -> np.ndarray:
    if scenario in ("both_correct", "ps_only"):
        # linear logit: the fitted logistic model (raw x1, x2) is correct
        return -0.4 + 0.8 * x1 - 0.6 * x2
    # nonlinear logit: a logistic model linear in (x1, x2) is wrong
    return -0.3 + 1.1 * (x1 ** 2) - 0.9 * x2 + 0.7 * x1 * x2


def _outcome0(x1: np.ndarray, x2: np.ndarray, scenario: Scenario) -> np.ndarray:
    if scenario in ("both_correct", "outcome_only"):
        # linear in raw covariates: OLS on (1, x1, x2) is correct
        return 1.0 + 0.5 * x1 - 0.7 * x2
    # nonlinear: quadratic + interaction, OLS linear in (x1, x2) is wrong
    return 1.0 + 0.8 * (x1 ** 2) - 0.6 * x2 + 0.4 * x1 * x2


@dataclass(frozen=True)
class DGPResult:
    dataset: Dataset
    true_ate: float
    scenario: str
    seed: int


def make_dataset(seed: int = 0, n: int = 2000, scenario: Scenario = "both_correct",
                 *, noise_sd: float = 0.5) -> DGPResult:
    """Generate an i.i.d. synthetic dataset for one misspecification scenario."""
    rng = np.random.default_rng(seed)
    x1 = rng.normal(0.0, 1.0, n)
    x2 = rng.normal(0.0, 1.0, n)
    x = np.column_stack([x1, x2])

    e = _sigmoid(_propensity_logit(x1, x2, scenario))
    a = (rng.uniform(0.0, 1.0, n) < e).astype(float)

    y0 = _outcome0(x1, x2, scenario) + rng.normal(0.0, noise_sd, n)
    y1 = y0 + KNOWN_TAU  # constant additive effect => true ATE is KNOWN_TAU
    y = a * y1 + (1.0 - a) * y0
    return DGPResult(dataset=Dataset(x=x, a=a, y=y), true_ate=KNOWN_TAU,
                     scenario=scenario, seed=seed)


@dataclass(frozen=True)
class ClusterDGPResult:
    dataset: Dataset
    true_ate: float
    n_clusters: int
    seed: int


def make_clustered_dataset(seed: int = 0, n_clusters: int = 80,
                           members_per_cluster: int = 10,
                           *, noise_sd: float = 0.5) -> ClusterDGPResult:
    """Cluster-randomized DGP with an UNOBSERVED cluster-level shock.

    Per cluster g:
      z_g ~ N(0,1)                         observed cluster covariate
      u_g ~ N(0, 0.9)                      UNOBSERVED cluster shock (in y only)
      A_g ~ Bernoulli(sigmoid(-.3 + .9 z_g))   randomized at cluster level
    Per member:
      x1 = z_g + small noise,  x2 ~ N(0,1) individual covariate
      y  = 1 + .5 z_g + .3 x2 + .8 u_g + eps,  eps ~ N(0, noise_sd)
      observed outcome = y + A_g * tau

    Independent units are clusters. The latent u_g induces a strong positive
    intra-cluster residual correlation, so i.i.d. inference materially
    understates uncertainty. Because A_g depends only on observed z_g, the
    assignment is unconfounded and the linear logistic propensity (fit on x1)
    and the linear outcome regression are both compatible with the model —
    u_g is residual noise at the cluster level, not a confounder.
    """
    rng = np.random.default_rng(seed)
    g = n_clusters
    m = members_per_cluster
    z = rng.normal(0.0, 1.0, g)
    u = rng.normal(0.0, 0.9, g)
    a_g = (rng.uniform(size=g) < _sigmoid(-0.3 + 0.9 * z)).astype(float)
    if a_g.sum() < 5 or (1 - a_g).sum() < 5:
        return make_clustered_dataset(seed=seed + 1, n_clusters=n_clusters,
                                      members_per_cluster=members_per_cluster,
                                      noise_sd=noise_sd)

    cluster_ids = np.repeat(np.arange(g), m)
    x1 = np.repeat(z, m) + rng.normal(0.0, 0.1, g * m)
    x2 = rng.normal(0.0, 1.0, g * m)
    x = np.column_stack([x1, x2])
    a = np.repeat(a_g, m)
    y = (1.0 + 0.5 * np.repeat(z, m) + 0.3 * x2
         + 0.8 * np.repeat(u, m)
         + rng.normal(0.0, noise_sd, g * m))
    y = y + a * KNOWN_TAU
    return ClusterDGPResult(dataset=Dataset(x=x, a=a, y=y, clusters=cluster_ids),
                            true_ate=KNOWN_TAU, n_clusters=g, seed=seed)
