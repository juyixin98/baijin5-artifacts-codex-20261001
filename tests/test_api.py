"""End-to-end HTTP tests for the FastAPI verification service."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from autodiff.api.app import app
from autodiff.fixtures import SCENARIOS

client = TestClient(app)


def test_health_lists_scenarios():
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["service"] == "mini-autodiff"
    assert set(SCENARIOS) <= set(body["scenarios"])


def test_scenarios_endpoint():
    r = client.get("/scenarios")
    assert r.status_code == 200
    assert "broadcast_add" in r.json()["scenarios"]


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_every_fixture_scenario_accepted_over_http(scenario):
    r = client.post("/verify/gradients", json={"scenario": scenario})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ACCEPTED", body
    assert body["scenario"] == scenario
    assert len(body["leaves"]) >= 1
    # Every leaf report carries a concrete status; none are vague.
    for leaf in body["leaves"]:
        assert leaf["status"] == "ACCEPTED"
        assert leaf["shape"]
    # Diagnostics carry correlation identity on every record.
    request_id = body["request_id"]
    assert request_id
    for rec in body["diagnostics"]:
        assert rec["request_id"] == request_id
        assert rec["record_id"].startswith("rec-")
        assert rec["status"] in {"ACCEPTED", "REJECTED", "UNABLE"}


def test_request_id_is_echoed_and_propagated():
    r = client.post(
        "/verify/gradients",
        json={"scenario": "reduction", "request_id": "fixed-correlation-id"},
    )
    body = r.json()
    assert body["request_id"] == "fixed-correlation-id"
    assert all(
        d["request_id"] == "fixed-correlation-id" for d in body["diagnostics"]
    )


def test_unknown_scenario_is_rejected_not_500():
    r = client.post("/verify/gradients", json={"scenario": "does-not-exist"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "REJECTED"
    events = [d["event"] for d in body["diagnostics"]]
    assert "service.unknown_scenario" in events


def test_invalid_eps_returns_422():
    r = client.post(
        "/verify/gradients",
        json={"scenario": "reduction", "eps": -0.01},
    )
    assert r.status_code == 422


def test_inplace_mutation_is_rejected_with_category():
    r = client.post("/verify/inplace", json={"mutate": True})
    assert r.status_code == 200
    body = r.json()
    assert body["accepted"] is False
    assert body["failure_category"] == "stale_graph_version_mismatch"
    statuses = [(d["event"], d["status"]) for d in body["diagnostics"]]
    assert ("inplace.mutation_detected", "REJECTED") in statuses
    assert ("inplace.backward_refused", "REJECTED") in statuses


def test_inplace_clean_graph_accepted():
    r = client.post("/verify/inplace", json={"mutate": False})
    body = r.json()
    assert body["accepted"] is True
    assert body["failure_category"] is None
    assert any(d["event"] == "inplace.backward_ok" and d["status"] == "ACCEPTED"
               for d in body["diagnostics"])


def test_diagnostic_state_is_redacted_over_http():
    # The analytic-vs-numeric record includes arrays; large payloads must not
    # appear verbatim.  batched_matmul leaves are small, so force a check via
    # the inplace endpoint whose grad arrays are tiny and inspect shapes.
    r = client.post("/verify/gradients", json={"scenario": "matmul_mlp"})
    for rec in r.json()["diagnostics"]:
        for key, value in rec["state"].items():
            if isinstance(value, dict) and "shape" in value:
                # Array summaries: never raw value lists beyond a preview.
                if value["size"] > 8:
                    assert "preview" not in value
