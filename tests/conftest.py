"""Test fixtures, independent reference implementation and run logging.

The reference estimators here are deliberately written *differently* from the
production kernel: inference goes through QR decomposition and explicit
scalar formulas, so a match validates the kernel rather than echoing it.

Each pytest invocation writes one JSONL log under ``logs/tests`` recording
versions, per-test steps (via the ``case_log`` fixture), inputs/run identity
and the final verdict with the failure category.
"""
from __future__ import annotations

import datetime as dt
import json
import platform
import sys
from pathlib import Path

import numpy as np
import pytest
import scipy

from app.core.contracts import (
    MissingPolicy,
    ZeroVariancePolicy,
)
from app.core.data import prepare_data

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs" / "tests"


# --------------------------------------------------------------------------- #
# Independent reference implementation (NOT importing the production formulas)
# --------------------------------------------------------------------------- #
def ref_ols(y: np.ndarray, x: np.ndarray) -> dict:
    """OLS via QR decomposition with classical and HC1 covariance."""
    q, r = np.linalg.qr(x)
    beta = np.linalg.solve(r, q.T @ y)
    resid = y - x @ beta
    n, p = x.shape
    dof = n - p
    rss = float(resid @ resid)
    sigma2 = rss / dof
    r_inv = np.linalg.inv(r)
    xtx_inv = r_inv @ r_inv.T
    cov_classical = sigma2 * xtx_inv
    meat = x.T @ (x * (resid[:, None] ** 2))
    cov_hc0 = xtx_inv @ meat @ xtx_inv
    return {
        "beta": beta,
        "sigma2": sigma2,
        "se_classical": np.sqrt(np.diag(cov_classical)),
        "se_hc0": np.sqrt(np.diag(cov_hc0)),
        "se_hc1": np.sqrt(np.diag(cov_hc0 * (n / dof))),
        "resid": resid,
    }


def ref_welch(y1: np.ndarray, y0: np.ndarray) -> dict:
    """Scalar Welch difference-of-means straight from the textbook formula."""
    n1, n0 = len(y1), len(y0)
    m1, m0 = y1.sum() / n1, y0.sum() / n0
    v1 = ((y1 - m1) ** 2).sum() / (n1 - 1)
    v0 = ((y0 - m0) ** 2).sum() / (n0 - 1)
    se2 = v1 / n1 + v0 / n0
    df = se2 ** 2 / ((v1 / n1) ** 2 / (n1 - 1) + (v0 / n0) ** 2 / (n0 - 1))
    return {"diff": m1 - m0, "se": np.sqrt(se2), "df": df,
            "var1": v1, "var0": v0, "mean1": m1, "mean0": m0}


def ref_simple_slope(x: np.ndarray, y: np.ndarray) -> tuple[float, float]:
    """Intercept/slope of simple OLS via raw sums of squares (control arm)."""
    n = len(x)
    xbar, ybar = x.sum() / n, y.sum() / n
    sxx = ((x - xbar) ** 2).sum()
    sxy = ((x - xbar) * (y - ybar)).sum()
    slope = sxy / sxx
    intercept = ybar - slope * xbar
    return intercept, slope


def ref_cuped(y: np.ndarray, t: np.ndarray, x_mat: np.ndarray,
              theta: np.ndarray) -> dict:
    """Adjusted outcome difference and Welch SE using raw formulas."""
    xbar = x_mat.mean(axis=0)
    y_adj = y - (x_mat - xbar) @ np.asarray(theta)
    return ref_welch(y_adj[t == 1], y_adj[t == 0])


def ref_lin_tau(y: np.ndarray, t: np.ndarray, x_mat: np.ndarray,
                interactions: bool) -> dict:
    xc = x_mat - x_mat.mean(axis=0)
    cols = [np.ones(len(y)), t, xc]
    if interactions:
        cols.append(t[:, None] * xc)
    design = np.column_stack(cols)
    fit = ref_ols(y, design)
    return {"tau": float(fit["beta"][1]),
            "se_hc1": float(fit["se_hc1"][1]),
            "se_classical": float(fit["se_classical"][1])}


# --------------------------------------------------------------------------- #
# Datasets
# --------------------------------------------------------------------------- #
def hand_dataset() -> dict:
    """Tiny hand-specified dataset with analytically verifiable quantities.

    y = 1 + 2*T + 3*X + eps with a fixed residual vector; treatment arm is
    X=5..8 and control X=1..4 (strong baseline imbalance by construction).
    """
    x = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]
    t = [0, 0, 0, 0, 1, 1, 1, 1]
    eps = [0.10, -0.10, 0.05, -0.05, -0.20, 0.20, -0.15, 0.15]
    y = [1.0 + 2.0 * ti + 3.0 * xi + e for xi, ti, e in zip(x, t, eps)]
    return {
        "name": "hand_dataset",
        "outcome_column": "y",
        "treatment_column": "t",
        "covariates": ["x"],
        "pre_treatment_covariates": {"x": True},
        "data": {"y": y, "t": t, "x": x},
    }


def prepare(payload, **overrides):
    defaults = dict(
        missing_policy=MissingPolicy.FAIL,
        zero_variance_policy=ZeroVariancePolicy.DROP,
    )
    defaults.update(overrides)
    return prepare_data(
        columns=payload["data"],
        outcome_column=payload["outcome_column"],
        treatment_column=payload["treatment_column"],
        requested_covariates=payload["covariates"],
        **defaults,
    )


@pytest.fixture
def prepared_hand():
    return prepare(hand_dataset())


@pytest.fixture
def sample_payload(request):
    path = ROOT / "data" / "sample" / f"{request.param}.json"
    return json.loads(path.read_text())


# --------------------------------------------------------------------------- #
# Structured, run-correlated test logging (one JSONL file per pytest run)
# --------------------------------------------------------------------------- #
def pytest_configure(config):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = LOG_DIR / f"run-{stamp}.jsonl"
    fh = path.open("a", encoding="utf-8")

    def emit(**fields):
        fields["logged_at"] = dt.datetime.now(dt.timezone.utc).isoformat(
            timespec="milliseconds")
        fh.write(json.dumps(fields, ensure_ascii=False, default=str) + "\n")
        fh.flush()

    config._test_log_emit = emit
    config._test_log_path = path
    emit(event="session_start", python=platform.python_version(),
         numpy=np.__version__, scipy=scipy.__version__,
         pytest=pytest.__version__, executable=sys.executable,
         argv=sys.argv)


def pytest_report_header(config):
    return f"test run log: {getattr(config, '_test_log_path', 'n/a')}"


@pytest.fixture
def case_log(request):
    """Log computational steps of one test, correlated with its node id."""
    emit = request.config._test_log_emit
    steps: list[dict] = []

    def step(message: str, **fields):
        rec = {"step": message, **fields}
        steps.append(rec)
        emit(event="test_step", nodeid=request.node.nodeid, **rec)

    request.node._case_steps = steps
    return step


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    report = (yield).get_result()
    emit = getattr(item.config, "_test_log_emit", None)
    if emit is not None and report.when == "call":
        if report.passed:
            verdict, category = "PASS", None
        elif report.failed:
            verdict, category = "FAIL", "assertion_or_error"
        else:
            verdict, category = "SKIP", report.longreprtext[:200]
        emit(event="test_verdict", nodeid=item.nodeid, verdict=verdict,
             failure_category=category,
             duration_s=round(report.duration, 4),
             steps=getattr(item, "_case_steps", []))
