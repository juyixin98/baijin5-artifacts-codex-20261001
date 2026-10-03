"""End-to-end API tests: response values, provenance, error categories."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import create_app

# Hand-computed reference (see tests/test_models.py): JC69 at p = 0.10.
JC69_P010 = 0.10732563273050497


@pytest.fixture()
def client(tmp_path):
    app = create_app(db_path=str(tmp_path / "runs.sqlite3"))
    # raise_server_exceptions=False so the 500 handler's body is asserted
    # instead of the exception propagating out of the test client.
    return TestClient(app, raise_server_exceptions=False)


def _payload(**overrides):
    payload = {
        "sequences": [
            {"id": "ref", "sequence": "A" * 100},
            {"id": "var", "sequence": "G" * 6 + "C" * 4 + "A" * 90},
        ],
        "model": "jc69",
        "bootstrap": {"replicates": 200, "confidence": 0.95, "seed": 42},
    }
    payload.update(overrides)
    return payload


class TestHappyPath:
    def test_distance_values_and_provenance(self, client):
        resp = client.post("/v1/distances", json=_payload())
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["alignment_length"] == 100

        pair = body["results"][0]
        assert pair["n_valid_sites"] == 100
        assert pair["n_transition"] == 6
        assert pair["n_transversion"] == 4
        assert pair["p_distance"] == pytest.approx(0.10)
        assert pair["distance"] == pytest.approx(JC69_P010, rel=1e-12)
        assert pair["status"] == "ok"
        assert pair["rationale"]  # decision reasons recorded
        assert pair["bootstrap"]["seed"] == 42
        assert pair["bootstrap"]["ci_low"] < pair["distance"] < pair["bootstrap"]["ci_high"]

        # Provenance: the run is replayable by run_id, with intermediate state.
        run = client.get(f"/v1/runs/{body['run_id']}")
        assert run.status_code == 200
        stored = run.json()
        assert stored["model"] == "jc69"
        assert stored["seed"] == 42
        stored_pair = stored["pair_results"][0]
        assert stored_pair["n_valid_sites"] == 100
        assert stored_pair["n_transition"] == 6
        assert "JC69" in stored_pair["rationale_json"]

    def test_fasta_input_equivalent(self, client):
        fasta = ">ref\n" + "A" * 100 + "\n>var\n" + "G" * 6 + "C" * 4 + "A" * 90 + "\n"
        resp = client.post("/v1/distances", json={"fasta": fasta, "model": "jc69"})
        assert resp.status_code == 200
        assert resp.json()["results"][0]["distance"] == pytest.approx(JC69_P010, rel=1e-12)

    def test_all_missing_pair_is_undefined_not_error(self, client):
        resp = client.post("/v1/distances", json={
            "sequences": [
                {"id": "x", "sequence": "NNNN----"},
                {"id": "y", "sequence": "----NNNN"},
            ],
            "model": "k2p",
        })
        assert resp.status_code == 200
        pair = resp.json()["results"][0]
        assert pair["status"] == "undefined"
        assert pair["distance"] is None
        assert pair["n_valid_sites"] == 0

    def test_saturated_pair_reported_in_result(self, client):
        resp = client.post("/v1/distances", json={
            "sequences": [
                {"id": "a", "sequence": "A" * 100},
                {"id": "b", "sequence": "G" * 40 + "C" * 40 + "A" * 20},
            ],
            "model": "jc69",
        })
        assert resp.status_code == 200
        pair = resp.json()["results"][0]
        assert pair["status"] == "saturated"
        assert pair["distance"] is None
        assert pair["p_distance"] == pytest.approx(0.80)


class TestIdempotencyAndStateConflict:
    def test_same_run_id_same_payload_replays(self, client):
        r1 = client.post("/v1/distances", json=_payload(run_id="run-001"))
        r2 = client.post("/v1/distances", json=_payload(run_id="run-001"))
        assert r1.status_code == r2.status_code == 200
        assert r1.json()["run_id"] == r2.json()["run_id"] == "run-001"
        assert r2.json().get("replayed") is True
        assert r1.json()["results"] == r2.json()["results"]

    def test_same_run_id_different_payload_is_conflict(self, client):
        client.post("/v1/distances", json=_payload(run_id="run-002"))
        conflict = _payload(run_id="run-002", model="k2p")
        resp = client.post("/v1/distances", json=conflict)
        assert resp.status_code == 409
        err = resp.json()["error"]
        assert err["category"] == "state_conflict"
        assert err["code"] == "run_id_payload_conflict"


class TestErrorCategories:
    def test_unequal_lengths_is_input_validation(self, client):
        resp = client.post("/v1/distances", json={
            "sequences": [
                {"id": "a", "sequence": "ACGT"},
                {"id": "b", "sequence": "ACG"},
            ],
            "model": "p",
        })
        assert resp.status_code == 422
        assert resp.json()["error"]["category"] == "input_validation"

    def test_ambiguous_input_rejected(self, client):
        resp = client.post("/v1/distances", json={
            "sequences": [{"id": "a", "sequence": "AC"}, {"id": "b", "sequence": "AC"}],
            "fasta": ">a\nAC\n>b\nAC\n",
            "model": "p",
        })
        assert resp.status_code == 422
        assert resp.json()["error"]["code"] == "ambiguous_input"

    def test_unknown_model_rejected_by_schema(self, client):
        resp = client.post("/v1/distances", json={
            "sequences": [{"id": "a", "sequence": "AC"}, {"id": "b", "sequence": "AC"}],
            "model": "tn93",
        })
        assert resp.status_code == 422

    def test_too_many_replicates_is_resource_exhausted(self, client):
        resp = client.post("/v1/distances", json=_payload(
            bootstrap={"replicates": 100_001, "confidence": 0.95, "seed": 0}))
        assert resp.status_code == 413
        assert resp.json()["error"]["category"] == "resource_exhausted"

    def test_overlong_sequence_is_resource_exhausted(self, client):
        resp = client.post("/v1/distances", json={
            "sequences": [
                {"id": "a", "sequence": "A" * 1_000_001},
                {"id": "b", "sequence": "AC"},
            ],
            "model": "p",
        })
        assert resp.status_code == 413
        assert resp.json()["error"]["category"] == "resource_exhausted"

    def test_unknown_run_id_is_404(self, client):
        resp = client.get("/v1/runs/does-not-exist")
        assert resp.status_code == 404
        assert resp.json()["error"]["code"] == "run_not_found"

    def test_unexpected_failure_is_computation_failure(self, client, monkeypatch):
        import app.api as api_module

        def boom(payload, store):
            raise RuntimeError("simulated crash")

        monkeypatch.setattr(api_module, "compute_distances", boom)
        resp = client.post("/v1/distances", json=_payload())
        assert resp.status_code == 500
        err = resp.json()["error"]
        assert err["category"] == "computation_failure"
        assert err["code"] == "unexpected_failure"
