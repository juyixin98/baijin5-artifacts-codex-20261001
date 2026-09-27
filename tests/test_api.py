"""End-to-end HTTP API tests: concrete status codes, error categories,
full chaining flow through HTTP, and evidence retrieval."""

from __future__ import annotations

import logging
import os

import pytest

# Isolated evidence DB per test process run.
os.environ.setdefault("RETE_DB_PATH", ":memory:")

from fastapi.testclient import TestClient  # noqa: E402

from rete_api.app import app  # noqa: E402

log = logging.getLogger("tests.api")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def session(client):
    r = client.post("/sessions", json={})
    assert r.status_code == 201, r.text
    sid = r.json()["session_id"]
    log.info("api test session=%s", sid)
    return sid


def test_health_reports_version(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["engine_version"]


def test_full_rule_lifecycle_and_chaining(client, session):
    rules = [
        {"name": "assign", "salience": 10,
         "conditions": [{"kind": "task", "fields": ["?tid", "queued"]},
                        {"kind": "worker", "fields": ["?wid", "free"]}],
         "actions": [
             {"op": "retract", "kind": "task",
              "fields": ["?tid", "queued"]},
             {"op": "assert", "kind": "task",
              "fields": ["?tid", "assigned", "?wid"]},
             {"op": "emit", "tag": "assigned",
              "fields": ["?tid", "?wid"]}]},
        {"name": "notify", "salience": 0,
         "conditions": [{"kind": "task", "fields": ["?tid", "assigned", "?wid"]}],
         "actions": [{"op": "emit", "tag": "notify", "fields": ["?tid", "?wid"]}]},
    ]
    for rule in rules:
        r = client.post(f"/sessions/{session}/rules", json=rule)
        assert r.status_code == 201, r.text
        assert r.json()["rule"] == rule["name"]

    assert client.post(f"/sessions/{session}/facts",
                       json={"kind": "worker", "fields": ["w1", "free"]}
                       ).status_code == 201
    assert client.post(f"/sessions/{session}/facts",
                       json={"kind": "task", "fields": ["t1", "queued"]}
                       ).status_code == 201

    matches = client.get(f"/sessions/{session}/matches").json()
    assert matches["count"] == 1
    assert matches["matches"][0]["rule"] == "assign"
    assert matches["matches"][0]["fact_keys"] == [
        ["task", "t1", "queued"], ["worker", "w1", "free"]]

    run = client.post(f"/sessions/{session}/run", json={"max_cycles": 10})
    assert run.status_code == 200
    body = run.json()
    assert body["status"] == "quiescent", body
    assert [f["rule"] for f in body["fired"]] == ["assign", "notify"]
    assert body["fired"][0]["fact_keys"][0][0] in ("task", "worker")
    assert [e["fields"] for e in body["emitted"]
            if e["tag"] == "notify"] == [["t1", "w1"]]

    facts = client.get(f"/sessions/{session}/facts").json()["facts"]
    keys = sorted((f["kind"], *f["fields"]) for f in facts)
    assert keys == [("task", "t1", "assigned", "w1"),
                    ("worker", "w1", "free")]

    # Evidence retrieval via the API.
    runs = client.get(f"/sessions/{session}/runs").json()["runs"]
    assert len(runs) == 1 and runs[0]["status"] == "quiescent"
    run_id = body["run_id"]
    acts = client.get(
        f"/sessions/{session}/runs/{run_id}/activations").json()
    assert [a["rule"] for a in acts["activations"]] == ["assign", "notify"]
    log.info("api chaining flow + evidence OK, run_id=%s", run_id)


def test_retract_via_api_drops_dependent_match(client, session):
    client.post(f"/sessions/{session}/rules", json={
        "name": "j",
        "conditions": [{"kind": "a", "fields": ["?x"]},
                       {"kind": "b", "fields": ["?x"]}],
        "actions": []})
    client.post(f"/sessions/{session}/facts", json={"kind": "a", "fields": [1]})
    client.post(f"/sessions/{session}/facts", json={"kind": "b", "fields": [1]})
    assert client.get(f"/sessions/{session}/matches").json()["count"] == 1
    r = client.request("DELETE", f"/sessions/{session}/facts",
                       json={"kind": "a", "fields": [1]})
    assert r.status_code == 200 and r.json()["removed"] is True
    assert client.get(f"/sessions/{session}/matches").json()["count"] == 0


def test_error_categories_are_explicit(client, session):
    # 404 unknown session
    r = client.get("/sessions/no-such-session/matches")
    assert r.status_code == 404
    assert r.json()["detail"]["category"] == "session_not_found"

    # 422 malformed rule (unbound variable)
    r = client.post(f"/sessions/{session}/rules", json={
        "name": "bad", "conditions": [{"kind": "f", "fields": ["?x"]}],
        "tests": [[">", "?y", 1]]})
    assert r.status_code == 422
    assert r.json()["detail"]["category"] == "rule_error"

    # 409 duplicate rule
    ok_rule = {"name": "dup", "conditions": [{"kind": "f", "fields": []}]}
    assert client.post(f"/sessions/{session}/rules", json=ok_rule
                       ).status_code == 201
    r = client.post(f"/sessions/{session}/rules", json=ok_rule)
    assert r.status_code == 409
    assert r.json()["detail"]["category"] == "duplicate_rule"

    # 404 retract unknown fact
    r = client.request("DELETE", f"/sessions/{session}/facts",
                       json={"kind": "ghost", "fields": []})
    assert r.status_code == 404
    assert r.json()["detail"]["category"] == "fact_not_found"

    # 404 unknown rule on matches filter
    r = client.get(f"/sessions/{session}/matches", params={"rule": "nope"})
    assert r.status_code == 404
    assert r.json()["detail"]["category"] == "rule_not_found"
    log.info("all API error categories asserted explicitly")


def test_cycle_limit_is_reported_not_hidden(client, session):
    client.post(f"/sessions/{session}/rules", json={
        "name": "flap",
        "conditions": [{"kind": "f", "fields": ["?x"]}],
        "actions": [
            {"op": "retract", "kind": "f", "fields": ["?x"]},
            {"op": "assert", "kind": "f", "fields": ["?x"]}]})
    client.post(f"/sessions/{session}/facts",
                json={"kind": "f", "fields": [1]})
    r = client.post(f"/sessions/{session}/run", json={"max_cycles": 2})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "cycle_limit_reached"
    assert body["cycles"] == 2 and body["agenda_remaining"] >= 1

    # invalid bound rejected at validation layer (pydantic ge=1 -> 422)
    bad = client.post(f"/sessions/{session}/run", json={"max_cycles": 0})
    assert bad.status_code == 422
    log.info("cycle limit surfaced with explicit status over HTTP")


def test_duplicate_fact_over_http_is_created_false(client, session):
    payload = {"kind": "f", "fields": ["same"]}
    first = client.post(f"/sessions/{session}/facts", json=payload).json()
    second = client.post(f"/sessions/{session}/facts", json=payload).json()
    assert first["created"] is True and second["created"] is False
    assert first["wme_id"] == second["wme_id"]
    assert second["refcount"] == 2


def test_session_and_evidence_query_routes(client, session):
    # session metadata and listings
    client.post(f"/sessions/{session}/rules", json={
        "name": "r", "conditions": [{"kind": "f", "fields": ["?x"]}],
        "actions": [{"op": "assert", "kind": "g", "fields": ["?x"]}]})
    client.post(f"/sessions/{session}/facts", json={"kind": "f", "fields": ["v"]})
    run = client.post(f"/sessions/{session}/run", json={"max_cycles": 5}).json()

    meta = client.get(f"/sessions/{session}").json()
    assert meta["rules"] == ["r"] and meta["fact_count"] == 2
    assert client.get(f"/sessions/{session}/rules").json()["rules"] == ["r"]

    sessions = client.get("/sessions").json()["sessions"]
    assert any(s["session_id"] == session for s in sessions)

    # fact events scoped to the run, and to the session overall
    run_events = client.get(
        f"/sessions/{session}/fact-events",
        params={"run_id": run["run_id"]}).json()["events"]
    assert any(e["op"] == "insert" and e["kind"] == "g" for e in run_events)
    all_events = client.get(
        f"/sessions/{session}/fact-events").json()["events"]
    assert len(all_events) >= len(run_events)

    # single run fetch + missing run is an explicit 404
    assert client.get(
        f"/sessions/{session}/runs/{run['run_id']}").status_code == 200
    missing = client.get(f"/sessions/{session}/runs/no-such-run")
    assert missing.status_code == 404
    assert missing.json()["detail"]["category"] == "run_not_found"

    # deleting the session removes it from listings
    assert client.delete(f"/sessions/{session}").status_code == 204
    assert client.get(f"/sessions/{session}").status_code == 404
    log.info("session/evidence query routes verified")


def test_invalid_fact_payload_is_rejected_not_accepted(client, session):
    # Nested structure is not a JSON scalar fact field.
    r = client.post(f"/sessions/{session}/facts",
                    json={"kind": "f", "fields": [{"nested": True}]})
    assert r.status_code == 422
    log.info("non-scalar fact field rejected at validation layer")
