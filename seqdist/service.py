"""Orchestration: parse -> classify -> correct -> bootstrap -> record.

This module owns the run lifecycle and emits the structured diagnostics
(run id, intermediate state, verdict rationale) that tests and operators use
to replay a computation.
"""
from __future__ import annotations

import hashlib
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np

from . import __version__
from .bootstrap import BootstrapResult, bootstrap_ci
from .distance import DistanceEstimate, SiteStats, classify_sites, estimate_from_stats
from .errors import ResourceExhaustedError
from .models import get_model
from .parsing import parse_pair
from .provenance import RunRecord, record_run

logger = logging.getLogger("seqdist.service")

MAX_SEQUENCE_LENGTH = 1_000_000
MAX_BOOTSTRAP_REPLICATES = 100_000


def _check_limits(length: int, n_replicates: int) -> None:
    if length > MAX_SEQUENCE_LENGTH:
        raise ResourceExhaustedError(
            "alignment length exceeds the configured limit",
            detail={"length": length, "limit": MAX_SEQUENCE_LENGTH},
        )
    if n_replicates > MAX_BOOTSTRAP_REPLICATES:
        raise ResourceExhaustedError(
            "bootstrap replicate count exceeds the configured limit",
            detail={"n_replicates": n_replicates, "limit": MAX_BOOTSTRAP_REPLICATES},
        )


def _intermediates(stats: SiteStats, est: DistanceEstimate, boot: BootstrapResult) -> dict[str, Any]:
    return {
        "n_valid": stats.n_valid,
        "n_match": stats.n_match,
        "n_transition": stats.n_transition,
        "n_transversion": stats.n_transversion,
        "n_excluded_gap": stats.n_excluded_gap,
        "n_excluded_ambiguous": stats.n_excluded_ambiguous,
        "p": stats.p,
        "transition_fraction": stats.transition_fraction,
        "transversion_fraction": stats.transversion_fraction,
        "estimate_status": est.status.value,
        "estimate_reason": est.reason,
        "bootstrap_n_ok": boot.n_ok,
        "bootstrap_n_saturated": boot.n_saturated,
        "bootstrap_status": boot.status.value,
    }


def run_distance(
    conn: sqlite3.Connection,
    *,
    seq1: str,
    seq2: str,
    model: str,
    n_replicates: int = 1000,
    alpha: float = 0.05,
    seed: int = 0,
) -> dict[str, Any]:
    """Compute a distance run end to end and record its provenance."""
    spec = get_model(model)  # validates model name first (input error)
    _check_limits(max(len(seq1), len(seq2)), n_replicates)
    pair = parse_pair(seq1, seq2)

    run_id = uuid.uuid4().hex
    log = logging.LoggerAdapter(logger, {"run_id": run_id})

    stats = classify_sites(pair)
    est = estimate_from_stats(stats, model)
    boot = bootstrap_ci(
        np.asarray(stats.site_codes, dtype=np.int64),
        model,
        n_replicates=n_replicates,
        alpha=alpha,
        seed=seed,
    )
    intermediates = _intermediates(stats, est, boot)
    log.info(
        "distance run computed: model=%s status=%s p=%.6f n_valid=%d "
        "excluded(gap=%d, ambiguous=%d) bootstrap(ok=%d, saturated=%d)",
        model,
        est.status.value,
        stats.p,
        stats.n_valid,
        stats.n_excluded_gap,
        stats.n_excluded_ambiguous,
        boot.n_ok,
        boot.n_saturated,
    )

    input_sha256 = hashlib.sha256(
        f"{pair.seq1}\n{pair.seq2}".encode("utf-8")
    ).hexdigest()
    result: dict[str, Any] = {
        "run_id": run_id,
        "model": model,
        "model_assumptions": list(spec.assumptions),
        "model_valid_domain": spec.valid_domain,
        "distance": est.value,
        "status": est.status.value,
        "reason": est.reason,
        "confidence_interval": {
            "level": boot.confidence_level,
            "lower": boot.lower,
            "upper": boot.upper,
            "status": boot.status.value,
            "n_replicates": boot.n_replicates,
            "n_ok": boot.n_ok,
            "n_saturated": boot.n_saturated,
            "seed": boot.seed,
        },
        "sites": {
            "n_valid": stats.n_valid,
            "n_match": stats.n_match,
            "n_transition": stats.n_transition,
            "n_transversion": stats.n_transversion,
            "n_excluded_gap": stats.n_excluded_gap,
            "n_excluded_ambiguous": stats.n_excluded_ambiguous,
        },
        "seqdist_version": __version__,
    }
    record = RunRecord(
        run_id=run_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        model=model,
        seed=seed,
        n_replicates=n_replicates,
        alpha=alpha,
        input_sha256=input_sha256,
        status=est.status.value,
        result=result,
        intermediates=intermediates,
    )
    record_run(conn, record)
    log.info("run recorded: input_sha256=%s status=%s", input_sha256, est.status.value)
    return result
