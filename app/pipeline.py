"""Analysis pipeline: orchestrates contract -> bandwidth -> fits -> inference.

Status semantics (never collapse a failure into success):

* ``ok``            - both side fits and inference completed;
* ``unidentified``  - valid request, but support/order conditions fail
                       (too few points on a side, a bandwidth that cannot
                       reach data, a singular design); estimates absent;
* ``error``         - invalid input or an internal numerical failure.
"""
from __future__ import annotations

import traceback

import numpy as np

from app import logging_setup as log
from app.bandwidths import select_bandwidth
from app.config import Settings
from app.contract import (
    BandwidthReport,
    BootstrapReport,
    Diagnostic,
    ErrorCode,
    InferenceMethod,
    RDEstimate,
    RDRequest,
    RDResponse,
    RunStatus,
    Severity,
    SideFit,
)
from app.diagnostics import (
    density_diagnostic,
    discreteness_diagnostics,
    effective_sample,
    identification_range,
    support_diagnostics,
)
from app.errors import (
    BandwidthFailedError,
    InsufficientDataError,
    InvalidInputError,
    RDPipelineError,
    SingularFitError,
)
from app.estimator import LEFT, RIGHT, fit_side, jump
from app.inference import BIAS_NOTE, robust_inference, wild_bootstrap


def _arrays(req: RDRequest) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray([p.x for p in req.data], dtype=float)
    y = np.asarray([p.y for p in req.data], dtype=float)
    return x, y


def _validate(req: RDRequest, x: np.ndarray, y: np.ndarray) -> None:
    if x.shape != y.shape or x.size < 6:
        raise InvalidInputError(
            "need aligned x/y with at least 6 observations",
            {"n": int(x.size)},
        )
    if not (np.all(np.isfinite(x)) and np.all(np.isfinite(y))):
        raise InvalidInputError("all x and y must be finite")
    if np.sum(x < req.cutoff) < 1 or np.sum(x > req.cutoff) < 1:
        raise InvalidInputError(
            "need at least one observation strictly on each side of the cutoff",
            {"n_below": int(np.sum(x < req.cutoff)),
             "n_above": int(np.sum(x > req.cutoff))},
        )
    if req.bandwidth_method.value == "manual" and req.bandwidth is None:
        raise InvalidInputError("manual bandwidth method requires bandwidth > 0")
    if req.cluster_var is not None and len(req.cluster_var) != x.size:
        raise InvalidInputError(
            "cluster_var length must equal number of observations",
            {"cluster_len": len(req.cluster_var), "n": int(x.size)},
        )


def _side_fit_payload(res) -> SideFit:
    return SideFit(
        intercept=res.intercept,
        slope=res.slope,
        bandwidth=res.bandwidth,
        n_in_window=res.n,
        effective_n=res.effective_n,
        condition_number=res.condition_number,
        x_at_cutoff_probe=res.max_distance,
    )


def _diag(payload: dict) -> Diagnostic:
    return Diagnostic(
        code=payload["code"],
        severity=payload["severity"],
        message=payload["message"],
        details=payload["details"],
    )


