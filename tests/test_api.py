"""HTTP boundary: status codes, error categories and end-to-end online flow."""

import pytest
from fastapi.testclient import TestClient

from app.service import create_app


@pytest.fixture()
def client():
    app = create_app(db_path=":memory:")
    with TestClient(app) as c:
        yield c
    app.state.store.close()


def _submit(client, run_id, hid, p):
    return client.post(
        f"/runs/{run_id}/decisions",
        json={"hypothesis_id": hid, "p_value": p},
    )


def test_health_and_contract_endpoints(client):
    h = client.get("/health").json()
    assert h["status"] == "ok"
    contract = client.get("/contract").json()
    assert contract["rule"] == "LORD3"
    assert h["contract_fingerprint"] == contract["fingerprint"]


def test_end_to_end_online_flow_matches_official_sample(client):
    run = client.post("/runs", json={"max_decisions": 10}).json()
    rid = run["run_id"]
    outcomes = []
    for i, p in enumerate([1e-7, 0.1, 0.00025, 0.07], start=1):
        resp = _submit(client, rid, f"H{i}", p)
        assert resp.status_code == 201
        record = resp.json()
        outcomes.append(record["rejected"])
        # Threshold is returned alongside the decision and stays stable for
        # the same trajectory position.
        assert isinstance(record["threshold"], float)
        assert record["row_hash"]
    assert outcomes == [True, False, True, False]

    history = client.get(f"/runs/{rid}/decisions").json()["decisions"]
    assert [d["hypothesis_id"] for d in history] == ["H1", "H2", "H3", "H4"]

    snapshot = client.get(f"/runs/{rid}").json()
    assert snapshot["state"]["last_index"] == 4
    assert snapshot["state"]["tau"] == 3

    audit = client.post(f"/runs/{rid}/audit").json()
    assert audit["ok"] is True


@pytest.mark.parametrize(
    "p_value", [-0.1, 1.1, "half", None, True]
)
def test_invalid_p_value_returns_422_input_category(client, p_value):
    rid = client.post("/runs", json={}).json()["run_id"]
    resp = _submit(client, rid, "H1", p_value)
    assert resp.status_code == 422
    err = resp.json()["error"]
    # String/None/bool fail request validation; bad floats fail the kernel,
    # but every case stays in the INPUT category.
    assert err["category"] == "INPUT"


def test_unknown_field_is_rejected(client):
    rid = client.post("/runs", json={}).json()["run_id"]
    resp = client.post(
        f"/runs/{rid}/decisions",
        json={"hypothesis_id": "H1", "p_value": 0.5, "extra": 1},
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "INPUT"


def test_missing_body_is_422(client):
    rid = client.post("/runs", json={}).json()["run_id"]
    resp = client.post(f"/runs/{rid}/decisions", json={})
    assert resp.status_code == 422


def test_duplicate_hypothesis_returns_409_state_conflict(client):
    rid = client.post("/runs", json={}).json()["run_id"]
    assert _submit(client, rid, "DUP", 0.4).status_code == 201
    resp = _submit(client, rid, "DUP", 1e-12)
    assert resp.status_code == 409
    err = resp.json()["error"]
    assert err["category"] == "STATE_CONFLICT"
    assert err["code"] == "E1021"
    # Original decision is untouched.
    row = client.get(f"/runs/{rid}/decisions").json()["decisions"][0]
    assert row["p_value"] == 0.4 and row["rejected"] is False


def test_unknown_run_returns_404(client):
    resp = _submit(client, "missing", "H1", 0.5)
    assert resp.status_code == 404
    assert resp.json()["error"]["category"] == "NOT_FOUND"
    assert client.post("/runs/missing/audit").status_code == 404


def test_run_limit_returns_507(client):
    rid = client.post("/runs", json={"max_decisions": 2}).json()["run_id"]
    _submit(client, rid, "H1", 0.9)
    _submit(client, rid, "H2", 0.9)
    resp = _submit(client, rid, "H3", 0.9)
    assert resp.status_code == 507
    assert resp.json()["error"]["category"] == "RESOURCE_EXHAUSTED"


def test_create_run_limit_validation(client):
    assert client.post("/runs", json={"max_decisions": 0}).status_code == 422
    assert client.post(
        "/runs", json={"max_decisions": 10 ** 9}
    ).status_code == 422


def test_pagination_query_validation(client):
    rid = client.post("/runs", json={}).json()["run_id"]
    resp = client.get(f"/runs/{rid}/decisions?limit=0")
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "E1005"


def test_tampered_evidence_audit_reports_failure(client):
    rid = client.post("/runs", json={}).json()["run_id"]
    _submit(client, rid, "H1", 0.4)
    store = client.app.state.store
    with store._conn:  # noqa: SLF001
        store._conn.execute(
            "UPDATE decisions SET p_value=1e-12 WHERE run_id=? AND idx=1",
            (rid,),
        )
    audit = client.post(f"/runs/{rid}/audit").json()
    assert audit["ok"] is False
    assert audit["chain_ok"] is False


def test_runs_listed(client):
    rid = client.post("/runs", json={}).json()["run_id"]
    ids = client.get("/runs").json()["run_ids"]
    assert rid in ids
