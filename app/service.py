"""Scan/calibrate orchestration: parse -> model -> calibrate -> scan ->
correct -> persist. Each step is logged with the request identity so the
processing trail of any result can be reconstructed from the logs and the
provenance store.
"""
from __future__ import annotations

import hashlib
import json
import logging
import platform

import numpy as np

from . import __version__
from .calibration import (
    ScoreDistribution,
    enumerate_score_distribution,
    validate_alpha,
)
from .config import (
    DEFAULT_BACKGROUND,
    DEFAULT_PSEUDOCOUNT,
    BackgroundModel,
)
from .correction import benjamini_hochberg, bonferroni
from .errors import (
    DuplicateSequenceIdError,
    InvalidPseudocountError,
    MotifScanError,
    UnknownBasePolicyError,
)
from .provenance import ProvenanceStore
from .pwm import PWM
from .scanning import VALID_UNKNOWN_BASE_POLICIES, WindowResult, scan_sequence
from .schemas import (
    CalibrateRequest,
    CalibrateResponse,
    DistributionEntry,
    Hit,
    ScanRequest,
    ScanResponse,
    SequenceInput,
    SkippedWindow,
    ThresholdInfo,
    ValidateRequest,
    ValidateResponse,
    ValidationIssue,
)
from .sequence import ParsedSequence, parse_sequence


def versions() -> dict[str, str]:
    return {
        "app": __version__,
        "python": platform.python_version(),
        "numpy": np.__version__,
    }


def sha256_json(obj) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _resolve_background(raw: dict[str, float] | None) -> BackgroundModel:
    return BackgroundModel(dict(raw) if raw is not None else dict(DEFAULT_BACKGROUND))


def _build_pwm(req, background: BackgroundModel) -> PWM:
    pseudocount = req.pseudocount if req.pseudocount is not None else DEFAULT_PSEUDOCOUNT
    return PWM(
        matrix=req.motif.matrix,
        background=background,
        pseudocount=pseudocount,
        name=req.motif.name,
    )


def _resolved_config(req, background: BackgroundModel, pwm: PWM) -> dict:
    return {
        "motif_name": pwm.name,
        "motif_length": pwm.length,
        "motif_matrix": req.motif.matrix,
        "background": background.probs,
        "pseudocount": pwm.pseudocount,
        **({"alpha": req.alpha} if hasattr(req, "alpha") else {}),
        **(
            {"unknown_base_policy": req.unknown_base_policy}
            if hasattr(req, "unknown_base_policy")
            else {}
        ),
    }


def _parse_sequences(seq_inputs: list[SequenceInput]) -> list[ParsedSequence]:
    seen: set[str] = set()
    parsed = []
    for s in seq_inputs:
        if s.id in seen:
            raise DuplicateSequenceIdError(
                f"duplicate sequence id {s.id!r}; hit identity is keyed on "
                "sequence ids and they must be unique within a request",
                {"seq_id": s.id},
            )
        seen.add(s.id)
        parsed.append(parse_sequence(s.id, s.sequence))
    return parsed


def _persist_failure(
    store: ProvenanceStore,
    endpoint: str,
    request_id: str,
    config: dict,
    input_sha256: str,
    exc: MotifScanError,
    log: logging.LoggerAdapter,
) -> None:
    log.warning("step=failed category=%s message=%s", exc.category, exc.message)
    store.record(
        request_id=request_id,
        endpoint=endpoint,
        app_version=__version__,
        status="failed",
        config=config,
        input_sha256=input_sha256,
        summary={"error_category": exc.category},
        error=exc.to_dict(),
    )


