"""Orchestration: validate -> phase -> record provenance -> respond.

Every request gets a UUID request id up front so that even validation
failures are recorded and traceable.  Key steps are logged with the
request id attached.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid

from . import ALGORITHM_VERSION, __version__
from .config import Settings
from .errors import DatasetError, ProcessingError
from .models import PhaseRequest
from .parsing import normalize
from .phasing import phase, result_to_dict
from .provenance import ProvenanceStore

logger = logging.getLogger("haplo.service")


class PhasingService:
    def __init__(self, settings: Settings, store: ProvenanceStore):
        self._settings = settings
        self._store = store

    @staticmethod
    def _input_hash(request: PhaseRequest) -> str:
        payload = json.dumps(request.model_dump(), sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def run_phase(self, request: PhaseRequest) -> dict:
        request_id = str(uuid.uuid4())
        log = logging.LoggerAdapter(logger, {"request_id": request_id})
        input_sha256 = self._input_hash(request)
        log.info(
            "phase request received: sample=%s sites=%d reads=%d input_sha256=%s",
            request.sample_id, len(request.sites), len(request.reads), input_sha256,
        )
        try:
            dataset = normalize(request, max_quality=self._settings.max_quality)
            log.info("validation passed: %d sites, %d reads", dataset.n_sites, dataset.n_reads)
            result = phase(dataset, max_enum_sites=self._settings.max_enum_sites)
            for block in result.blocks:
                log.info(
                    "block %d: sites=%s mec=%d ambiguous=%s optima=%d",
                    block.block_index, ",".join(block.site_ids), block.mec,
                    block.ambiguous, block.n_optima,
                )
            for uncertainty in result.uncertainties:
                log.warning("uncertainty: %s", uncertainty)
        except (DatasetError, ProcessingError) as exc:
            log.warning("request failed: %s: %s", exc.category.value, exc.message)
            self._store.record_run(
                request_id=request_id,
                sample_id=request.sample_id,
                input_sha256=input_sha256,
                status="failed",
                failure_category=exc.category.value,
                error=exc.to_dict(),
            )
            raise PhasingFailure(request_id=request_id, error=exc) from exc

        result_dict = result_to_dict(result)
        self._store.record_run(
            request_id=request_id,
            sample_id=request.sample_id,
            input_sha256=input_sha256,
            status="success",
            result=result_dict,
        )
        log.info("provenance recorded: status=success total_mec=%d", result.total_mec)
        return {
            "request_id": request_id,
            "status": "success",
            "versions": {
                "app": __version__,
                "algorithm": ALGORITHM_VERSION,
            },
            "input_sha256": input_sha256,
            "result": result_dict,
        }


class PhasingFailure(Exception):
    """Wraps a categorized DatasetError/ProcessingError with the request id."""

    def __init__(self, request_id: str, error: DatasetError | ProcessingError):
        super().__init__(error.message)
        self.request_id = request_id
        self.error = error
