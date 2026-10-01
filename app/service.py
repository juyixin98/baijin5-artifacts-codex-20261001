"""Analysis orchestration: validation, budget decision, evidence, persistence.

This is the layer the HTTP API calls. It keeps the statistical contract
honest end to end:

* paired data validation (:mod:`app.core.contract`)
* exact enumeration while ``2**n`` is within budget, otherwise Monte-Carlo
  with an explicitly reported error band
* independent oracle cross-check for small enough exact analyses
* request-correlated logging and SQLite persistence
"""
from __future__ import annotations

import math
import uuid

import numpy as np

from app.config import settings
from app.core import contract, kernel
from app.core.intervals import IntervalSet
from app.diagnostics import StepLogger
from app import evidence
from app.storage import get_database

# Beyond this many pairs the plain-Python oracle cross-check is too slow to do
# inline; the kernel is still exact, the independent check runs in the test
# suite on smaller cases.
ORACLE_INLINE_MAX_PAIRS = 10


def new_request_id() -> str:
    return uuid.uuid4().hex


def _require_object(payload: object, endpoint: str) -> dict:
    if not isinstance(payload, dict):
        raise contract.ContractError(
            "MALFORMED_DATA",
            f"request body to {endpoint} must be a JSON object",
        )
    return payload


def _optional_int(payload: dict, key: str, default: int | None = None
                  ) -> int | None:
    value = payload.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise contract.ContractError(
            "INVALID_INTEGER", f"'{key}' must be an integer", {key: value}
        )
    return value


def _start_request(step_log: StepLogger, request_id: str, endpoint: str,
                   d: np.ndarray, payload: dict,
                   alpha: float | None) -> None:
    get_database().record_request(
        request_id=request_id,
        n_pairs=int(d.size),
        endpoint=endpoint,
        payload=payload,
        alpha=alpha,
    )
    step_log.info(
        "validate",
        "request validated",
        "app.service:_start_request",
        n_pairs=int(d.size),
        n_assignments=contract.n_assignments(int(d.size)),
        mean_difference=float(d.mean()),
    )


def budget_decision(n_pairs: int) -> dict:
    """Decide exact vs Monte-Carlo from the paired randomization-set size."""
    total = contract.n_assignments(n_pairs)
    within_budget = total <= settings.exact_budget_flips
    return {
        "n_assignments": total,
        "exact_budget_flips": settings.exact_budget_flips,
        "within_budget": within_budget,
        "method": "exact-signflip" if within_budget
        else "monte-carlo-signflip",
    }


def _envelope(request_id: str, endpoint: str, d: np.ndarray,
              step_log: StepLogger, result_body: dict) -> dict:
    return {
        "request_id": request_id,
        "endpoint": endpoint,
        "version": settings.version,
        "statistical_contract": {
            "design": "paired: independent within-pair sign flips only",
            "randomization_set_size": contract.n_assignments(int(d.size)),
            "statistic": contract.TWO_SIDED_DEFINITION,
        },
        "result": result_body,
        "diagnostics": {
            "processing_steps": step_log.trail(),
            "log_location": settings.log_path,
        },
    }


# ---------------------------------------------------------------------------
# P-value test
# ---------------------------------------------------------------------------

