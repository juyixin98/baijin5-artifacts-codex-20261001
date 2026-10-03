"""HTTP routes.  Thin layer: validate contract -> drive session -> report."""
from __future__ import annotations

import logging

import numpy as np
from fastapi import APIRouter, Request

from app import contracts
from app.convolution import SwapStrategy
from app.diagnostics import current_request_id, log_decision, sample_digest
from app.sessions import SessionRegistry

logger = logging.getLogger("pcv.routes")

router = APIRouter()


def _registry(request: Request) -> SessionRegistry:
    return request.app.state.registry


@router.get("/healthz")
def healthz(request: Request) -> dict:
    settings = request.app.state.settings
    return {"status": "ok", "service": settings.service_name}


@router.post("/v1/sessions", response_model=contracts.CreateSessionResponse, status_code=201)
def create_session(body: contracts.CreateSessionRequest, request: Request):
    ir = np.asarray(body.ir, dtype=np.float64)
    session = _registry(request).create(
        sample_rate=body.sample_rate,
        block_size=body.block_size,
        ir=ir,
        swap_strategy=SwapStrategy(body.swap_strategy),
        crossfade_blocks=body.crossfade_blocks,
        max_state_bytes=body.max_state_bytes,
    )
    log_decision(
        logger, "accepted", "session created",
        session_id=session.session_id, block_size=session.block_size,
        ir_length=session.convolver.current_ir_length, ir_payload=sample_digest(ir),
    )
    report = session.state_report()
    return contracts.CreateSessionResponse(
        session_id=session.session_id,
        request_id=current_request_id(),
        block_size=session.block_size,
        ir_length=report["ir_length"],
        num_partitions=report["num_partitions"],
        state_bytes=contracts.StateBytesReport(**report["state_bytes"]),
        budget_bytes=report["budget_bytes"],
    )


@router.get("/v1/sessions/{session_id}", response_model=contracts.SessionStateResponse)
def get_session(session_id: str, request: Request):
    report = _registry(request).get(session_id).state_report()
    return contracts.SessionStateResponse(
        request_id=current_request_id(),
        **{k: v for k, v in report.items() if k != "state_bytes"},
        state_bytes=contracts.StateBytesReport(**report["state_bytes"]),
    )


@router.delete("/v1/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, request: Request):
    _registry(request).delete(session_id)
    log_decision(logger, "accepted", "session deleted", session_id=session_id)


@router.post("/v1/sessions/{session_id}/blocks", response_model=contracts.BlockResponse)
def push_block(session_id: str, body: contracts.BlockRequest, request: Request):
    session = _registry(request).get(session_id)
    out = session.push_block(np.asarray(body.samples, dtype=np.float64), final=body.final)
    log_decision(
        logger, "accepted", "block processed",
        session_id=session_id, blocks_processed=session.blocks_processed,
        final=body.final, payload=sample_digest(out),
    )
    return contracts.BlockResponse(
        session_id=session_id,
        request_id=current_request_id(),
        output=out.tolist(),
        output_length=int(out.size),
        blocks_processed=session.blocks_processed,
    )


@router.post("/v1/sessions/{session_id}/flush", response_model=contracts.FlushResponse)
def flush_session(session_id: str, request: Request):
    session = _registry(request).get(session_id)
    tail = session.flush()
    log_decision(
        logger, "accepted", "session flushed",
        session_id=session_id, tail_length=int(tail.size),
        total_input=session.total_input, total_output=session.total_output,
    )
    return contracts.FlushResponse(
        session_id=session_id,
        request_id=current_request_id(),
        tail=tail.tolist(),
        tail_length=int(tail.size),
        total_input_samples=session.total_input,
        total_output_samples=session.total_output,
    )


@router.post("/v1/sessions/{session_id}/ir", response_model=contracts.SwapIRResponse)
def swap_ir(session_id: str, body: contracts.SwapIRRequest, request: Request):
    session = _registry(request).get(session_id)
    ir = np.asarray(body.ir, dtype=np.float64)
    strategy = SwapStrategy(body.strategy) if body.strategy else session.convolver.default_strategy
    session.swap_ir(ir, strategy)
    log_decision(
        logger, "accepted", "ir swapped",
        session_id=session_id, strategy=strategy.value,
        ir_length=session.convolver.current_ir_length, ir_payload=sample_digest(ir),
    )
    report = session.state_report()
    return contracts.SwapIRResponse(
        session_id=session_id,
        request_id=current_request_id(),
        strategy=strategy.value,
        ir_length=report["ir_length"],
        num_partitions=report["num_partitions"],
        state_bytes=contracts.StateBytesReport(**report["state_bytes"]),
    )
