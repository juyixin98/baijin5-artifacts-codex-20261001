"""Training session routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ..state import LinearSGDTrainer
from .schemas import TrainingCreate


class TrainingRunRequest(BaseModel):
    steps: int
    background: bool = False


class TrainingWaitRequest(BaseModel):
    timeout: float | None = None


def _status(handle: str, trainer: LinearSGDTrainer) -> dict:
    body = trainer.describe()
    body["handle"] = handle
    return body


def register_training_routes(router: APIRouter) -> None:

    @router.post("/training", status_code=201, tags=["training"])
    async def create_session(payload: TrainingCreate, request: Request) -> dict:
        store = request.app.state.tensors
        trainer = LinearSGDTrainer(
            store.get(payload.features),
            store.get(payload.targets),
            learning_rate=payload.learning_rate,
            max_steps=payload.max_steps,
            tol=payload.tol)
        handle = request.app.state.trainers.put(trainer)
        return {"ok": True, "handle": handle, "status": _status(handle, trainer)}

    @router.get("/training", tags=["training"])
    async def list_sessions(request: Request) -> dict:
        handles = request.app.state.trainers.list_handles()
        return {"ok": True, "handles": handles, "count": len(handles)}

    @router.get("/training/{handle}", tags=["training"])
    async def get_session(handle: str, request: Request) -> dict:
        return {"ok": True, "status": _status(
            handle, request.app.state.trainers.get(handle))}

    @router.post("/training/{handle}/run", tags=["training"])
    async def run(handle: str, payload: TrainingRunRequest,
                  request: Request) -> dict:
        trainer = request.app.state.trainers.get(handle)
        if payload.background:
            trainer.run_background(payload.steps)
            return {"ok": True, "background": True,
                    "status": _status(handle, trainer)}
        reports = trainer.run(payload.steps)
        return {"ok": True, "background": False,
                "status": _status(handle, trainer),
                "steps": [report.to_dict() for report in reports]}

    @router.post("/training/{handle}/wait", tags=["training"])
    async def wait(handle: str, payload: TrainingWaitRequest,
                   request: Request) -> dict:
        trainer = request.app.state.trainers.get(handle)
        settled = trainer.wait_idle(payload.timeout)
        return {"ok": True, "settled": settled,
                "status": _status(handle, trainer)}

    @router.post("/training/{handle}/pause", tags=["training"])
    async def pause(handle: str, request: Request) -> dict:
        trainer = request.app.state.trainers.get(handle)
        trainer.request_pause()
        return {"ok": True, "pause_requested": True,
                "status": _status(handle, trainer)}

    @router.post("/training/{handle}/resume", tags=["training"])
    async def resume(handle: str, request: Request) -> dict:
        trainer = request.app.state.trainers.get(handle)
        trainer.resume()
        return {"ok": True, "background": True,
                "status": _status(handle, trainer)}

    @router.get("/training/{handle}/weights", tags=["training"])
    async def weights(handle: str, request: Request) -> dict:
        trainer = request.app.state.trainers.get(handle)
        return {"ok": True,
                "weights": trainer.weights.to_nested(),
                "bias": trainer.bias.to_nested(),
                "storage_tokens": {
                    "weights": trainer.weights.token,
                    "bias": trainer.bias.token,
                }}

    @router.get("/training/{handle}/predictions", tags=["training"])
    async def predictions(handle: str, request: Request) -> dict:
        trainer = request.app.state.trainers.get(handle)
        return {"ok": True, "predictions": trainer.predict().to_nested()}

    @router.delete("/training/{handle}", tags=["training"])
    async def delete_session(handle: str, request: Request) -> dict:
        request.app.state.trainers.delete(handle)
        return {"ok": True, "deleted": handle}
