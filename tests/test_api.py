"""API behaviour: success paths, error categories, no silent successes."""

import random

import pytest
from fastapi.testclient import TestClient

from app.service import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMIZER_LOG_DIR", str(tmp_path / "logs"))
    return TestClient(create_app(data_dir=tmp_path / "data"))


def make_ref(length: int = 300, seed: int = 20261003) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice("ACGT") for _ in range(length))


def build(client: TestClient, **overrides) -> dict:
    payload = {
        "sequences": [{"name": "ref", "sequence": make_ref()}],
        "config": {"k": 7, "window": 4},
    }
    payload.update(overrides)
    resp = client.post("/indexes", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_health_and_meta(client):
    assert client.get("/health").json() == {"status": "ok"}
    meta = client.get("/meta").json()
    assert "python" in meta["versions"] and "numpy" in meta["versions"]
    assert meta["default_config"]["tie_break"] == "rightmost"


def test_build_then_query_roundtrip(client, runlog):
    built = build(client)
    runlog.step(
        "judgment_basis",
        basis="query is an exact substring of the indexed reference; "
        "top candidate must recover offset 100",
        index_id=built["index_id"],
        run_id=built["run_id"],
    )
    assert built["stats"]["sequences_indexed"] == 1
    assert built["input_fingerprint"]

    info = client.get(f"/indexes/{built['index_id']}")
    assert info.status_code == 200
    assert info.json()["config"]["k"] == 7
    assert info.json()["run_id"] == built["run_id"]

    read = make_ref()[100:180]
    resp = client.post(
        f"/indexes/{built['index_id']}/queries",
        json={"name": "r1", "sequence": read},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["conclusion"] == "candidate_only"
    assert body["candidates"], "expected candidates for exact substring"
    top = body["candidates"][0]
    assert top["seq_name"] == "ref"
    assert top["estimated_ref_start"] == 100


def test_invalid_config_is_400_with_category(client):
    resp = client.post(
        "/indexes",
        json={
            "sequences": [{"name": "r", "sequence": "ACGTACGTACGT"}],
            "config": {"k": 1},
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "INVALID_PARAMETER"


def test_invalid_sequence_is_400_with_category(client):
    resp = client.post(
        "/indexes",
        json={"sequences": [{"name": "r", "sequence": "ACGTNACGTACGT"}]},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "INVALID_SEQUENCE"


def test_unknown_index_is_404(client):
    resp = client.get("/indexes/doesnotexist")
    assert resp.status_code == 404
    assert resp.json()["error"]["category"] == "INDEX_NOT_FOUND"
    resp = client.post(
        "/indexes/doesnotexist/queries",
        json={"sequence": "ACGTACGTACGT"},
    )
    assert resp.status_code == 404


def test_too_short_read_is_400_not_empty_success(client):
    built = build(client)
    resp = client.post(
        f"/indexes/{built['index_id']}/queries",
        json={"sequence": "ACGT"},  # shorter than k + window - 1 = 10
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "SEQUENCE_TOO_SHORT"


def test_malformed_body_is_422(client):
    resp = client.post("/indexes", json={"sequences": []})
    assert resp.status_code == 422
