"""Boundary validation and the end-to-end estimation pipeline.

``run_estimate`` is the single composition root: validate -> fold -> cross-fit
nuisance models -> kernel -> influence-function inference -> diagnostics ->
typed result. It is deliberately small; every statistical decision lives in
its own module.
"""
from __future__ import annotations

import uuid
from typing import Any

import numpy as np

from .contract import (
    Config, Dataset, DOUBLE_ROBUST_CONDITION, EstimateResult, FoldDiagnostics,
    InputError,
)
from .crossfit import (
    OOFPredictions, cross_fit, make_cluster_folds, make_stratified_folds,
)
from .estimators import estimate
from .influence import InferenceResult, cluster_variance, iid_variance
from .models import OLSRidge


def validate_dataset(raw: dict[str, Any]) -> Dataset:
    """Validate a JSON-ish payload into a numeric ``Dataset``.

    Fails with ``InputError`` distinguishing shape, type and positivity
    problems so the API can map them to a single error taxonomy.
    """
    for key in ("x", "a", "y"):
        if key not in raw:
            raise InputError(f"missing required field {key!r}")
    try:
        x = np.asarray(raw["x"], dtype=float)
        a = np.asarray(raw["a"], dtype=float)
        y = np.asarray(raw["y"], dtype=float)
    except (TypeError, ValueError) as exc:
        raise InputError(f"inputs must be numeric arrays: {exc}") from exc

    if x.ndim != 2:
        raise InputError("x must be a 2-D matrix", details={"ndim": x.ndim})
    n, p = x.shape
    if n < 10:
        raise InputError("need at least 10 rows for cross-fitted estimation",
                         details={"n": int(n)})
    if p < 1:
        raise InputError("x must contain at least one covariate")
    if a.shape != (n,) or y.shape != (n,):
        raise InputError("x, a and y must share the same row count",
                         details={"x_rows": n, "a_rows": a.shape,
                                  "y_rows": y.shape})
    if not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise InputError("x and y must contain finite values only")
    if np.any(~np.isin(a, [0.0, 1.0])):
        raise InputError("treatment a must contain only 0 and 1")
    if np.sum(a == 1) < 2 or np.sum(a == 0) < 2:
        raise InputError("need at least 2 treated and 2 control units")

    clusters = None
    if raw.get("cluster_id") is not None:
        clusters = np.asarray(raw["cluster_id"])
        if clusters.shape != (n,):
            raise InputError("cluster_id must have one entry per row")
        if np.unique(clusters).size < 2:
            raise InputError("need at least 2 distinct clusters")

    return Dataset(x=x, a=a, y=y, clusters=clusters)


def _insample_gcomp(dataset: Dataset, config: Config) -> float:
    """Full-sample g-computation comparator (intentionally in-sample).

    This is NEVER reported as the answer. It exists so diagnostics and tests
    can demonstrate that in-sample fitting overfits relative to the OOF path —
    a leakage sentinel.
    """
    treated = dataset.a == 1
    cfg = config.outcome_models
    m0 = OLSRidge(intercept=cfg.intercept, standardize=cfg.standardize).fit(
        dataset.x[~treated], dataset.y[~treated])
    m1 = OLSRidge(intercept=cfg.intercept, standardize=cfg.standardize).fit(
        dataset.x[treated], dataset.y[treated])
    return float(np.mean(m1.predict(dataset.x) - m0.predict(dataset.x)))


def assign_folds(dataset: Dataset, config: Config,
                 rng: np.random.Generator) -> np.ndarray:
    """Choose the fold mechanism implied by the contract.

    Cluster-robust inference (``cluster.enabled`` with cluster ids) requires
    CLUSTER-LEVEL folds so a validation row's cluster-mates never appear in
    its training set. Otherwise use treatment-stratified row-level folds.
    """
    if config.cluster.enabled and dataset.clusters is not None:
        return make_cluster_folds(dataset, config.folds, rng)
    return make_stratified_folds(dataset, config.folds, rng)


def run_estimate(dataset: Dataset, config: Config, *,
                 seed: int = 0, run_id: str | None = None,
                 fold_id: np.ndarray | None = None) -> EstimateResult:
    """Execute one estimation run deterministically from ``seed``."""
    rng = np.random.default_rng(seed)
    if config.cluster.enabled and dataset.clusters is None:
        raise InputError(
            "cluster inference requested but dataset has no cluster_id column")
    if fold_id is None:
        fold_id = assign_folds(dataset, config, rng)
    elif fold_id.shape != (dataset.n,):
        raise InputError("supplied fold_id is not row-aligned with the data")

    oof: OOFPredictions = cross_fit(dataset, config, fold_id)
    comp = estimate(
        dataset.a, dataset.y, oof.propensity, oof.mu0, oof.mu1,
        config.trim_propensity, config.estimand, config.stabilize_weight,
    )

    if config.cluster.enabled and dataset.clusters is not None:
        inf: InferenceResult = cluster_variance(comp.scores, comp.point,
                                                dataset.clusters)
    else:
        inf = iid_variance(comp.scores, comp.point)

    return EstimateResult(
        run_id=run_id or f"run-{uuid.uuid4()}",
        estimand=config.estimand.value,
        point=inf.point,
        se=inf.se,
        variance=inf.variance,
        ci_lower=inf.ci_lower,
        ci_upper=inf.ci_upper,
        independent_units=inf.independent_units,
        clustered=inf.clustered,
        method="augmented_hajek" if config.stabilize_weight else "aipw_ht",
        folds=config.folds,
        n=dataset.n,
        gcomp_point=comp.gcomp,
        ipw_point=comp.ipw,
        aipw_point=comp.point,
        trimmed_fraction=comp.trimmed_fraction,
        stabilized=config.stabilize_weight,
        fold_diagnostics=oof.diagnostics,
        insample_gcomp_point=_insample_gcomp(dataset, config),
        config=config.to_dict(),
    )


def double_robust_condition() -> str:
    return DOUBLE_ROBUST_CONDITION
