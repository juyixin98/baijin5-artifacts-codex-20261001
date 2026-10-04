"""End-to-end API tests: full flow over HTTP, failure categories surfaced in
response bodies, request ids echoed, audit endpoint exposes decisions.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from commit_reveal.api.app import create_app
from commit_reveal.clock import ManualClock
from commit_reveal.config import Settings
from tests.conftest import (
    COMMIT_DEADLINE,
    PARTICIPANTS,
    REVEAL_DEADLINE,
    T0,
    synth_secret,
)
from tests.helpers import reference_impl as ref

ROUND_ID = "round-api"


@pytest.fixture
def api(tmp_path):
    clock = ManualClock(T0)
    settings = Settings(database_path=str(tmp_path / "api.db"))
    app = create_app(settings, clock=clock)
    client = TestClient(app)
    resp = client.post(
        "/rounds",
        json={
            "round_id": ROUND_ID,
            "participants": list(PARTICIPANTS),
            "commit_deadline": "2026-01-01T01:00:00+00:00",
            "reveal_deadline": "2026-01-01T02:00:00+00:00",
            "min_reveals": 2,
        },
    )
    assert resp.status_code == 201, resp.text
    return client, clock


def _commit(client, name):
    commitment = ref.commitment(
        ROUND_ID, name,
        bytes.fromhex(synth_secret(name, "value")),
        bytes.fromhex(synth_secret(name, "salt")),
    )
    return client.post(
        f"/rounds/{ROUND_ID}/commitments",
        json={"participant_id": name, "commitment": commitment},
    )


def _reveal(client, name, salt=None):
    return client.post(
        f"/rounds/{ROUND_ID}/reveals",
        json={
            "participant_id": name,
            "value": synth_secret(name, "value"),
            "salt": salt or synth_secret(name, "salt"),
        },
    )


def test_full_flow_over_http(api):
    client, clock = api
    for name in PARTICIPANTS:
        resp = _commit(client, name)
        assert resp.status_code == 201, resp.text
        assert resp.json()["decision"] == "COMMIT_ACCEPTED"
        assert "X-Request-ID" in resp.headers

    clock.set(COMMIT_DEADLINE + timedelta(minutes=1))
    for name in PARTICIPANTS:
        resp = _reveal(client, name)
        assert resp.status_code == 201, resp.text

    clock.set(REVEAL_DEADLINE + timedelta(minutes=1))
    resp = client.post(f"/rounds/{ROUND_ID}/finalize")
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["winner"] in PARTICIPANTS

    resp = client.get(f"/rounds/{ROUND_ID}/evidence")
    assert resp.status_code == 200
    assert resp.json()["evidence"]["seed"] == result["seed_hex"]

    resp = client.get(f"/rounds/{ROUND_ID}/audit")
    assert resp.status_code == 200
    events = [row["event"] for row in resp.json()["audit"]]
    assert events.count("commit") == 3
    assert events.count("reveal") == 3
    assert "finalize" in events


def test_wrong_salt_returns_category(api):
    client, clock = api
    assert _commit(client, "alice").status_code == 201
    clock.set(COMMIT_DEADLINE + timedelta(minutes=1))
    bad_salt = hashlib.sha256(b"fixture:salt:wrong").hexdigest()
    resp = _reveal(client, "alice", salt=bad_salt)
    assert resp.status_code == 422
    body = resp.json()["error"]
    assert body["category"] == "COMMITMENT_MISMATCH"
    assert body["request_id"]


def test_late_commitment_returns_category(api):
    client, clock = api
    clock.set(COMMIT_DEADLINE + timedelta(seconds=1))
    resp = _commit(client, "alice")
    assert resp.status_code == 409
    assert resp.json()["error"]["category"] == "LATE_COMMITMENT"


def test_unknown_round_returns_404(api):
    client, _ = api
    resp = client.post(
        "/rounds/nope/commitments",
        json={"participant_id": "alice", "commitment": "ab" * 32},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["category"] == "ROUND_NOT_FOUND"


def test_request_id_header_is_honored(api):
    client, _ = api
    resp = client.get(f"/rounds/{ROUND_ID}", headers={"X-Request-ID": "req-123"})
    assert resp.status_code == 200
    assert resp.headers["X-Request-ID"] == "req-123"
