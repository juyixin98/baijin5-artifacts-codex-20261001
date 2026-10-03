"""HTTP routes: fold, lineage retrieval, health."""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from .. import ALGORITHM_NAME, ALGORITHM_VERSION, MODEL_SCOPE
from ..domain.rules import ALLOWED_PAIR_TUPLES, MIN_LOOP_LENGTH, PSEUDOKNOTS_SUPPORTED
from ..domain.service import fold
from ..errors import (
    InvalidBaseError,
    InvalidParameterError,
    NussinovError,
    ResultNotFoundError,
    SequenceTooLongError,
)
from ..parser import parse_sequence
from ..logging_setup import get_logger
from ..trace import LineageRecorder, get_request, new_request_id
from .schemas import FoldRequestBody

logger = get_logger("api.routes")


def create_router() -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health(request: Request) -> dict[str, str]:
        return {
            "status": "ok",
            "algorithm": ALGORITHM_NAME,
            "version": ALGORITHM_VERSION,
            "model_scope": MODEL_SCOPE,
            "db_path": str(request.app.state.settings.db_path),
        }

    @router.post("/api/v1/fold")
    def fold_endpoint(request: Request, body: FoldRequestBody):
        request_id = getattr(request.state, "request_id", None) or new_request_id()
        settings = request.app.state.settings
        db = request.app.state.db

        recorder = LineageRecorder(db=db, request_id=request_id, source="http")
        recorder.raw_sequence = body.sequence
        recorder.alternatives_requested = body.enumerate_alternatives
        recorder.alternatives_limit = body.alternatives_limit

        try:
            parsed = parse_sequence(
                body.sequence, max_length=settings.max_sequence_length, source="http"
            )
            recorder.step(
                "parse_sequence",
                "ok",
                f"normalized length={parsed.length} source={parsed.source} "
                f"base_conversions={len(parsed.normalized_bases)}",
            )
        except NussinovError as exc:
            recorder.step("parse_sequence", "failed", str(exc))
            recorder.fail(exc.category, str(exc))
            logger.warning("fold rejected at parsing: %s: %s", exc.category, exc)
            return _error_response(request_id, exc, status_code=400)

        recorder.sequence = parsed.sequence
        recorder.sequence_length = parsed.length
        recorder.fasta_header = parsed.fasta_header

        limit = body.alternatives_limit
        if limit is None:
            limit = min(10, settings.max_structures_limit)
        elif limit > settings.max_structures_limit:
            exc = InvalidParameterError(
                f"alternatives_limit {limit} exceeds server maximum "
                f"{settings.max_structures_limit}",
                parameter="alternatives_limit",
                value=limit,
            )
            recorder.step("validate_parameters", "failed", str(exc))
            recorder.fail(exc.category, str(exc))
            logger.warning("fold rejected at parameter validation: %s", exc)
            return _error_response(request_id, exc, status_code=400)

        recorder.step(
            "validate_parameters",
            "ok",
            f"min_loop_length={MIN_LOOP_LENGTH} pseudoknots_supported=False "
            f"enumerate_alternatives={body.enumerate_alternatives} "
            f"alternatives_limit={limit}",
        )

        try:
            recorder.step(
                "fill_dp_table",
                "ok",
                f"numPy vectorized fill of {parsed.length}x{parsed.length} table",
            )
            result = fold(
                parsed.sequence,
                enumerate_alternatives=body.enumerate_alternatives,
                alternatives_limit=limit,
                min_loop_length=MIN_LOOP_LENGTH,
            )
            recorder.step(
                "independent_optimum_check",
                "ok",
                f"scalar plain-Python recurrence confirms optimum={result.optimum}",
            )
            recorder.step(
                "traceback",
                "ok",
                "deterministic rule: keep i unpaired if optimal, else leftmost admissible partner; "
                f"recovered {result.primary.pair_count} pairs",
            )
            if result.self_check.valid:
                recorder.step(
                    "legality_check",
                    "ok",
                    "canonical pairs, minimum loop length respected, no crossing pairs",
                )
            else:
                recorder.step(
                    "legality_check",
                    "failed",
                    "; ".join(v.message for v in result.self_check.violations),
                )
            if body.enumerate_alternatives:
                total = 1 + len(result.alternatives)
                recorder.step(
                    "enumerate_alternatives",
                    "ok" if not result.alternatives_truncated else "warning",
                    f"returned {total} optimal structure(s)"
                    + (" (truncated at limit)" if result.alternatives_truncated else ""),
                )
            else:
                recorder.step("enumerate_alternatives", "skipped", "not requested")

            recorder.step("persist_lineage", "ok", f"sqlite db={db.path}")
            recorder.succeed(result)
        except NussinovError as exc:
            recorder.step("fold_pipeline", "failed", f"{exc.category}: {exc}")
            recorder.fail(exc.category, str(exc))
            logger.error("fold pipeline failed: %s: %s", exc.category, exc)
            return _error_response(request_id, exc, status_code=422)

        record = get_request(db, request_id)
        logger.info(
            "fold succeeded: length=%d optimum=%d alternatives=%d truncated=%s",
            len(result.sequence),
            result.optimum,
            len(result.alternatives),
            result.alternatives_truncated,
        )
        return _success_response(record, result, limit)

    @router.get("/api/v1/lineage/{request_id}")
    def lineage_endpoint(request: Request, request_id: str):
        try:
            record = get_request(request.app.state.db, request_id)
        except ResultNotFoundError as exc:
            return _error_response(request_id, exc, status_code=404)
        return {"success": True, "request_id": request_id, "record": record}

    return router


