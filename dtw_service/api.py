"""FastAPI surface for the DTW alignment service.

Domain outcomes (accepted / rejected / indeterminate) are returned as
``200`` with the structured ``AlignmentResponse`` — a rejected alignment is
a valid decision, not a transport error. Schema-level malformed payloads
still surface as FastAPI's ``422``.
"""

from __future__ import annotations

from fastapi import FastAPI

from dtw_service.contracts import AlignmentRequest, AlignmentResponse
from dtw_service.service import align
from dtw_service.settings import load_settings

app = FastAPI(title="dtw-alignment-service", version="1.0.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/dtw", response_model=AlignmentResponse)
def dtw_endpoint(request: AlignmentRequest) -> AlignmentResponse:
    return align(request, settings=load_settings())