def analyze_pvalue(payload: dict, request_id: str) -> dict:
    logger = StepLogger(request_id)
    payload = _require_object(payload, "pvalue")
    d = contract.validate_pairs(payload.get("pairs"),
                                payload.get("differences"))
    effect = contract.validate_effect(payload.get("effect", 0.0))
    _start_request(logger, request_id, "pvalue", d, payload, None)

    decision = budget_decision(d.size)
    logger.info(
        "budget",
        "combinatorial budget evaluated",
        "app.service:analyze_pvalue",
        **decision,
    )

    if decision["within_budget"]:
        p_result = kernel.exact_pvalue(d, effect=effect)
        logger.info(
            "exact_test",
            "enumerated every paired assignment",
            "app.service:analyze_pvalue",
            p_value=p_result.p_value,
            count_as_extreme=p_result.count_as_extreme,
        )
        cross = _maybe_cross_check(d, effect, p_result.p_value, logger)
        body = p_result.to_dict()
        body["independent_cross_check"] = cross
        failures: list[dict] = []
        budget_exceeded = False
        mc_halfwidth = None
    else:
        n_draws = _optional_int(payload, "n_draws",
                                settings.mc_default_draws)
        seed = _optional_int(payload, "seed")
        p_result = kernel.mc_pvalue(d, effect=effect, n_draws=n_draws,
                                   seed=seed,
                                   error_confidence=settings.mc_error_confidence)
        logger.uncertainty(
            "monte_carlo_test",
            "exact enumeration over budget; used Monte-Carlo",
            "app.service:analyze_pvalue",
            n_draws=n_draws,
            mc_error_halfwidth=p_result.mc_error_halfwidth,
        )
        body = p_result.to_dict()
        body["independent_cross_check"] = {
            "ok": None,
            "skipped": "Monte-Carlo result; use /replay for reproducibility",
        }
        failures = []
        budget_exceeded = True
        mc_halfwidth = p_result.mc_error_halfwidth

    summary = evidence.build_diagnostic_summary(
        d, method=body["method"], steps=logger.trail(), failures=failures,
        mc_error_halfwidth=mc_halfwidth, budget_exceeded=budget_exceeded,
        request_id=request_id,
    )
    body["summary"] = summary.to_dict()

    get_database().record_analysis(
        request_id, kind="pvalue", method=body["method"], result=body,
        effect=effect,
    )
    get_database().update_request_status(request_id, "completed")
    logger.info("persist", "result stored", "app.service:analyze_pvalue")
    return _envelope(request_id, "pvalue", d, logger, body)


def _maybe_cross_check(d: np.ndarray, effect: float, core_p: float,
                       logger: StepLogger) -> dict:
    if d.size > ORACLE_INLINE_MAX_PAIRS:
        logger.info(
            "cross_check",
            "independent oracle skipped inline (n too large); covered by tests",
            "app.service:_maybe_cross_check",
            n_pairs=int(d.size),
        )
        return {"ok": None, "skipped": "oracle enumeration too large inline"}
    report = evidence.cross_check_pvalue(d, effect=effect,
                                         core_p_value=core_p)
    logger.info(
        "cross_check",
        "independent itertools oracle compared against core",
        "app.service:_maybe_cross_check",
        ok=report.ok,
        discrepancies=report.discrepancies,
    )
    return report.to_dict()


def _maybe_cross_check_inversion(d: np.ndarray, alpha: float,
                                 interval_set, logger: StepLogger) -> dict:
    """Pointwise membership check of the exact set against the oracle."""
    if d.size > ORACLE_INLINE_MAX_PAIRS:
        return {"ok": None, "skipped": "oracle enumeration too large inline"}
    finite = [x for iv in interval_set.intervals
              for x in (iv.lower, iv.upper) if math.isfinite(x)]
    scale = max(1.0, float(np.abs(d).max()))
    half_width = max(4.0 * scale,
                     2.0 * max((abs(x) for x in finite), default=0.0) + 2.0)
    center = float(d.mean())
    probes = list(np.linspace(center - half_width, center + half_width, 201))
    # A generic grid point can land within floating-point epsilon of a root,
    # where the oracle's >= comparison tolerance dominates; probe each
    # boundary explicitly instead, with a probe offset far above epsilon.
    offset = 1e-6 * max(1.0, half_width)
    endpoints = sorted(set(finite))
    probes = [t for t in probes
              if all(abs(t - x) > offset for x in endpoints)]
    for x in endpoints:
        probes.extend([x - offset, x, x + offset])
    probe_grid = np.asarray(sorted(set(probes)), dtype=float)
    discrepancies = evidence.cross_check_interval_set(
        d, alpha=alpha, interval_set=interval_set, probe_grid=probe_grid,
    )
    report = {
        "ok": not discrepancies,
        "check": "membership on 201-point independent-oracle probe grid",
        "probe_range": [float(probe_grid[0]), float(probe_grid[-1])],
        "discrepancies": discrepancies,
    }
    logger.info(
        "cross_check_inversion",
        "exact acceptance set compared pointwise with independent oracle",
        "app.service:_maybe_cross_check_inversion",
        ok=report["ok"],
        n_discrepancies=len(discrepancies),
    )
    return report


