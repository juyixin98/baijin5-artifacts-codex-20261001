"""FastAPI control plane: bundle management, connections, audit, explain.

The control plane never terminates TLS itself; it administers the data
plane and exposes the persisted state. Error contract: any TrustLabError
is returned as ``{"error": {category, message, detail, reasoning}}`` with
the category-specific HTTP status (400 / 409 / 429 / 422).
"""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .errors import TrustLabError
from .explainer import explain_untrusted_root
from .service import TrustLabService


class BundleCreateIn(BaseModel):
    roots_pem: list[str] = Field(min_length=1)
    note: str = ""
    explicit_version: int | None = None


class RotateIn(BaseModel):
    new_roots_pem: list[str] = Field(min_length=1)
    note: str = ""


class RollbackIn(BaseModel):
    to_version: int
    note: str = ""


class ExplainIn(BaseModel):
    chain_pem: list[str] = Field(min_length=1)


def create_app(service: TrustLabService) -> FastAPI:
    app = FastAPI(title="trustlab control plane", version="1.0.0")

    @app.exception_handler(TrustLabError)
    async def _trustlab_error(_request: Request, exc: TrustLabError):
        # Rejected requests are audited too: input errors and state
        # conflicts must be distinguishable in the replayable log.
        service.store.audit(
            run_id=service.run_id,
            category=exc.category.value,
            event="request_rejected",
            detail=exc.to_dict(),
            reasoning=exc.reasoning,
        )
        return JSONResponse(status_code=exc.http_status,
                            content={"error": exc.to_dict()})

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "run_id": service.run_id}

    @app.get("/run")
    def run_info() -> dict:
        return {
            "run_id": service.run_id,
            "started_at": service.started_at,
            "data_plane": {"host": service.host, "port": service.port},
        }

    @app.get("/bundles")
    def list_bundles() -> dict:
        return {"bundles": [b.to_dict() for b in service.bundles.history()]}

    @app.post("/bundles", status_code=201)
    def create_bundle(body: BundleCreateIn) -> dict:
        bundle = service.bundles.create(
            body.roots_pem, kind="manual", note=body.note,
            explicit_version=body.explicit_version,
        )
        return {"bundle": bundle.to_dict()}

    @app.post("/rotation/begin", status_code=201)
    def rotation_begin(body: RotateIn) -> dict:
        bundle = service.bundles.begin_rotation(body.new_roots_pem,
                                                note=body.note)
        return {"bundle": bundle.to_dict()}

    @app.post("/rotation/end", status_code=201)
    def rotation_end(body: RotateIn) -> dict:
        bundle = service.bundles.end_rotation(body.new_roots_pem,
                                              note=body.note)
        return {"bundle": bundle.to_dict()}

    @app.post("/rollback", status_code=201)
    def rollback(body: RollbackIn) -> dict:
        bundle = service.bundles.rollback(body.to_version, note=body.note)
        return {"bundle": bundle.to_dict()}

    @app.get("/connections")
    def list_connections() -> dict:
        active = service.bundles.active()
        active_version = active.version if active else None
        rows = service.store.list_connections(run_id=service.run_id)
        live_ids = {c["connection_id"] for c in service.data_plane.live_connections()}
        return {
            "active_bundle_version": active_version,
            "connections": [
                {
                    **row,
                    "live": row["id"] in live_ids,
                    "legacy": (row["bundle_version"] != active_version
                               and row["closed_at"] is None),
                }
                for row in rows
            ],
        }

    @app.post("/connections/{connection_id}/revoke")
    def revoke_connection(connection_id: str) -> dict:
        service.data_plane.revoke_connection(connection_id)
        return {"revoked": connection_id}

    @app.get("/audit")
    def list_audit(category: str | None = None) -> dict:
        return {
            "run_id": service.run_id,
            "audit": service.store.list_audit(run_id=service.run_id,
                                              category=category),
        }

    @app.get("/failures")
    def list_failures() -> dict:
        return {
            "run_id": service.run_id,
            "failures": service.store.list_handshake_failures(service.run_id),
        }

    @app.post("/explain")
    def explain(body: ExplainIn) -> dict:
        return {
            "explanation": explain_untrusted_root(
                service.store, presented_chain_pem=body.chain_pem
            )
        }

    return app
