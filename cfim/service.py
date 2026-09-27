"""FastAPI service: corpora, resumable mining jobs, independent query.

Endpoints
---------
``POST /corpora``                         create a normalized corpus
``GET  /corpora`` / ``GET /corpora/{id}`` list / inspect
``POST /corpora/{id}/jobs``               start a mining job (first slice)
``GET  /jobs/{id}``                       job snapshot with results
``POST /jobs/{id}/advance``               spend more budget, resume mining
``POST /corpora/{id}/query``              independent support/closure check
``GET  /health``                          liveness + version

All responses carry the request id (header ``X-Request-ID``); all failures
share one envelope ``{"success": false, "error": {...}}`` with stable codes.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from cfim import __version__
from cfim.config import KERNEL_VERSION, Settings, load_settings
from cfim.corpus import normalize_corpus, support_threshold
from cfim.errors import ApiError, DomainError, ErrorCode
from cfim.kernel import (
    initial_state,
    advance as kernel_advance,
    validate_budget,
    budget_unit,
)
from cfim.models import (
    CorpusCreateRequest,
    CorpusResponse,
    JobAdvanceRequest,
    JobCreateRequest,
    JobResponse,
    ItemsetResponse,
    QueryRequest,
    QueryResponse,
)
from cfim.observability import (
    REQUEST_ID_HEADER,
    bind_request_id,
    configure_logging,
    current_request_id,
    log_event,
)
from cfim.store import Store


def _settings() -> Settings:
    return load_settings()


def create_app(settings: Settings | None = None, store: Store | None = None) -> FastAPI:
    """Application factory. Tests pass explicit settings/store; ``run.py``
    builds them from the environment."""
    settings = settings or _settings()
    logger = configure_logging(settings.log_level, KERNEL_VERSION)

    app = FastAPI(
        title="cfim - closed frequent itemset backend",
        version=__version__,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.state.store = store or Store(settings.db_path)
    app.state.logger = logger
    app.state.owns_store = store is None

    @app.middleware("http")
    async def correlation_middleware(request: Request, call_next):
        incoming = request.headers.get(REQUEST_ID_HEADER)
        rid = bind_request_id(incoming)
        log_event(
            logger,
            logging.INFO,
            step="request.received",
            message=f"{request.method} {request.url.path}",
            location="cfim.service:correlation_middleware",
            context={"incoming_request_id": incoming, "client": request.client.host if request.client else None},
        )
        try:
            response = await call_next(request)
        except Exception:
            log_event(
                logger,
                logging.ERROR,
                step="request.failed",
                message="unhandled exception while serving request",
                location="cfim.service:correlation_middleware",
                failure={"kind": "unhandled_exception"},
            )
            raise
        response.headers[REQUEST_ID_HEADER] = rid
        log_event(
            logger,
            logging.INFO,
            step="request.completed",
            message=f"{request.method} {request.url.path} -> {response.status_code}",
            location="cfim.service:correlation_middleware",
        )
        return response

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        status = 404 if exc.code in (ErrorCode.CORPUS_NOT_FOUND, ErrorCode.JOB_NOT_FOUND) else 400
        log_event(
            logger,
            logging.WARNING,
            step="request.domain_error",
            message=exc.message,
            location="cfim.service:exception_handler",
            failure={"code": exc.code.value, "details": exc.details},
        )
        return _error_response(status, exc.code, exc.message, exc.details)

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        log_event(
            logger,
            logging.WARNING,
            step="request.api_error",
            message=exc.message,
            location="cfim.service:exception_handler",
            failure={"code": exc.code.value, "details": exc.details},
        )
        return _error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = {"errors": exc.errors()}
        log_event(
            logger,
            logging.WARNING,
            step="request.validation_error",
            message="request payload failed schema validation",
            location="cfim.service:exception_handler",
            failure={"code": ErrorCode.VALIDATION_ERROR.value, "details": details},
        )
        return _error_response(422, ErrorCode.VALIDATION_ERROR, "request payload is invalid", details)

    # -- health ------------------------------------------------------------

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service_version": __version__,
            "kernel_version": KERNEL_VERSION,
            "budget_unit": budget_unit(),
            "request_id": current_request_id(),
        }

    # -- corpora -----------------------------------------------------------

    @app.post("/corpora", response_model=CorpusResponse, status_code=201)
    async def create_corpus(payload: CorpusCreateRequest) -> CorpusResponse:
        s: Settings = app.state.settings
        normalized = normalize_corpus(payload.name, payload.transactions, s)
        log_event(
            logger,
            logging.INFO,
            step="corpus.normalized",
            message=f"corpus {normalized.name!r} normalized",
            location="cfim.service:create_corpus",
            context={
                "transaction_count": len(normalized.transactions),
                "item_domain_size": len(normalized.item_domain),
                "empty_transactions": normalized.empty_transaction_count,
                "duplicate_item_occurrences": normalized.duplicate_item_occurrences,
            },
        )
        record = app.state.store.create_corpus(normalized)
        log_event(
            logger,
            logging.INFO,
            step="corpus.stored",
            message=f"corpus stored as {record.corpus_id}",
            location="cfim.service:create_corpus",
            context={"corpus_id": record.corpus_id},
        )
        return CorpusResponse(**record.__dict__)

    @app.get("/corpora", response_model=list[CorpusResponse])
    async def list_corpora() -> list[CorpusResponse]:
        return [CorpusResponse(**r.__dict__) for r in app.state.store.list_corpora()]

    @app.get("/corpora/{corpus_id}", response_model=CorpusResponse)
    async def get_corpus(corpus_id: str) -> CorpusResponse:
        record = app.state.store.get_corpus(corpus_id)
        return CorpusResponse(**record.__dict__)

    # -- jobs --------------------------------------------------------------

    def _run_slice(job_id: str | None, budget: int | None, *, create: bool,
                   corpus_id: str | None = None,
                   min_support: int | None = None) -> JobResponse:
        s: Settings = app.state.settings
        effective_budget = budget if budget is not None else s.default_budget
        validate_budget(effective_budget)
        if effective_budget > s.max_advance_budget:
            raise ApiError(
                400,
                ErrorCode.BUDGET_INVALID,
                f"budget {effective_budget} exceeds per-request maximum "
                f"{s.max_advance_budget}; advance in smaller slices",
                {"budget": effective_budget, "max": s.max_advance_budget},
            )

        if create:
            assert corpus_id is not None and min_support is not None
            record = app.state.store.get_corpus(corpus_id)
            threshold = support_threshold(min_support, record.transaction_count)
            db = app.state.store.load_vertical_database(corpus_id)
            state = initial_state(db, threshold)
            job_id = app.state.store.create_job(corpus_id, threshold, state)
            log_event(
                logger,
                logging.INFO,
                step="job.created",
                message=f"job {job_id} created",
                location="cfim.service:_run_slice",
                context={"job_id": job_id, "corpus_id": corpus_id, "min_support": threshold},
            )
        else:
            assert job_id is not None
            record, state = app.state.store.state_snapshot(job_id)

        log_event(
            logger,
            logging.INFO,
            step="job.slice_start",
            message="advancing mining kernel",
            location="cfim.service:_run_slice",
            context={
                "job_id": job_id,
                "budget": effective_budget,
                "nodes_visited_before": state.nodes_visited,
                "completed_before": state.completed,
            },
        )
        kernel_advance(state, effective_budget)
        app.state.store.save_job_state(job_id, state)  # type: ignore[arg-type]
        job = app.state.store.get_job(job_id)  # type: ignore[arg-type]
        log_event(
            logger,
            logging.INFO,
            step="job.slice_end",
            message=f"job slice finished; status={job.status}",
            location="cfim.service:_run_slice",
            context={
                "job_id": job_id,
                "nodes_visited": state.nodes_visited,
                "closed_count": len(state.results),
                "completed": state.completed,
            },
            uncertainty=None if state.completed else "maximal flags are provisional until status=COMPLETED",
        )
        return _job_response(job, state)

    @app.post("/corpora/{corpus_id}/jobs", response_model=JobResponse, status_code=201)
    async def create_job(corpus_id: str, payload: JobCreateRequest) -> JobResponse:
        return _run_slice(
            None,
            payload.budget,
            create=True,
            corpus_id=corpus_id,
            min_support=payload.min_support,
        )

    @app.post("/jobs/{job_id}/advance", response_model=JobResponse)
    async def advance_job(job_id: str, payload: JobAdvanceRequest) -> JobResponse:
        return _run_slice(job_id, payload.budget, create=False)

    @app.get("/jobs/{job_id}", response_model=JobResponse)
    async def get_job(job_id: str) -> JobResponse:
        record, state = app.state.store.state_snapshot(job_id)
        return _job_response(record, state)

    @app.get("/jobs", response_model=list[JobResponse])
    async def list_jobs(corpus_id: str | None = None) -> list[JobResponse]:
        responses: list[JobResponse] = []
        for record in app.state.store.list_jobs(corpus_id):
            _, state = app.state.store.state_snapshot(record.job_id)
            responses.append(_job_response(record, state))
        return responses

    # -- independent query -------------------------------------------------

    @app.post("/corpora/{corpus_id}/query", response_model=QueryResponse)
    async def query_itemset(corpus_id: str, payload: QueryRequest) -> QueryResponse:
        s: Settings = app.state.settings
        items = tuple(dict.fromkeys(payload.items))  # de-dup, preserve order
        if len(items) != len(payload.items):
            log_event(
                logger,
                logging.INFO,
                step="query.dedup",
                message="duplicate items in query collapsed for support counting",
                location="cfim.service:query_itemset",
                context={"raw": payload.items, "deduped": list(items)},
            )
        record = app.state.store.get_corpus(corpus_id)
        for item in items:
            if not isinstance(item, str) or not item.strip():
                raise DomainError(
                    ErrorCode.INVALID_ITEM,
                    "query items must be non-empty strings",
                    {"raw_items": payload.items},
                )
        items = tuple(i.strip() for i in items)
        if not items:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                "query items must contain at least one item; the empty itemset "
                "is not mined by this service",
                {"field": "items"},
            )
        if len(items) > s.max_items_per_transaction:
            raise DomainError(
                ErrorCode.VALIDATION_ERROR,
                f"query has {len(items)} distinct items; limit is "
                f"{s.max_items_per_transaction}",
                {"item_count": len(items)},
            )
        min_support = payload.min_support
        if min_support is not None:
            support_threshold(min_support, record.transaction_count)

        support = len(app.state.store.tidset_for_itemset(corpus_id, items))
        closure = app.state.store.closure_of_itemset(corpus_id, items)
        closure_support = len(app.state.store.tidset_for_itemset(corpus_id, closure))
        is_closed = tuple(sorted(items)) == tuple(sorted(closure))
        same_support_supersets: list[list[str]] = []
        if not is_closed:
            same_support_supersets.append(list(closure))
        log_event(
            logger,
            logging.INFO,
            step="query.evaluated",
            message="itemset verified against independent SQL index",
            location="cfim.service:query_itemset",
            context={
                "corpus_id": corpus_id,
                "itemset": list(items),
                "support": support,
                "closure": list(closure),
                "is_closed": is_closed,
            },
        )
        return QueryResponse(
            corpus_id=corpus_id,
            itemset=list(items),
            support=support,
            transaction_count=record.transaction_count,
            is_frequent=None if min_support is None else support >= min_support,
            closure=list(closure),
            closure_support=closure_support,
            is_closed=is_closed,
            same_support_supersets=same_support_supersets,
            computed_by="store.tidset_for_itemset+closure_of_itemset (independent SQL index)",
            kernel_version=KERNEL_VERSION,
        )

    return app


def _error_response(
    status_code: int, code: ErrorCode, message: str, details: dict[str, Any]
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {"code": code.value, "message": message, "details": details},
            "request_id": current_request_id(),
        },
    )


def _job_response(record, state) -> JobResponse:
    return JobResponse(
        job_id=record.job_id,
        corpus_id=record.corpus_id,
        min_support=record.min_support,
        status=record.status,
        partial=not state.completed,
        nodes_visited=state.nodes_visited,
        total_budget_used=record.total_budget_used,
        budget_unit=budget_unit(),
        closed_count=len(state.results),
        results=[ItemsetResponse(**r.to_dict()) for r in state.results],
        kernel_version=record.kernel_version,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


app = create_app()