def run_analysis(req: RDRequest, settings: Settings) -> RDResponse:
    run_id = req.run_id or log.new_run_id()
    ctx = log.bind_context(run_id, req.input_label, settings)
    log.run_start(req.input_label, len(req.data), req.cutoff, req.bandwidth_method.value)

    def base(status: RunStatus, **kw) -> RDResponse:
        return RDResponse(
            run_id=run_id,
            input_label=req.input_label,
            status=status,
            cutoff=req.cutoff,
            kernel=req.kernel,
            inference=req.inference,
            alpha=req.alpha,
            versions=dict(ctx.versions),
            **kw,
        )

    try:
        x, y = _arrays(req)
        _validate(req, x, y)
        clusters = (
            None
            if req.cluster_var is None
            else np.asarray(req.cluster_var, dtype=object)
        )
        at_cutoff_side = RIGHT if req.treatment_above else LEFT

        log.step("bandwidth", "selecting bandwidth", method=req.bandwidth_method.value)
        bw = select_bandwidth(
            x, y, req.cutoff, req.bandwidth_method, req.bandwidth,
            req.bandwidth_multiplier, settings.min_obs_per_side, req.kernel,
        )
        log.step("bandwidth", "bandwidth selected", h_left=bw.left, h_right=bw.right)

        diagnostics: list[Diagnostic] = [
            _diag(d) for d in discreteness_diagnostics(x, req.cutoff)
        ]
        diagnostics.append(_diag(density_diagnostic(x, req.cutoff, req.alpha)))

        try:
            log.step("fit", "fitting left side", bandwidth=bw.left)
            left = fit_side(
                x, y, req.cutoff, bw.left, req.kernel, LEFT,
                at_cutoff_side, clusters,
            )
            log.step("fit", "fitting right side", bandwidth=bw.right)
            right = fit_side(
                x, y, req.cutoff, bw.right, req.kernel, RIGHT,
                at_cutoff_side, clusters,
            )
        except SingularFitError as exc:
            log.failure("singular_fit", "fit", str(exc), **exc.details)
            return base(
                RunStatus.UNIDENTIFIED,
                bandwidth=BandwidthReport(
                    method=bw.method, left=bw.left, right=bw.right,
                    multiplier_applied=req.bandwidth_multiplier, notes=bw.notes,
                ),
                diagnostics=diagnostics,
                error_code=ErrorCode.SINGULAR_FIT,
                error_message=str(exc),
            )

        diagnostics.extend(
            _diag(d) for d in support_diagnostics(
                left, right, settings.min_obs_per_side
            )
        )
        diagnostics.append(_diag(identification_range(left, right)))
        diagnostics.append(_diag(effective_sample(left, right)))

        # Identification gate: a starved side cannot support the estimate.
        critical = [
            d for d in diagnostics if d.severity is Severity.CRITICAL
        ]
        if left.n < settings.min_obs_per_side or right.n < settings.min_obs_per_side:
            log.warn(
                "unidentified", "gate",
                "fewer than minimum observations within window",
                n_left=left.n, n_right=right.n,
                min_obs=settings.min_obs_per_side,
            )
            return base(
                RunStatus.UNIDENTIFIED,
                bandwidth=BandwidthReport(
                    method=bw.method, left=bw.left, right=bw.right,
                    multiplier_applied=req.bandwidth_multiplier, notes=bw.notes,
                ),
                left_fit=_side_fit_payload(left),
                right_fit=_side_fit_payload(right),
                diagnostics=diagnostics,
                error_code=ErrorCode.INSUFFICIENT_DATA,
                error_message=(
                    f"fewer than {settings.min_obs_per_side} effective "
                    "observations on a side within the bandwidth"
                ),
            )

        tau = jump(left, right, req.treatment_above)
        log.step("estimate", "jump computed", tau=tau)

        estimate_kw = {}
        bootstrap_payload = None
        if req.inference is InferenceMethod.NONE:
            log.step("inference", "inference disabled by caller")
        else:
            kind = "hc3" if req.inference is InferenceMethod.HC3 else "hc1"
            res = robust_inference(tau, left, right, kind, req.alpha)
            log.step(
                "inference", f"{kind.upper()} robust inference",
                se=res.se, ci=[res.ci_low, res.ci_high], p=res.p_value,
            )
            estimate_kw.update(
                se=res.se, ci_low=res.ci_low, ci_high=res.ci_high,
                z=res.z, p_value=res.p_value,
            )

        reps = (
            req.bootstrap_reps
            if req.bootstrap_reps is not None
            else settings.bootstrap_reps
        )
        if reps > 0 and req.inference is not InferenceMethod.NONE:
            log.step("bootstrap", "wild bootstrap", reps=reps)
            boot = wild_bootstrap(
                tau,
                estimate_kw.get("se"),
                left,
                right,
                reps,
                req.bootstrap_seed,
                left.cluster_labels,
                right.cluster_labels,
            )
            bootstrap_payload = BootstrapReport(
                reps=boot["reps"], seed=req.bootstrap_seed,
                ci_low=boot["ci_low"], ci_high=boot["ci_high"],
                p_value=boot["p_value"], clustered=boot["clustered"],
                note="Rademacher wild; percentile CI, restricted-null p",
            )
            log.step(
                "bootstrap", "wild bootstrap complete",
                ci=[boot["ci_low"], boot["ci_high"]], p=boot["p_value"],
            )

        estimate = RDEstimate(
            tau=tau,
            tau_left=left.intercept,
            tau_right=right.intercept,
            bias_notes=BIAS_NOTE,
            **estimate_kw,
        )
        log.step("done", "analysis complete", status=RunStatus.OK.value, critical=len(critical))
        return base(
            RunStatus.OK,
            bandwidth=BandwidthReport(
                method=bw.method, left=bw.left, right=bw.right,
                multiplier_applied=req.bandwidth_multiplier, notes=bw.notes,
            ),
            estimate=estimate,
            left_fit=_side_fit_payload(left),
            right_fit=_side_fit_payload(right),
            diagnostics=diagnostics,
            bootstrap=bootstrap_payload,
        )

    except InsufficientDataError as exc:
        log.failure("insufficient_data", "bandwidth", str(exc), **exc.details)
        return base(
            RunStatus.UNIDENTIFIED,
            error_code=ErrorCode.INSUFFICIENT_DATA,
            error_message=str(exc),
        )
    except BandwidthFailedError as exc:
        log.failure("bandwidth_failed", "bandwidth", str(exc), **exc.details)
        return base(
            RunStatus.UNIDENTIFIED,
            error_code=ErrorCode.BANDWIDTH_FAILED,
            error_message=str(exc),
        )
    except InvalidInputError as exc:
        log.failure("invalid_input", "validate", str(exc), **exc.details)
        return base(
            RunStatus.ERROR,
            error_code=ErrorCode.INVALID_INPUT,
            error_message=str(exc),
        )
    except RDPipelineError as exc:
        log.failure("pipeline_error", "run", str(exc), **exc.details)
        return base(
            RunStatus.ERROR,
            error_code=exc.error_code,
            error_message=str(exc),
        )
    except Exception as exc:  # noqa: BLE001 - last-resort explicit envelope
        log.failure(
            "internal_error", "run", f"{type(exc).__name__}: {exc}",
            traceback=traceback.format_exc(),
        )
        return base(
            RunStatus.ERROR,
            error_code=ErrorCode.INTERNAL_ERROR,
            error_message=f"{type(exc).__name__}: {exc}",
        )
