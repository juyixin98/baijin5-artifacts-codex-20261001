"""FastAPI surface for the local Paillier aggregation test service."""

from __future__ import annotations

import platform

import cryptography
import fastapi
import phe
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from . import __version__
from .config import Settings
from .encoding import EncodingParams
from .errors import PaillierServiceError
from .schemas import CreateBatchRequest, SubmitContributionRequest
from .service import AggregationService, new_run_id
from .storage import Storage
from .verifier import verify_batch


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    storage = Storage(settings.database_path)
    service = AggregationService(settings, storage)

    app = FastAPI(title="paillier-aggregate-local", version=__version__)
    app.state.service = service
    app.state.storage = storage

    @app.exception_handler(PaillierServiceError)
    async def service_error_handler(_: Request, exc: PaillierServiceError):
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": exc.to_dict()},
        )

    @app.get("/health")
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/meta")
    def meta() -> dict:
        return {
            "service_version": __version__,
            "python_version": platform.python_version(),
            "phe_version": phe.__version__,
            "fastapi_version": fastapi.__version__,
            "cryptography_version": cryptography.__version__,
            "supported_operations": [
                "ciphertext_addition",
                "plaintext_scalar_multiplication",
            ],
            "unsupported_operations": [
                "ciphertext_multiplication",
                "comparison_on_ciphertexts",
            ],
        }

    @app.post("/batches", status_code=201)
    def create_batch(req: CreateBatchRequest) -> dict:
        run_id = new_run_id()
        params = EncodingParams(
            max_plaintext_abs=req.max_plaintext_abs
            if req.max_plaintext_abs is not None
            else settings.encoding.max_plaintext_abs,
            max_coefficient_abs=req.max_coefficient_abs
            if req.max_coefficient_abs is not None
            else settings.encoding.max_coefficient_abs,
            max_aggregate_abs=req.max_aggregate_abs
            if req.max_aggregate_abs is not None
            else settings.encoding.max_aggregate_abs,
        )
        result = service.create_batch(
            run_id, label=req.label, params=params, key_size=req.key_size
        )
        return {"run_id": run_id, "batch": result}

    @app.get("/batches/{batch_id}")
    def get_batch(batch_id: str) -> dict:
        return {"batch": service.describe_batch(batch_id)}

    @app.post("/batches/{batch_id}/contributions", status_code=201)
    def submit_contribution(batch_id: str, req: SubmitContributionRequest) -> dict:
        run_id = new_run_id()
        try:
            ciphertext = int(req.ciphertext)
        except ValueError:
            from .errors import ErrorCategory

            raise PaillierServiceError(
                ErrorCategory.CIPHERTEXT_INVALID,
                "ciphertext must be a decimal integer string",
                {"ciphertext": req.ciphertext[:64]},
            )
        result = service.submit_contribution(
            run_id,
            batch_id=batch_id,
            participant_id=req.participant_id,
            key_id=req.key_id,
            ciphertext=ciphertext,
            coefficient=req.coefficient,
            plaintext_fixture=req.plaintext_fixture,
        )
        return {"run_id": run_id, "contribution": result}

    @app.post("/batches/{batch_id}/aggregate")
    def aggregate(batch_id: str) -> dict:
        run_id = new_run_id()
        return {"run_id": run_id, "aggregate": service.aggregate(run_id, batch_id)}

    @app.post("/batches/{batch_id}/decrypt")
    def decrypt(batch_id: str) -> dict:
        run_id = new_run_id()
        return {"run_id": run_id, "result": service.decrypt_result(run_id, batch_id)}

    @app.post("/batches/{batch_id}/verify")
    def verify(batch_id: str) -> dict:
        run_id = new_run_id()
        return {"run_id": run_id, "verification": verify_batch(service, run_id, batch_id)}

    @app.get("/batches/{batch_id}/audit")
    def audit(batch_id: str) -> dict:
        service.describe_batch(batch_id)  # 404 if unknown
        return {"audit": storage.list_audit(batch_id)}

    return app


app = create_app()