def _structure_out(rank: int, is_primary: bool, structure, sequence: str) -> dict:
    return {
        "rank": rank,
        "is_primary": is_primary,
        "pair_count": structure.pair_count,
        "dot_bracket": structure.dot_bracket,
        "pairs": [
            {
                "position_5prime": i + 1,
                "position_3prime": j + 1,
                "base_5prime": sequence[i],
                "base_3prime": sequence[j],
            }
            for i, j in structure.pairs
        ],
        "pair_table": list(structure.pair_table),
    }


def _success_response(record: dict, result, limit: int) -> dict:
    sequence = result.sequence
    structures = [_structure_out(1, True, result.primary, sequence)]
    alternatives = [
        _structure_out(rank, False, struct, sequence)
        for rank, struct in enumerate(result.alternatives, start=2)
    ]
    return {
        "success": True,
        "request_id": record["request_id"],
        "sequence": sequence,
        "sequence_length": len(sequence),
        "optimum": result.optimum,
        "min_loop_length": result.min_loop_length,
        "pseudoknots_supported": PSEUDOKNOTS_SUPPORTED,
        "allowed_pairs": [f"{a}-{b}" for a, b in ALLOWED_PAIR_TUPLES],
        "structure": structures[0],
        "alternatives": alternatives,
        "alternatives_truncated": result.alternatives_truncated,
        "legal_structure": result.self_check.valid,
        "legality_violations": [v.message for v in result.self_check.violations],
        "uncertainties": list(result.warnings),
        "processing_steps": [
            {
                "order": s["step_order"],
                "name": s["step_name"],
                "outcome": s["outcome"],
                "detail": s["detail"],
                "at": s["created_at"],
            }
            for s in record["steps"]
        ],
        "provenance": {
            "request_id": record["request_id"],
            "algorithm_name": record["algorithm_name"],
            "algorithm_version": record["algorithm_version"],
            "model_scope": record["model_scope"],
            "processing_location": record["processing_location"],
            "created_at": record["created_at"],
            "finished_at": record["finished_at"],
            "duration_ms": record["duration_ms"],
            "source": record["source"],
            "stored": True,
        },
        "error": None,
    }


def _error_response(request_id: str, exc: NussinovError, *, status_code: int) -> JSONResponse:
    details: dict[str, object] = {}
    if isinstance(exc, InvalidBaseError):
        details = {"position": exc.position, "base": exc.base}
    elif isinstance(exc, SequenceTooLongError):
        details = {"length": exc.length, "max_length": exc.max_length}
    elif isinstance(exc, InvalidParameterError):
        details = {"parameter": exc.parameter, "value": exc.value}
    body = {
        "success": False,
        "request_id": request_id,
        "error": {"category": exc.category, "message": str(exc), "details": details},
    }
    return JSONResponse(status_code=status_code, content=body)