# ---------------------------------------------------------------------------
# Confidence-set inversion
# ---------------------------------------------------------------------------

def _default_grid(d: np.ndarray, payload: dict) -> np.ndarray:
    raw_width = payload.get("grid_half_width", settings.grid_half_width)
    raw_points = payload.get("grid_points", settings.grid_points)
    if isinstance(raw_width, bool) or not isinstance(raw_width, (int, float)):
        raise contract.ContractError(
            "INVALID_GRID", "'grid_half_width' must be a number",
            {"grid_half_width": raw_width},
        )
    half_width = float(raw_width)
    if half_width <= 0 or not math.isfinite(half_width):
        raise contract.ContractError(
            "INVALID_GRID",
            "'grid_half_width' must be a finite positive number",
            {"grid_half_width": half_width},
        )
    if isinstance(raw_points, bool) or not isinstance(raw_points, int):
        raise contract.ContractError(
            "INVALID_GRID", "'grid_points' must be an integer",
            {"grid_points": raw_points},
        )
    points = raw_points
    if points < 3 or points % 2 == 0:
        raise contract.ContractError(
            "INVALID_GRID",
            "'grid_points' must be an odd integer >= 3",
            {"grid_points": points},
        )
    center = float(d.mean())
    return np.linspace(center - half_width, center + half_width, points)


def analyze_inversion(payload: dict, request_id: str) -> dict:
    logger = StepLogger(request_id)
    payload = _require_object(payload, "inversion")
    d = contract.validate_pairs(payload.get("pairs"),
                                payload.get("differences"))
    alpha = contract.validate_alpha(payload.get("alpha", 0.05))
    _start_request(logger, request_id, "inversion", d, payload, alpha)

    decision = budget_decision(d.size)
    logger.info(
        "budget",
        "combinatorial budget evaluated",
        "app.service:analyze_inversion",
        **decision,
    )

    if decision["within_budget"]:
        interval_set = kernel.invert_confidence_set_exact(d, alpha=alpha)
        p_profile = kernel.exact_pvalue_profile(
            d, np.array([float(d.mean())], dtype=float)
        )
        logger.info(
            "exact_inversion",
            "acceptance set recovered by breakpoint sweep",
            "app.service:analyze_inversion",
            n_components=interval_set.n_intervals,
            is_connected=interval_set.is_connected,
        )
        cross = _maybe_cross_check_inversion(
            d, alpha, interval_set, logger
        )
        body = {
            "method": "exact-signflip",
            "alpha": alpha,
            "confidence_level": 1 - alpha,
            "confidence_set": interval_set.to_dict(),
            "p_value_at_mean": float(p_profile[0]),
            "budget": decision,
            "independent_cross_check": cross,
        }
        grid_used = False
        mc_halfwidth = None
    else:
        grid = _default_grid(d, payload)
        n_draws = _optional_int(payload, "n_draws",
                                settings.mc_default_draws)
        seed = _optional_int(payload, "seed")
        interval_set, p_values, mc_halfwidth = (
            kernel.mc_invert_confidence_set(
                d, alpha=alpha, n_draws=n_draws, grid=grid, seed=seed,
                error_confidence=settings.mc_error_confidence,
            )
        )
        logger.uncertainty(
            "monte_carlo_inversion",
            "exact inversion over budget; grid Monte-Carlo used",
            "app.service:analyze_inversion",
            n_draws=n_draws,
            grid_points=int(grid.size),
            mc_error_halfwidth=mc_halfwidth,
        )
        body = {
            "method": "monte-carlo-signflip",
            "alpha": alpha,
            "confidence_level": 1 - alpha,
            "confidence_set": interval_set.to_dict(),
            "grid": {
                "lower": float(grid[0]),
                "upper": float(grid[-1]),
                "points": int(grid.size),
            },
            "mc_error_halfwidth": mc_halfwidth,
            "budget": decision,
        }
        grid_used = True
        if _touches_grid_edge(interval_set, grid):
            body["grid_truncated"] = True
            logger.uncertainty(
                "grid_edge",
                "acceptance reaches the evaluated grid edge; widen the grid",
                "app.service:analyze_inversion",
            )

    summary = evidence.build_diagnostic_summary(
        d, method=body["method"], steps=logger.trail(),
        interval_set=interval_set, mc_error_halfwidth=mc_halfwidth,
        grid_used=grid_used,
        budget_exceeded=not decision["within_budget"],
        request_id=request_id,
    )
    if body.get("grid_truncated"):
        summary.uncertainties.append({
            "code": "GRID_TRUNCATION",
            "message": "acceptance set touches the grid boundary; the true "
                       "set may extend beyond the evaluated range",
        })
    body["summary"] = summary.to_dict()

    get_database().record_analysis(
        request_id, kind="inversion", method=body["method"], result=body,
    )
    get_database().update_request_status(request_id, "completed")
    logger.info("persist", "result stored", "app.service:analyze_inversion")
    return _envelope(request_id, "inversion", d, logger, body)


