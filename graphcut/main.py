"""FastAPI application: HTTP boundary of the graph-cut service.

Endpoints
---------
GET  /health                      liveness
POST /v1/validate                 dry-run validation report (no solve)
POST /v1/segment                  synchronous segmentation (certified)
POST /v1/jobs                     submit a chunked background job
GET  /v1/jobs/{job_id}            job state + chunk progress
POST /v1/jobs/{job_id}/cancel     cooperative cancellation
GET  /v1/jobs/{job_id}/result     certified result of a SUCCEEDED job

Error contract: every failure is JSON
``{"error": {category, code, message, details, run_id}}`` where category is
one of INPUT_VALIDATION / STATE_CONFLICT / RESOURCE_EXHAUSTED /
COMPUTATION_FAILURE / NOT_FOUND.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from .config import Settings
from .contracts import SegmentationSpec
from .errors import ErrorCategory, GraphCutError
from .graph import build_st_graph
from .jobs import JobManager
from .runlog import RunLogger, new_run_id
from .schemas import SegmentRequest, request_to_spec
from .service import run_segmentation

_CATEGORY_STATUS = {
    ErrorCategory.INPUT_VALIDATION: status.HTTP_400_BAD_REQUEST,
    ErrorCategory.STATE_CONFLICT: status.HTTP_409_CONFLICT,
    ErrorCategory.NOT_FOUND: status.HTTP_404_NOT_FOUND,
    ErrorCategory.COMPUTATION_FAILURE: status.HTTP_500_INTERNAL_SERVER_ERROR,
}
_RESOURCE_STATUS_BY_CODE = {
    "JOB_QUEUE_FULL": status.HTTP_429_TOO_MANY_REQUESTS,
}


def _status_for(exc: GraphCutError) -> int:
    if exc.category is ErrorCategory.RESOURCE_EXHAUSTED:
        return _RESOURCE_STATUS_BY_CODE.get(exc.code, status.HTTP_413_CONTENT_TOO_LARGE)
    return _CATEGORY_STATUS.get(exc.category, status.HTTP_500_INTERNAL_SERVER_ERROR)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(title="graphcut-segmentation", version="1.0.0")
    app.state.settings = settings
    app.state.jobs = JobManager(settings)

    @app.middleware("http")
    async def attach_run_id(request: Request, call_next):
        request.state.run_id = new_run_id()
        return await call_next(request)

    @app.exception_handler(GraphCutError)
    async def graphcut_error_handler(request: Request, exc: GraphCutError):
        run_id = getattr(request.state, "run_id", new_run_id())
        RunLogger(run_id).failure(
            "request.failed", path=request.url.path,
            category=exc.category.value, code=exc.code, message=exc.message,
        )
        body = {"error": {**exc.to_dict(), "run_id": run_id}}
        return JSONResponse(status_code=_status_for(exc), content=body)

    # ----------------------------------------------------------- endpoints
    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/v1/validate")
    def validate(req: SegmentRequest, request: Request) -> dict[str, object]:
        """Dry-run: validate inputs and graph feasibility, solve nothing."""
        run_id: str = request.state.run_id
        log = RunLogger(run_id)
        checks: list[dict[str, object]] = []

        def record(name: str, ok: bool, **fields: object) -> None:
            checks.append({"check": name, "ok": ok, **fields})

        spec: SegmentationSpec | None = None
        try:
            spec = request_to_spec(req, settings)
        except GraphCutError as exc:
            record(exc.code.lower(), False, category=exc.category.value,
                   message=exc.message, details=exc.details)
            log.decision("validate.rejected", reason=exc.message, code=exc.code)
            return {"ok": False, "run_id": run_id, "checks": checks}

        record("input_contract", True, pixels=spec.num_pixels,
               seeds=len(spec.seeds))
        try:
            graph = build_st_graph(spec, settings=settings)
        except GraphCutError as exc:
            record(exc.code.lower(), False, category=exc.category.value,
                   message=exc.message, details=exc.details)
            log.decision("validate.rejected", reason=exc.message, code=exc.code)
            return {"ok": False, "run_id": run_id, "checks": checks}

        record("graph_feasible", True, nodes=graph.num_nodes,
               edges=graph.num_edges, big_m=graph.big_m,
               constant=graph.constant)
        log.decision("validate.accepted", reason="all checks passed",
                     pixels=spec.num_pixels, edges=graph.num_edges)
        return {
            "ok": True,
            "run_id": run_id,
            "checks": checks,
            "summary": {
                "height": spec.height,
                "width": spec.width,
                "num_edges": graph.num_edges,
                "big_m": graph.big_m,
                "graph_constant": graph.constant,
            },
        }

    @app.post("/v1/segment")
    def segment(req: SegmentRequest, request: Request) -> dict[str, object]:
        run_id: str = request.state.run_id
        spec = request_to_spec(req, settings)
        result = run_segmentation(spec, settings=settings, run_id=run_id)
        return result.to_dict()

    @app.post("/v1/jobs", status_code=status.HTTP_202_ACCEPTED)
    async def submit_job(req: SegmentRequest) -> dict[str, object]:
        spec = request_to_spec(req, settings)
        job = app.state.jobs.submit(spec)
        return job.view()

    @app.get("/v1/jobs/{job_id}")
    def job_status(job_id: str) -> dict[str, object]:
        return app.state.jobs.get(job_id).view()

    @app.post("/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, object]:
        return app.state.jobs.cancel(job_id).view()

    @app.get("/v1/jobs/{job_id}/result")
    def job_result(job_id: str) -> dict[str, object]:
        return app.state.jobs.result(job_id).to_dict()

    return app


app = create_app()


def main() -> None:  # pragma: no cover - convenience entry point
    import uvicorn

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    uvicorn.run("graphcut.main:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":  # pragma: no cover
    main()
