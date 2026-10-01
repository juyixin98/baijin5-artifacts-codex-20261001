"""Integration tests for storage/service defensive branches."""

from __future__ import annotations

import pytest

from app.core.errors import ErrorCode, EstimationError
from app.storage.db import Database


@pytest.mark.integration
def test_save_run_rejects_unknown_status(tmp_path):
    db = Database(tmp_path / "edge.db")
    db.register_experiment("e", "d", [])
    with pytest.raises(ValueError, match="refusing to persist"):
        db.save_run("r1", "e", {"status": "pending"})
    db.close()


@pytest.mark.integration
def test_run_on_registered_experiment_without_observations_is_empty(config_path, tmp_path):
    from app.core.config import load_config
    from app.storage.service import ExperimentService

    cfg = load_config(str(config_path))
    db = Database(str(tmp_path / "edge2.db"))
    service = ExperimentService(db, cfg)
    service.register("empty-exp", "no data", [{"name": "x_pre", "pre_treatment": True}])
    with pytest.raises(EstimationError) as exc:
        service.run_estimation("empty-exp", run_id="edge-empty")
    assert exc.value.code is ErrorCode.EMPTY_DATA
    stored = service.get_run("edge-empty")
    assert stored["status"] == "failed"
    assert stored["error_code"] == "EMPTY_DATA"
    db.close()
