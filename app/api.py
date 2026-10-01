"""FastAPI application: thin HTTP layer over the analysis pipeline.

Route handlers are synchronous ``def`` on purpose: the work is CPU-bound
NumPy/SciPy plus a quick SQLite write, and FastAPI runs sync handlers in a
worker threadpool so the event loop is not blocked.
"""
from __future__ import annotations

import json
import threading
import time
from collections import deque
from typing import Deque

from fastapi import Depends, FastAPI, HTTPException, Request

from app import __version__, logging_setup
from app.config import Settings
from app.contract import (
    DataPoint,
    RDRequest,
    RDResponse,
    RunStatus,
)
from app.datasets import FIXTURE_REGISTRY
from app.dependencies import get_settings, get_store
from app.pipeline import run_analysis
from app.store import RunStore


class FixedWindowRateLimiter:
    """Tiny in-process fixed-window limiter (local single-process deploy).

    Not a substitute for a gateway limiter; it only stops a tight loop of
    expensive analyses from saturating the threadpool.
    """

    def __init__(self, max_events: int, window_s: float) -> None:
        self._max = max_events
        self._window = window_s
        self._events: dict[str, Deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            bucket = self._events.setdefault(key, deque())
            while bucket and now - bucket[0] > self._window:
                bucket.popleft()
            if len(bucket) >= self._max:
                raise HTTPException(
                    status_code=429,
                    detail=(
                        f"rate limit: {self._max} analyses per {self._window:g}s "
                        "per client; slow down or run batch locally"
                    ),
                )
            bucket.append(now)


def create_app() -> FastAPI:
    settings = get_settings()
    logging_setup.configure_logging(settings.log_level)
    app = FastAPI(
        title="Local-linear RD backend",
        version=__version__,
        description=(
            "Synthetic-data regression-discontinuity service: separate "
            "local-linear fits at the cutoff, robust and bootstrap inference, "
            "discreteness/heaping and density-sorting diagnostics."
        ),
    )
    app.state.settings = settings
    app.state.store = get_store()
    app.state.limiter = FixedWindowRateLimiter(max_events=30, window_s=10.0)

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get("/version")
    def version() -> dict:
        return settings.versions()

    @app.get("/api/v1/fixtures")
    def list_fixtures() -> dict:
        return {
            "fixtures": [
                {"name": name, "doc": (fn.__doc__ or "").strip().splitlines()[0]}
                for name, fn in FIXTURE_REGISTRY.items()
            ]
        }

    @app.post("/api/v1/fixtures/{name}/sample")
    def sample_fixture(name: str, n: int = 1000, seed: int = 7) -> dict:
        if name not in FIXTURE_REGISTRY:
            raise HTTPException(status_code=404, detail=f"unknown fixture {name!r}")
        if not 20 <= n <= 20000:
            raise HTTPException(status_code=422, detail="n must be in [20, 20000]")
        dgp = FIXTURE_REGISTRY[name](n=n, seed=seed)
        return {
            "fixture": name,
            "seed": seed,
            "true_tau": dgp.tau,
            "params": dgp.params,
            "data": [
                DataPoint(x=float(xi), y=float(yi)).model_dump()
                for xi, yi in zip(dgp.x, dgp.y)
            ],
        }

    @app.post("/api/v1/rd/analyze", response_model=RDResponse)
    def analyze(
        request: RDRequest,
        http_request: Request,
        store: RunStore = Depends(get_store),
        cfg: Settings = Depends(get_settings),
    ) -> RDResponse:
        client = http_request.client.host if http_request.client else "local"
        http_app = http_request.app
        http_app.state.limiter.check(client)
        response = run_analysis(request, cfg)
        store.save(
            request.model_dump_json(),
            response.model_dump_json(),
        )
        if response.status is RunStatus.ERROR:
            # Input errors were validated by pydantic; reaching here is a
            # pipeline-level failure -> 422 with the explicit error envelope.
            raise HTTPException(
                status_code=422,
                detail={
                    "run_id": response.run_id,
                    "error_code": response.error_code,
                    "error_message": response.error_message,
                },
            )
        return response

    @app.get("/api/v1/runs/{run_id}")
    def get_run(
        run_id: str, store: RunStore = Depends(get_store)
    ) -> dict:
        row = store.get(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="run_id not found")
        row["request"] = json.loads(row.pop("request_json"))
        row["response"] = json.loads(row.pop("response_json"))
        return row

    @app.get("/api/v1/runs")
    def list_runs(
        limit: int = 50,
        status: RunStatus | None = None,
        store: RunStore = Depends(get_store),
    ) -> dict:
        limit = max(1, min(limit, 500))
        rows = store.list_runs(
            limit=limit, status=None if status is None else status.value
        )
        return {"runs": rows, "count": len(rows)}

    return app


app = create_app()
