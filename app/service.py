"""Pipeline orchestration: parse -> validate -> build -> measure -> persist.

This module owns the data/error contract between the boundaries:
- parsing.py     produces (labels, matrix) or InputValidationError
- validation.py  enforces matrix preconditions or Input/Resource errors
- nj.py          produces NJResult or ComputationFailedError
- residuals.py   produces the ResidualReport (never raises on valid trees)
- store.py       persists provenance; idempotency conflicts surface as
                 StateConflictError

Nothing here swallows an exception: expected NJServiceError subclasses
propagate to the API layer with their category intact; anything unexpected
is wrapped as COMPUTATION_FAILED so the error taxonomy stays closed. Every
failure is also persisted as an ``error`` run record (except idempotency
conflicts, which must not touch the original record) so the run id in the
error response can be audited later.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from typing import Any

from . import runlog
from .errors import (
    ComputationFailedError,
    InputValidationError,
    NJServiceError,
    StateConflictError,
)
from .models import NJEvent, TreeRequest, TreeResponse
from .newick import serialize_newick
from .nj import neighbor_joining
from .parsing import p_distance_matrix, parse_fasta
from .residuals import compute_residuals
from .store import RunStore
from .validation import validate_distance_matrix


def _new_run_id() -> str:
    return uuid.uuid4().hex[:16]


def _input_hash(request: TreeRequest) -> str:
    canonical = json.dumps(
        request.model_dump(mode="json", exclude={"request_id"}),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _resolve_matrix(request: TreeRequest) -> tuple[list[str], list[list[float]]]:
    if request.input_format == "fasta":
        if request.sequences is None:
            raise InputValidationError(
                "input_format is 'fasta' but no sequences were provided"
            )
        records = parse_fasta(request.sequences)
        return p_distance_matrix(records)
    if request.matrix is None:
        raise InputValidationError(
            "input_format is 'matrix' but no matrix was provided"
        )
    return request.matrix.labels, request.matrix.distances


def _replay_response(stored: dict[str, Any]) -> TreeResponse:
    return TreeResponse(
        run_id=stored["run_id"],
        replayed=True,
        newick=stored["newick"],
        leaf_map=stored["leaf_map"],
        residuals=stored["residuals"],
        events=[NJEvent(**e) for e in stored["events"]],
    )


def _persist_failure(
    store: RunStore,
    request: TreeRequest,
    run_id: str,
    input_hash: str,
    exc: NJServiceError,
) -> None:
    """Best-effort failure record; never masks the original error."""
    if isinstance(exc, StateConflictError):
        return  # must not touch the conflicting original record
    try:
        store.save_run(
            {
                "run_id": run_id,
                "request_id": request.request_id,
                "input_hash": input_hash,
                "params": request.options.model_dump(mode="json"),
                "status": "error",
                "error": exc.to_dict(),
            }
        )
    except sqlite3.IntegrityError:
        runlog.log_step(
            run_id,
            "failure_record_skipped",
            "an error record with this request_id already exists",
        )


def build_tree(request: TreeRequest, store: RunStore) -> TreeResponse:
    run_id = _new_run_id()
    input_hash = _input_hash(request)
    runlog.log_step(
        run_id,
        "request_received",
        "build request accepted for processing",
        {
            "input_format": request.input_format,
            "negative_branch_mode": request.options.negative_branch_mode,
            "max_taxa": request.options.max_taxa,
            "input_hash": input_hash,
        },
    )

    try:
        return _build_tree_inner(request, store, run_id, input_hash)
    except NJServiceError as exc:
        exc.run_id = run_id  # surfaced in the API error body
        runlog.log_step(
            run_id,
            "run_failed",
            exc.message,
            {"category": exc.category.value, "details": exc.details},
        )
        _persist_failure(store, request, run_id, input_hash, exc)
        raise


def _build_tree_inner(
    request: TreeRequest, store: RunStore, run_id: str, input_hash: str
) -> TreeResponse:
    labels, matrix = _resolve_matrix(request)
    matrix = validate_distance_matrix(
        labels, matrix, max_taxa=request.options.max_taxa
    )
    runlog.log_step(
        run_id,
        "input_validated",
        "matrix passed symmetry / zero-diagonal / non-negativity checks",
        {"n_taxa": len(labels)},
    )

    if request.request_id is not None:
        stored = store.find_by_request_id(request.request_id)
        if stored is not None:
            if stored["input_hash"] == input_hash and stored["status"] == "ok":
                runlog.log_step(
                    run_id,
                    "idempotent_replay",
                    "request_id already completed with an identical payload; "
                    "returning the stored result",
                    {"original_run_id": stored["run_id"]},
                )
                return _replay_response(stored)
            raise StateConflictError(
                "request_id was already used with a different payload",
                {
                    "request_id": request.request_id,
                    "original_run_id": stored["run_id"],
                    "original_status": stored["status"],
                },
            )

    try:
        result = neighbor_joining(
            labels, matrix, negative_branch_mode=request.options.negative_branch_mode
        )
    except NJServiceError:
        raise
    except Exception as exc:  # keep the error taxonomy closed
        raise ComputationFailedError(
            "unexpected failure inside the neighbor-joining computation",
            {"exception": type(exc).__name__},
        ) from exc

    for event in result.events:
        runlog.log_step(run_id, event["type"], event["message"], event["data"])

    newick, leaf_map = serialize_newick(result)
    residuals = compute_residuals(labels, matrix, result)
    runlog.log_step(
        run_id,
        "residuals_computed",
        "fit of emitted tree against observed distances",
        {
            "sum_abs": residuals.sum_abs,
            "max_abs": residuals.max_abs,
            "rms": residuals.rms,
        },
    )

    response = TreeResponse(
        run_id=run_id,
        newick=newick,
        leaf_map=leaf_map,
        residuals=residuals,
        events=[NJEvent(**e) for e in result.events],
    )
    store.save_run(
        {
            "run_id": run_id,
            "request_id": request.request_id,
            "input_hash": input_hash,
            "params": request.options.model_dump(mode="json"),
            "status": "ok",
            "newick": newick,
            "leaf_map": leaf_map,
            "residuals": residuals.model_dump(mode="json"),
            "events": result.events,
        }
    )
    runlog.log_step(run_id, "run_persisted", "provenance record stored")
    return response
