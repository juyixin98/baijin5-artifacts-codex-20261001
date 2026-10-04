"""End-to-end API tests through FastAPI's TestClient.

Covers the HTTP surface: request ids, structured error categories, the full
commit -> freeze -> reveal -> finalize -> verify flow, and the audit trail.
"""

import time

import pytest
from fastapi.testclient import TestClient

from commit_reveal.app import create_app
from commit_reveal.config import Settings
from commit_reveal.crypto.commitment import compute_commitment

SECRETS = {
    "alice": {"random_value": "aa" * 32, "salt": "a1" * 16},
    "bob": {"random_value": "bb" * 32, "salt": "b2" * 16},
}


@pytest.fixture
def client(tmp_path):
    settings = Settings(db_path=str(tmp_path / "api-test.db"))
    return TestClient(create_app(settings))


def _create_round(client, round_id="api-round", commit_deadline=None,
                  reveal_deadline=None):
    now = int(time.time())
    resp = client.post("/rounds", json={
        "round_id": round_id,
        "participants": ["alice", "bob"],
        "commit_deadline": commit_deadline or now + 3600,
        "reveal_deadline": reveal_deadline or now + 7200,
    })
    assert resp.status_code == 201, resp.text
    return resp


def _commitment(round_id, pid):
    s = SECRETS[pid]
    return compute_commitment(round_id, pid, bytes.fromhex(s["random_value"]),
                              bytes.fromhex(s["salt"]))


def test_full_flow_over_http(client):
    _create_round(client)
    for pid in ("alice", "bob"):
        resp = client.post(f"/rounds/api-round/commitments",
                           json={"participant_id": pid,
                                 "commitment": _commitment("api-round", pid)})
        assert resp.status_code == 201, resp.text
        assert "X-Request-ID" in resp.headers

    # Commit set is frozen by time travel only in service tests; over HTTP we
    # create a round whose commit deadline is already past via a second round.
    now = int(time.time())
    _create_round(client, round_id="api-round-2",
                  commit_deadline=now + 1, reveal_deadline=now + 3600)
    for pid in ("alice", "bob"):
        client.post("/rounds/api-round-2/commitments",
                    json={"participant_id": pid,
                          "commitment": _commitment("api-round-2", pid)})
    time.sleep(1.1)  # let the commit deadline pass

    for pid in ("alice", "bob"):
        s = SECRETS[pid]
        resp = client.post("/rounds/api-round-2/reveals",
                           json={"participant_id": pid, **s})
        assert resp.status_code == 201, resp.text

    resp = client.post("/rounds/api-round-2/finalize")
    assert resp.status_code == 200, resp.text
    evidence = resp.json()
    assert evidence["draw"]["winner"] in {"alice", "bob"}

    resp = client.post("/rounds/api-round-2/verify")
    assert resp.status_code == 200
    assert resp.json()["valid"] is True

    resp = client.get("/rounds/api-round-2/evidence")
    assert resp.status_code == 200
    assert resp.json()["seed"] == evidence["seed"]


def test_error_response_carries_code_and_request_id(client):
    _create_round(client)
    resp = client.post("/rounds/api-round/commitments",
                       json={"participant_id": "mallory", "commitment": "ab" * 32})
    assert resp.status_code == 409
    body = resp.json()
    assert body["error"]["code"] == "UNKNOWN_PARTICIPANT"
    assert body["error"]["request_id"] == resp.headers["X-Request-ID"]


def test_unknown_round_is_404(client):
    resp = client.get("/rounds/nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "ROUND_NOT_FOUND"


def test_audit_trail_records_decisions(client):
    _create_round(client)
    client.post("/rounds/api-round/commitments",
                json={"participant_id": "mallory", "commitment": "ab" * 32})
    client.post("/rounds/api-round/commitments",
                json={"participant_id": "alice",
                      "commitment": _commitment("api-round", "alice")})
    resp = client.get("/rounds/api-round/audit")
    assert resp.status_code == 200
    events = resp.json()["events"]
    outcomes = {(e["event_type"], e["outcome"]) for e in events}
    assert ("commit", "REJECTED") in outcomes
    assert ("commit", "ACCEPTED") in outcomes
    assert all(e["request_id"] for e in events)
