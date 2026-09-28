"""End-to-end HTTP tests: happy paths and explicit failure categories."""

from __future__ import annotations

import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sparse_embeddings.api.app import create_app
from sparse_embeddings.config import ServiceConfig


@pytest.fixture
def client(tmp_path):
    cfg = ServiceConfig(
        data_dir=tmp_path / "data",
        log_path=tmp_path / "logs" / "svc.jsonl",
        log_to_stderr=False,
    )
    app = create_app(config=cfg)
    with TestClient(app) as c:
        c._log_path = cfg.log_path  # type: ignore[attr-defined]
        yield c


def _create(client, name="embed", **over):
    body = {
        "name": name,
        "vocab_size": 8,
        "dim": 4,
        "optimizer": {"name": "momentum_sgd", "learning_rate": 0.01, "momentum": 0.9},
        "clipping": {"mode": "global", "max_norm": 2.0},
        "seed": 7,
    }
    body.update(over)
    r = client.post("/tables", json=body)
    assert r.status_code == 200, r.text
    return r


def test_health_reports_versions(client):
    r = client.get("/health")
    assert r.status_code == 200
    payload = r.json()
    assert payload["ok"] is True
    assert payload["version"] == "1.0.0"
    assert isinstance(payload["numpy"], str) and payload["numpy"]


def test_create_and_get_table(client):
    _create(client)
    r = client.get("/tables/embed")
    assert r.status_code == 200
    body = r.json()
    assert body["clip_mode"] == "global"
    assert body["global_step"] == 0
    assert body["rows_ever_touched"] == 0


def test_apply_batch_returns_concrete_decision_basis(client):
    _create(client)
    r = client.post(
        "/tables/embed/batches",
        json={
            "indices": [1, 1, 3],
            "values": [[1.0, 0, 0, 0], [1.0, 0, 0, 0], [0, 0, 3.0, 0]],
            "run_id": "e2e-run-1",
        },
    )
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert r.json()["run_id"] == "e2e-run-1"
    assert result["stepped"] is True
    assert result["nnz"] == 3
    assert sorted(result["touched_indices"]) == [1, 3]
    assert result["token_counts"] == [2, 1]
    # Row 1 mean grad = [2/3,0,0,0], row 3 = [0,0,1,0]; norm = sqrt(13)/3.
    assert result["pre_clip_global_norm"] == pytest.approx(np.sqrt(13.0) / 3.0)
    assert result["clip_applied"] is False
    assert result["global_step_after"] == 1


