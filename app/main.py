"""FastAPI application: HTTP surface, orchestration, logging, provenance."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.config import Settings, load_settings
from app.errors import DomainError, ErrorCategory
from app.provenance import ProvenanceStore
from app.pwm import build_pwm
from app.scan import scan_records
from app.schemas import HitOut, ScanRequest, ScanResponse, ScanSummary, ThresholdInfo
from app.sequence import parse_sequences
from app.significance import enumerate_score_distribution, pvalue_at_least, threshold_for_pvalue
from app.version import ALGORITHM_VERSION, APP_VERSION

logger = logging.getLogger("motifscan")
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def _log_step(request_id: str, step: str, **fields) -> None:
    payload = " ".join(f"{k}={v}" for k, v in fields.items())
    logger.info("request_id=%s step=%s %s", request_id, step, payload)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    store = ProvenanceStore(settings.db_path)
    app = FastAPI(title="motifscan", version=APP_VERSION)
    app.state.settings = settings
    app.state.store = store

    def config_snapshot() -> dict:
        return {
            "background": settings.background,
            "pseudocount": settings.pseudocount,
            "unknown_policy": settings.unknown_policy,
            "score_round_decimals": settings.score_round_decimals,
            "max_motif_length": settings.max_motif_length,
        }

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        _log_step(request_id or "-", "request_failed", category=exc.category.value, reason=exc.message)
        status_code = 404 if exc.category is ErrorCategory.SCAN_NOT_FOUND else 422
        return JSONResponse(
            status_code=status_code,
            content={"request_id": request_id, "error": exc.to_dict()},
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Normalise schema-level failures into the same error envelope.
        request_id = getattr(request.state, "request_id", None)
        first = exc.errors()[0] if exc.errors() else {}
        _log_step(request_id or "-", "request_failed", category="INVALID_REQUEST",
                  reason=".".join(str(p) for p in first.get("loc", [])))
        return JSONResponse(
            status_code=422,
            content={
                "request_id": request_id,
                "error": {
                    "category": "INVALID_REQUEST",
                    "message": "request body failed schema validation",
                    "detail": {"errors": jsonable_encoder(exc.errors())},
                },
            },
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)
        logger.exception("request_id=%s step=internal_error", request_id)
        return JSONResponse(
            status_code=500,
            content={
                "request_id": request_id,
                "error": {
                    "category": "INTERNAL",
                    "message": "unexpected internal error; see server logs for this request_id",
                    "detail": {},
                },
            },
        )

    @app.middleware("http")
    async def attach_request_id(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        _log_step(request.state.request_id, "request_received", method=request.method, path=request.url.path)
        return await call_next(request)

    @app.get("/v1/health")
    def health() -> dict:
        return {"status": "ok", "app_version": APP_VERSION, "algorithm_version": ALGORITHM_VERSION}

    @app.get("/v1/config")
    def get_config() -> dict:
        return {
            "app_version": APP_VERSION,
            "algorithm_version": ALGORITHM_VERSION,
            **config_snapshot(),
            "default_pvalue_threshold": settings.default_pvalue_threshold,
        }

    @app.post("/v1/scan", response_model=ScanResponse)
    def scan(req: ScanRequest, request: Request) -> ScanResponse:
        request_id = request.state.request_id
        cfg = config_snapshot()
        params = req.model_dump()
        canonical = json.dumps(
            {"params": params, "config": cfg, "algorithm_version": ALGORITHM_VERSION},
            sort_keys=True,
        )
        request_hash = hashlib.sha256(canonical.encode()).hexdigest()

        try:
            # 1. Parse synthetic sequences.
            records = parse_sequences(req.sequences)
            _log_step(request_id, "sequences_parsed", n_records=len(records),
                      total_bases=sum(r.length for r in records))

            # 2. Build PWM against the declared background.
            background = req.background if req.background is not None else settings.background
            pseudocount = req.pseudocount if req.pseudocount is not None else settings.pseudocount
            pwm = build_pwm(req.motif_counts, background, pseudocount, settings.max_motif_length)
            _log_step(request_id, "pwm_built", motif_length=pwm.length, pseudocount=pseudocount)

            # 3. Exact score distribution under the declared background.
            dist = enumerate_score_distribution(pwm, settings.score_round_decimals)
            _log_step(request_id, "distribution_enumerated", n_words=dist.n_words,
                      min_score=round(dist.min_score, 6), max_score=round(dist.max_score, 6))

            # 4. Resolve the threshold against that same distribution.
            warnings: list[dict] = []
            if req.score_threshold is not None and req.pvalue_threshold is not None:
                raise DomainError(
                    ErrorCategory.INVALID_THRESHOLD,
                    "provide either score_threshold or pvalue_threshold, not both",
                )
            if req.score_threshold is not None:
                threshold = req.score_threshold
                reported_threshold = threshold
                mode = "score"
                achieved = pvalue_at_least(dist, threshold)
                requested_p = None
            else:
                requested_p = (
                    req.pvalue_threshold
                    if req.pvalue_threshold is not None
                    else settings.default_pvalue_threshold
                )
                mode = "pvalue" if req.pvalue_threshold is not None else "default_pvalue"
                resolved = threshold_for_pvalue(dist, requested_p)
                if resolved is None:
                    warnings.append(
                        {
                            "category": "THRESHOLD_UNREACHABLE",
                            "message": (
                                f"no word reaches pvalue <= {requested_p} under the declared "
                                "background; the scan will report zero hits"
                            ),
                        }
                    )
                    threshold = float("inf")
                    reported_threshold = None
                    achieved = None
                else:
                    threshold = resolved
                    reported_threshold = resolved
                    achieved = pvalue_at_least(dist, threshold)
            _log_step(request_id, "threshold_resolved", mode=mode,
                      score_threshold=threshold, achieved_pvalue=achieved)

            # 5. Scan both strands; calibrate and correct for multiple testing.
            policy = req.unknown_policy or settings.unknown_policy
            outcome = scan_records(pwm, dist, records, threshold, policy)
            warnings.extend(outcome.warnings)
            _log_step(request_id, "scan_completed", evaluated=outcome.evaluated_windows,
                      skipped=outcome.skipped_windows, n_hits=len(outcome.hits))

            hits = [HitOut(**h.__dict__) for h in outcome.hits]
            summary = ScanSummary(
                n_sequences=len(records),
                motif_length=pwm.length,
                evaluated_windows=outcome.evaluated_windows,
                skipped_windows=outcome.skipped_windows,
                n_hits=len(hits),
                enumerated_words=dist.n_words,
                warnings=warnings,
            )
            response = ScanResponse(
                request_id=request_id,
                status="ok",
                app_version=APP_VERSION,
                algorithm_version=ALGORITHM_VERSION,
                request_hash=request_hash,
                config=cfg,
                threshold=ThresholdInfo(
                    mode=mode,
                    score_threshold=reported_threshold,
                    requested_pvalue_threshold=requested_p,
                    achieved_pvalue=achieved,
                ),
                summary=summary,
                hits=hits,
            )
            store.save_scan(
                request_id=request_id,
                app_version=APP_VERSION,
                algorithm_version=ALGORITHM_VERSION,
                request_hash=request_hash,
                status="ok",
                params=params,
                config=cfg,
                summary={**summary.model_dump(), "threshold": response.threshold.model_dump()},
                hits=[h.model_dump() for h in hits],
            )
            _log_step(request_id, "scan_persisted", status="ok")
            return response

        except DomainError as exc:
            # Persist the failure too, so the request_id remains explainable.
            store.save_scan(
                request_id=request_id,
                app_version=APP_VERSION,
                algorithm_version=ALGORITHM_VERSION,
                request_hash=request_hash,
                status="failed",
                params=params,
                config=cfg,
                summary={"error": exc.to_dict()},
                hits=[],
            )
            _log_step(request_id, "scan_persisted", status="failed", category=exc.category.value)
            raise

    @app.get("/v1/scan/{request_id}")
    def get_scan(request_id: str) -> dict:
        record = store.get_scan(request_id)
        if record is None:
            raise DomainError(
                ErrorCategory.SCAN_NOT_FOUND,
                f"no scan stored for request_id '{request_id}'",
                {"request_id": request_id},
            )
        return record

    return app


app = create_app()