def _touches_grid_edge(interval_set: IntervalSet, grid: np.ndarray) -> bool:
    for iv in interval_set.intervals:
        if math.isclose(iv.lower, float(grid[0]), abs_tol=1e-12):
            return True
        if math.isclose(iv.upper, float(grid[-1]), abs_tol=1e-12):
            return True
    return False


# ---------------------------------------------------------------------------
# Deterministic replay
# ---------------------------------------------------------------------------

def analyze_replay(payload: dict, request_id: str) -> dict:
    """Run a Monte-Carlo analysis twice with the same seed and compare."""
    from app.repro import replay as replay_mod

    logger = StepLogger(request_id)
    payload = _require_object(payload, "replay")
    fixture_name = payload.get("fixture")
    if fixture_name is not None:
        from app.repro.fixtures import get_fixture

        d = np.asarray(get_fixture(str(fixture_name)).differences, dtype=float)
    else:
        d = contract.validate_pairs(payload.get("pairs"),
                                    payload.get("differences"))
    n_draws = _optional_int(payload, "n_draws", settings.mc_default_draws)
    if n_draws < 1:
        raise contract.ContractError(
            "INVALID_N_DRAWS", "'n_draws' must be a positive integer",
            {"n_draws": n_draws},
        )
    seed = _optional_int(payload, "seed", 0)
    effect = (None if payload.get("effect") is None
              else contract.validate_effect(payload.get("effect")))
    alpha = contract.validate_alpha(payload.get("alpha", 0.05))

    _start_request(logger, request_id, "replay", d, payload, alpha)

    grid: list[float] | None = None
    if payload.get("kind", "pvalue") == "inversion":
        grid_array = _default_grid(d, payload)
        grid = [float(x) for x in grid_array]

    logger.info(
        "replay_run",
        "running Monte-Carlo twice with identical seed",
        "app.service:analyze_replay",
        n_draws=n_draws,
        seed=seed,
    )
    result = replay_mod.run_and_replay(
        d.tolist(), n_draws=n_draws, seed=seed, effect=effect,
        alpha=alpha, grid=grid,
    )
    body = result.to_dict()
    if result.reproducible:
        logger.info(
            "replay_match",
            "replay reproduced the original outputs exactly",
            "app.service:analyze_replay",
            max_abs_difference=result.max_abs_difference,
            fingerprint=result.bundle.get("fingerprint"),
        )
    else:
        logger.failure(
            "replay_mismatch",
            "replay did not reproduce original outputs",
            "app.service:analyze_replay",
            code="REPLAY_MISMATCH",
            discrepancies=result.discrepancies,
        )

    failures = ([] if result.reproducible else [{
        "code": "REPLAY_MISMATCH",
        "message": "seeded replay returned different outputs",
        "discrepancies": result.discrepancies,
    }])
    summary = evidence.build_diagnostic_summary(
        d, method="monte-carlo-signflip", steps=logger.trail(),
        failures=failures, mc_error_halfwidth=None,
        budget_exceeded=True, request_id=request_id,
    )
    body["summary"] = summary.to_dict()

    get_database().record_analysis(
        request_id, kind="replay", method="monte-carlo-signflip",
        result=body,
    )
    get_database().update_request_status(
        request_id, "completed" if result.reproducible else "failed"
    )
    return _envelope(request_id, "replay", d, logger, body)
