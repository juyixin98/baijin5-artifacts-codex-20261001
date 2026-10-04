"""HTTP routes. All mutating endpoints echo the request id and the decision
taken; failures surface the typed error category via the exception handler
registered in ``app.py``.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from commit_reveal.api.app import get_request_id, get_service
from commit_reveal.api.schemas import (
    CommitRequest,
    CreateRoundRequest,
    RevealRequest,
)
from commit_reveal.state.service import RoundService

router = APIRouter()


@router.post("/rounds", status_code=201)
def create_round(
    body: CreateRoundRequest,
    request: Request,
    service: RoundService = Depends(get_service),
):
    request_id = get_request_id(request)
    rnd = service.create_round(
        participants=body.participants,
        commit_deadline=body.commit_deadline,
        reveal_deadline=body.reveal_deadline,
        min_reveals=(
            body.min_reveals
            if body.min_reveals is not None
            else request.app.state.settings.default_min_reveals
        ),
        request_id=request_id,
        round_id=body.round_id,
    )
    return {"request_id": request_id, "round": rnd}


@router.post("/rounds/{round_id}/commitments", status_code=201)
def commit(
    round_id: str,
    body: CommitRequest,
    request: Request,
    service: RoundService = Depends(get_service),
):
    request_id = get_request_id(request)
    service.commit(round_id, body.participant_id, body.commitment, request_id)
    return {"request_id": request_id, "decision": "COMMIT_ACCEPTED"}


@router.post("/rounds/{round_id}/reveals", status_code=201)
def reveal(
    round_id: str,
    body: RevealRequest,
    request: Request,
    service: RoundService = Depends(get_service),
):
    request_id = get_request_id(request)
    service.reveal(
        round_id, body.participant_id, body.value, body.salt, request_id
    )
    return {"request_id": request_id, "decision": "REVEAL_ACCEPTED"}


@router.post("/rounds/{round_id}/finalize")
def finalize(
    round_id: str,
    request: Request,
    service: RoundService = Depends(get_service),
):
    request_id = get_request_id(request)
    result = service.finalize(round_id, request_id)
    return {"request_id": request_id, "result": result}


@router.get("/rounds/{round_id}")
def get_round(round_id: str, service: RoundService = Depends(get_service)):
    return {"round": service.get_round(round_id)}


@router.get("/rounds/{round_id}/evidence")
def get_evidence(round_id: str, service: RoundService = Depends(get_service)):
    return {"evidence": service.get_evidence(round_id)}


@router.get("/rounds/{round_id}/audit")
def get_audit(round_id: str, service: RoundService = Depends(get_service)):
    return {"audit": service.list_audit(round_id)}