def test_empty_batch_is_no_step_success_not_error(client):
    _create(client)
    r = client.post(
        "/tables/embed/batches", json={"indices": [], "values": [], "run_id": "empty"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["result"]["stepped"] is False
    assert body["result"]["reason"] == "empty_batch_no_step"
    assert body["result"]["global_step_after"] == 0


def test_out_of_range_index_rejects_whole_batch_http(client):
    _create(client)
    good = client.post(
        "/tables/embed/batches",
        json={"indices": [0], "values": [[1.0, 0, 0, 0]]},
    )
    assert good.json()["result"]["global_step_after"] == 1

    r = client.post(
        "/tables/embed/batches",
        json={"indices": [0, 99], "values": [[1.0, 0, 0, 0], [1.0, 0, 0, 0]]},
    )
    assert r.status_code == 400
    err = r.json()["error"]
    assert err["category"] == "index_out_of_range"
    assert err["details"]["bad_index"] == 99
    # Whole batch rejected: step stays at 1, row 99 never touched, row 0
    # unaffected by the rejected second request.
    assert client.get("/tables/embed").json()["global_step"] == 1


def test_non_finite_value_is_numeric_error(client):
    _create(client)
    # NaN is not legal JSON, so send the Python-json token as a raw body;
    # the server's parser accepts it and validation must reject it numerically.
    r = client.post(
        "/tables/embed/batches",
        content='{"indices": [0], "values": [[NaN, 0, 0, 0]]}',
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 422
    assert r.json()["error"]["category"] == "numeric_error"


def test_shape_validation_error_is_422(client):
    _create(client)
    r = client.post(
        "/tables/embed/batches",
        json={"indices": [0, 1], "values": [[0.0, 0, 0, 0]]},  # count mismatch
    )
    assert r.status_code == 422
    assert r.json()["error"]["category"] == "validation_error"


def test_schema_level_bad_body_is_422_validation_error(client):
    _create(client)
    r = client.post(
        "/tables/embed/batches",
        json={"indices": "not-a-list", "values": []},
    )
    assert r.status_code == 422
    assert r.json()["error"]["category"] == "validation_error"


def test_unknown_table_is_404(client):
    r = client.post(
        "/tables/nope/batches", json={"indices": [0], "values": [[0.0] * 4]}
    )
    assert r.status_code == 404
    assert r.json()["error"]["category"] == "not_found"


def test_duplicate_table_is_conflict(client):
    _create(client)
    r = client.post(
        "/tables",
        json={"name": "embed", "vocab_size": 8, "dim": 4},
    )
    assert r.status_code == 409
    assert r.json()["error"]["category"] == "state_conflict"


def test_invalid_clipping_config_rejected_at_creation(client):
    r = client.post(
        "/tables",
        json={
            "name": "bad",
            "vocab_size": 8,
            "dim": 4,
            "clipping": {"mode": "diagonal", "max_norm": 1.0},
        },
    )
    assert r.status_code == 400
    assert r.json()["error"]["category"] == "config_error"


def test_query_rows_returns_optimizer_state(client):
    _create(client)
    client.post(
        "/tables/embed/batches",
        json={"indices": [2], "values": [[0.5, 0.5, 0.5, 0.5]]},
    )
    r = client.post("/tables/embed/rows/query", json={"indices": [2, 6]})
    assert r.status_code == 200
    body = r.json()
    assert body["row_steps"] == [1, 0]
    assert np.all(np.array(body["momentum"][1]) == 0.0)  # untouched row 6


def test_unknown_exception_is_500_internal_error_not_success(tmp_path):
    cfg = ServiceConfig(
        data_dir=tmp_path / "data",
        log_path=tmp_path / "logs" / "svc.jsonl",
        log_to_stderr=False,
    )
    app = create_app(config=cfg)

    def boom(_name):
        raise RuntimeError("unexpected catastrophe")

    app.state.service.get_table = boom  # type: ignore[method-assign]
    # Do not re-raise server exceptions: we want the HTTP 500 envelope.
    with TestClient(app, raise_server_exceptions=False) as client:
        r = client.post(
            "/tables/embed/batches", json={"indices": [0], "values": [[0.0] * 4]}
        )
    assert r.status_code == 500
    body = r.json()
    assert body["ok"] is False
    assert body["error"]["category"] == "internal_error"
    assert "RuntimeError" in body["error"]["message"]


def test_query_rows_rejects_out_of_range(client):
    _create(client)
    r = client.post("/tables/embed/rows/query", json={"indices": [99]})
    assert r.status_code == 400
    assert r.json()["error"]["category"] == "index_out_of_range"


def test_run_id_links_log_line_to_inputs(client):
    _create(client)
    client.post(
        "/tables/embed/batches",
        json={
            "indices": [3, 3],
            "values": [[1.0, 0, 0, 0], [0.0, 1.0, 0, 0]],
            "run_id": "traceable-42",
        },
    )
    lines = [
        json.loads(line)
        for line in client._log_path.read_text(encoding="utf-8").splitlines()  # type: ignore[attr-defined]
    ]
    matched = [ln for ln in lines if ln.get("run_id") == "traceable-42"]
    assert matched, "no log line correlated with the supplied run_id"
    applied = [ln for ln in matched if ln["event"] == "batch_applied"]
    assert applied and applied[0]["verdict"] == "applied"
    # Versions and decision basis present.
    assert applied[0]["versions"]["numpy"]
    assert applied[0]["n_unique_touched"] == 1
    recv = [ln for ln in matched if ln["event"] == "batch_received"][0]
    assert recv["input_indices"] == [3, 3]


def test_rejected_batch_logged_as_rejected_not_success(client):
    _create(client)
    client.post(
        "/tables/embed/batches",
        json={"indices": [8], "values": [[1.0, 0, 0, 0]], "run_id": "reject-1"},
    )
    lines = [
        json.loads(line)
        for line in client._log_path.read_text(encoding="utf-8").splitlines()  # type: ignore[attr-defined]
        if line.strip()
    ]
    rejects = [ln for ln in lines if ln.get("run_id") == "reject-1"]
    assert rejects, "rejection must be correlated to the supplied run_id"
    # The typed failure is logged explicitly; it is never reported as success.
    assert not any(ln["verdict"] == "applied" for ln in rejects)
    error_lines = [ln for ln in rejects if ln["event"] == "http_error"]
    assert error_lines and error_lines[0]["category"] == "index_out_of_range"