def run_scan(
    req: ScanRequest,
    store: ProvenanceStore,
    request_id: str,
    log: logging.LoggerAdapter,
) -> ScanResponse:
    input_sha = sha256_json(req.model_dump())
    config: dict = {}
    try:
        parsed = _parse_sequences(req.sequences)
        log.info("step=parse_sequences n=%d", len(parsed))

        background = _resolve_background(req.background)
        pwm = _build_pwm(req, background)
        validate_alpha(req.alpha)
        config = _resolved_config(req, background, pwm)
        log.info(
            "step=build_model motif=%s length=%d pseudocount=%s background=%s",
            pwm.name, pwm.length, pwm.pseudocount, background.probs,
        )

        dist = enumerate_score_distribution(pwm, background)
        threshold = dist.threshold_for_alpha(req.alpha)
        log.info(
            "step=calibrate distinct_scores=%d achievable=%s threshold=%s",
            dist.n_distinct, threshold.achievable, threshold.score,
        )

        windows: list[WindowResult] = []
        for p in parsed:
            windows.extend(scan_sequence(p.id, p.sequence, pwm, req.unknown_base_policy))
        scored = [w for w in windows if w.status == "scored"]
        skipped = [w for w in windows if w.status == "skipped"]
        log.info(
            "step=scan windows=%d scored=%d skipped=%d",
            len(windows), len(scored), len(skipped),
        )

        pvalues = [dist.pvalue(w.score) for w in scored]  # type: ignore[arg-type]
        adj_bonf = bonferroni(pvalues)
        adj_bh = benjamini_hochberg(pvalues)
        log.info("step=correct family_size=%d methods=bonferroni,bh", len(scored))

        hits: list[Hit] = []
        for i, (w, p, pb, ph) in enumerate(zip(scored, pvalues, adj_bonf, adj_bh)):
            if p > req.alpha:
                continue
            hits.append(
                Hit(
                    hit_id=f"h{i + 1:04d}",
                    seq_id=w.seq_id,
                    start=w.start,
                    end=w.end,
                    strand=w.strand,
                    matched_sequence=w.matched_sequence,
                    score=w.score,  # type: ignore[arg-type]
                    pvalue=p,
                    pvalue_bonferroni=pb,
                    pvalue_bh=ph,
                    significant_raw=True,
                    significant_adjusted=ph <= req.alpha,
                )
            )
        uncertain = [h.hit_id for h in hits if not h.significant_adjusted]

        caveats = [
            "A statistically significant hit means the score is unlikely "
            "under the declared background model; it is not evidence of "
            "biological function.",
            f"Multiple-testing family size = {len(scored)} scored windows "
            "(both strands); skipped windows are excluded from correction.",
        ]
        if not threshold.achievable:
            caveats.append(
                f"No score in the null distribution reaches alpha={req.alpha}; "
                "the achievable tail probability is 0 at any threshold."
            )
        if skipped:
            caveats.append(
                f"{len(skipped)} windows were skipped (unknown base, policy="
                f"'{req.unknown_base_policy}') and contribute no p-values."
            )

        response = ScanResponse(
            request_id=request_id,
            app_version=__version__,
            versions=versions(),
            resolved_config=config,
            input_sha256=input_sha,
            threshold=ThresholdInfo(**threshold.__dict__),
            n_windows_total=len(windows),
            n_scored_windows=len(scored),
            n_skipped_windows=len(skipped),
            hits=hits,
            uncertain_hit_ids=uncertain,
            skipped_windows=[
                SkippedWindow(
                    seq_id=w.seq_id, start=w.start, end=w.end,
                    strand=w.strand, reason=w.reason or "unknown",
                )
                for w in skipped
            ],
            caveats=caveats,
        )
        store.record(
            request_id=request_id,
            endpoint="/v1/scan",
            app_version=__version__,
            status="completed",
            config=config,
            input_sha256=input_sha,
            summary={
                "n_hits": len(hits),
                "n_uncertain": len(uncertain),
                "n_scored_windows": len(scored),
                "n_skipped_windows": len(skipped),
            },
            result=response.model_dump(),
        )
        log.info("step=done hits=%d uncertain=%d", len(hits), len(uncertain))
        return response
    except MotifScanError as exc:
        _persist_failure(store, "/v1/scan", request_id, config, input_sha, exc, log)
        raise


def run_calibrate(
    req: CalibrateRequest,
    store: ProvenanceStore,
    request_id: str,
    log: logging.LoggerAdapter,
) -> CalibrateResponse:
    input_sha = sha256_json(req.model_dump())
    config: dict = {}
    try:
        background = _resolve_background(req.background)
        pwm = _build_pwm(req, background)
        validate_alpha(req.alpha)
        config = _resolved_config(req, background, pwm)
        log.info(
            "step=build_model motif=%s length=%d pseudocount=%s",
            pwm.name, pwm.length, pwm.pseudocount,
        )

        dist: ScoreDistribution = enumerate_score_distribution(pwm, background)
        threshold = dist.threshold_for_alpha(req.alpha)
        log.info(
            "step=calibrate distinct_scores=%d achievable=%s",
            dist.n_distinct, threshold.achievable,
        )

        response = CalibrateResponse(
            request_id=request_id,
            app_version=__version__,
            resolved_config=config,
            input_sha256=input_sha,
            motif_length=dist.motif_length,
            n_distinct_scores=dist.n_distinct,
            min_score=dist.min_score,
            max_score=dist.max_score,
            threshold=ThresholdInfo(**threshold.__dict__),
            distribution=(
                [
                    DistributionEntry(score=s, tail_probability=t)
                    for s, t in zip(dist.scores, dist.tail_probs)
                ]
                if req.include_distribution
                else None
            ),
        )
        store.record(
            request_id=request_id,
            endpoint="/v1/calibrate",
            app_version=__version__,
            status="completed",
            config=config,
            input_sha256=input_sha,
            summary={
                "motif_length": dist.motif_length,
                "n_distinct_scores": dist.n_distinct,
                "achievable": threshold.achievable,
            },
            result=response.model_dump(),
        )
        log.info("step=done")
        return response
    except MotifScanError as exc:
        _persist_failure(store, "/v1/calibrate", request_id, config, input_sha, exc, log)
        raise


def run_validate(req: ValidateRequest, request_id: str) -> ValidateResponse:
    """Dry-run validation: collects ALL declared failures, persists nothing."""
    errors: list[ValidationIssue] = []

    def check(fn) -> None:
        try:
            fn()
        except MotifScanError as exc:
            errors.append(ValidationIssue(**exc.to_dict()))

    background: BackgroundModel | None = None

    def _bg():
        nonlocal background
        background = _resolve_background(req.background)

    check(_bg)

    if req.pseudocount is not None and req.pseudocount <= 0:
        exc = InvalidPseudocountError(
            f"pseudocount must be positive, got {req.pseudocount!r}",
            {"pseudocount": req.pseudocount},
        )
        errors.append(ValidationIssue(**exc.to_dict()))

    if req.motif is not None and background is not None:
        check(lambda: _build_pwm(req, background))

    if req.alpha is not None:
        check(lambda: validate_alpha(req.alpha))

    if req.unknown_base_policy is not None:
        def _policy():
            if req.unknown_base_policy not in VALID_UNKNOWN_BASE_POLICIES:
                raise UnknownBasePolicyError(
                    f"unknown_base_policy must be one of "
                    f"{VALID_UNKNOWN_BASE_POLICIES}",
                    {"policy": req.unknown_base_policy},
                )

        check(_policy)

    if req.sequences is not None:
        check(lambda: _parse_sequences(req.sequences))

    return ValidateResponse(request_id=request_id, valid=not errors, errors=errors)
