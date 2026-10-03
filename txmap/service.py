"""FastAPI service layer: mapping endpoints + validation + provenance access.

Every mapping/validation request is assigned a request id (honoring an
incoming X-Request-ID header), logged with its decision and reason, and
appended to the SQLite provenance log.
"""

from __future__ import annotations

import uuid
from typing import Literal

from fastapi import FastAPI, Request
from pydantic import BaseModel, Field

from .diagnostics import log_decision
from .mapping import Mapper
from .models import Status
from .provenance import (
    ProvenanceRecord,
    interval_outcome_dict,
    point_outcome_dict,
)
from .store import Store


class PointRequest(BaseModel):
    transcript_id: str = Field(min_length=1)
    position: int


class IntervalRequest(BaseModel):
    transcript_id: str = Field(min_length=1)
    start: int
    end: int


class RoundtripRequest(BaseModel):
    transcript_id: str = Field(min_length=1)
    space: Literal["genomic", "transcript"]
    position: int


def _request_id(request: Request) -> str:
    incoming = request.headers.get("x-request-id", "").strip()
    return incoming or uuid.uuid4().hex


def create_app(mapper: Mapper, store: Store) -> FastAPI:
    app = FastAPI(title="txmap", version="0.1.0")

    def record(request: Request, endpoint: str, tx_id: str | None,
               status: str, reason: str, inputs: dict, outputs: dict) -> str:
        rid = _request_id(request)
        store.record_provenance(
            ProvenanceRecord.build(rid, endpoint, tx_id, status, reason, inputs, outputs)
        )
        log_decision(rid, endpoint, status, reason, {**inputs, "result": outputs})
        return rid

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok", "transcripts": list(mapper.transcript_ids())}

    @app.get("/transcripts")
    def list_transcripts() -> dict:
        out = []
        for tx_id in mapper.transcript_ids():
            tx = mapper.get_transcript(tx_id)
            assert tx is not None
            out.append(
                {
                    "tx_id": tx.tx_id,
                    "gene": tx.gene,
                    "contig": tx.contig,
                    "strand": tx.strand.value,
                    "exons": [[e.start, e.end] for e in tx.exons],
                    "tx_length": tx.tx_length,
                }
            )
        return {"transcripts": out}

    @app.get("/transcripts/{tx_id}/sequence")
    def spliced_sequence(tx_id: str, request: Request) -> dict:
        status, reason, seq = mapper.spliced_sequence(tx_id)
        outputs = {"length": len(seq) if seq is not None else None,
                   "sequence": seq}
        rid = record(request, "spliced_sequence", tx_id, status.value,
                     reason.value, {"transcript_id": tx_id}, outputs)
        return {
            "request_id": rid,
            "status": status.value,
            "reason": reason.value,
            "transcript_id": tx_id,
            "length": outputs["length"],
            "sequence": seq,
        }

    @app.post("/map/genomic-to-transcript")
    def map_g2t(body: PointRequest, request: Request) -> dict:
        outcome = mapper.genomic_to_transcript(body.transcript_id, body.position)
        payload = point_outcome_dict(outcome)
        rid = record(request, "genomic_to_transcript", body.transcript_id,
                     payload["status"], payload["reason"], body.model_dump(), payload)
        return {"request_id": rid, **payload}

    @app.post("/map/transcript-to-genomic")
    def map_t2g(body: PointRequest, request: Request) -> dict:
        outcome = mapper.transcript_to_genomic(body.transcript_id, body.position)
        payload = point_outcome_dict(outcome)
        rid = record(request, "transcript_to_genomic", body.transcript_id,
                     payload["status"], payload["reason"], body.model_dump(), payload)
        return {"request_id": rid, **payload}

    @app.post("/map/genomic-interval")
    def map_g_interval(body: IntervalRequest, request: Request) -> dict:
        outcome = mapper.genomic_interval_to_transcript(
            body.transcript_id, body.start, body.end
        )
        payload = interval_outcome_dict(outcome)
        rid = record(request, "genomic_interval", body.transcript_id,
                     payload["status"], payload["reason"], body.model_dump(), payload)
        return {"request_id": rid, **payload}

    @app.post("/map/transcript-interval")
    def map_t_interval(body: IntervalRequest, request: Request) -> dict:
        outcome = mapper.transcript_interval_to_genomic(
            body.transcript_id, body.start, body.end
        )
        payload = interval_outcome_dict(outcome)
        rid = record(request, "transcript_interval", body.transcript_id,
                     payload["status"], payload["reason"], body.model_dump(), payload)
        return {"request_id": rid, **payload}

    @app.post("/validate/roundtrip")
    def validate_roundtrip(body: RoundtripRequest, request: Request) -> dict:
        """Map a position forward and back; identity must hold for exonic bases."""
        if body.space == "genomic":
            fwd = mapper.genomic_to_transcript(body.transcript_id, body.position)
            back = (
                mapper.transcript_to_genomic(body.transcript_id, fwd.mapped)
                if fwd.status is Status.OK
                else None
            )
        else:
            fwd = mapper.transcript_to_genomic(body.transcript_id, body.position)
            back = (
                mapper.genomic_to_transcript(body.transcript_id, fwd.mapped)
                if fwd.status is Status.OK
                else None
            )
        identity = back is not None and back.mapped == body.position
        outputs = {
            "forward": point_outcome_dict(fwd),
            "backward": point_outcome_dict(back) if back is not None else None,
            "identity": identity,
        }
        status = fwd.status.value
        rid = record(request, "validate_roundtrip", body.transcript_id,
                     status, fwd.reason.value, body.model_dump(), outputs)
        return {"request_id": rid, "status": status, **outputs}

    @app.get("/provenance/{request_id}")
    def provenance(request_id: str) -> dict:
        return {"request_id": request_id, "records": store.get_provenance(request_id)}

    return app
