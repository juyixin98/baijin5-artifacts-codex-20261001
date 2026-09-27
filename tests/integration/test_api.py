"""End-to-end HTTP tests driving the FastAPI application via TestClient."""

from __future__ import annotations

import json

import pytest

from tests.conftest import load_fixture


@pytest.fixture
def seeded(client) -> object:
    """Load the birds fixture through the public API."""
    data = load_fixture("birds.json")["cases"][0]
    client.post(f"/cases/{data['case_id']}/theory", json=data["theory"])
    client.put(
        f"/cases/{data['case_id']}/evidence",
        json={"literals": data["evidence"]},
    )
    return client


class TestHealthAndCases:
    def test_health(self, client) -> None:
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}

    def test_full_case_workflow_and_snapshot(self, client) -> None:
        theory = {
            "rules": [
                {"id": "rb", "kind": "default", "body": ["bird(X)"],
                 "head": "flies(X)"}
            ],
            "priorities": [],
        }
        assert client.post("/cases/x/theory", json=theory).status_code == 200
        r = client.put("/cases/x/evidence", json={"literals": ["bird(tweety)"]})
        assert r.json()["added"] == 1
        snap = client.get("/cases/x").json()
        assert snap["evidence"] == ["bird(tweety)"]
        assert snap["theory"]["rules"][0]["id"] == "rb"
        assert "x" in {
            c["case_id"] for c in client.get("/cases").json()["cases"]
        }


class TestQueryEndpoint:
    def test_proved_chain_for_plain_bird(self, seeded) -> None:
        r = seeded.post("/cases/birds/query", json={"literal": "flies(tweety)"})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "proved"
        assert body["flags"]["supported"] is True
        support = body["chains"]["support"]
        assert len(support) == 1
        assert support[0]["steps"][-1]["rule_id"] == "r_bird_flies"
        assert body["chains"]["defeat"] == []
        assert body["chains"]["pending"] == []
        assert body["run_id"].startswith("run-")

    def test_refuted_chain_names_penguin_attacker(self, seeded) -> None:
        r = seeded.post("/cases/birds/query", json={"literal": "flies(polly)"})
        body = r.json()
        assert body["status"] == "refuted"
        defeats = body["chains"]["defeat"]
        assert len(defeats) == 1  # the single bird-rule chain for polly
        assert defeats[0]["attacker_top_rule"] == "r_penguin_not"

    def test_unknown_when_no_evidence(self, seeded) -> None:
        r = seeded.post("/cases/birds/query", json={"literal": "flies(ghost)"})
        body = r.json()
        assert body["status"] == "unknown"
        assert body["chains"] == {
            "support": [],
            "defeat": [],
            "pending": [],
        }

    def test_nixon_conflict_is_served(self, client) -> None:
        data = load_fixture("nixon_diamond.json")["cases"][0]
        client.post("/cases/nixon/theory", json=data["theory"])
        client.put(
            "/cases/nixon/evidence", json={"literals": data["evidence"]}
        )
        r = client.post(
            "/cases/nixon/query", json={"literal": "pacifist(nixon)"}
        )
        body = r.json()
        assert body["status"] == "conflict"
        assert body["flags"]["supported"] is True
        assert body["flags"]["opposite_supported"] is True
        pending = body["chains"]["pending"]
        assert pending and "不可比" in pending[0]["reason"]


class TestEvaluateEndpoint:
    def test_full_conclusion_table(self, seeded) -> None:
        r = seeded.post("/cases/birds/evaluate")
        table = {c["literal"]: c["status"] for c in r.json()["conclusions"]}
        assert table["flies(tweety)"] == "proved"
        assert table["flies(polly)"] == "refuted"
        assert table["-flies(polly)"] == "proved"


class TestFailureCategoriesOverHttp:
    def test_malformed_literal_is_422(self, seeded) -> None:
        r = seeded.post("/cases/birds/query", json={"literal": "flies("})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "invalid_input"

    def test_priority_cycle_is_409(self, client) -> None:
        data = load_fixture("priority_cycle.json")["cases"][0]
        # uploading a cyclic theory is itself rejected as a state conflict
        r = client.post("/cases/cyc/theory", json=data["theory"])
        assert r.status_code == 409
        assert r.json()["error"]["code"] == "state_conflict"
        assert r.json()["error"]["details"]["cycle"]

    def test_query_without_theory_is_422(self, client) -> None:
        r = client.post("/cases/nothing/query", json={"literal": "q(t)"})
        assert r.status_code == 422
        assert r.json()["error"]["code"] == "invalid_input"

    def test_resource_exhaustion_is_509(self) -> None:
        # Dedicated app with a tiny grounding budget: a three-variable rule
        # over 8 constants needs 8^3 = 512 ground instances > 50.
        from fastapi.testclient import TestClient

        from defeasible.api import create_app
        from defeasible.config import Settings
        from defeasible.logging import RunLogger
        from defeasible.storage import EvidenceStore

        app = create_app(
            Settings(
                db_path=":memory:",
                log_path=":memory:",
                max_ground_rules=50,
            ),
            store=EvidenceStore(":memory:"),
            logger=RunLogger(":memory:"),
        )
        with TestClient(app) as small:
            small.post(
                "/cases/big/theory",
                json={
                    "rules": [
                        {"id": "r", "kind": "default",
                         "body": ["c(X)", "c(Y)", "c(Z)"],
                         "head": "q(X, Y, Z)"}
                    ],
                    "priorities": [],
                },
            )
            literals = [f"c(k{i})" for i in range(8)]
            small.put("/cases/big/evidence", json={"literals": literals})
            r = small.post("/cases/big/evaluate")
        assert r.status_code == 509
        assert r.json()["error"]["code"] == "resource_exhausted"


class TestReplay:
    def test_run_is_replayable_with_intermediate_state(self, seeded) -> None:
        q = seeded.post(
            "/cases/birds/query", json={"literal": "flies(tweety)"}
        ).json()
        run_id = q["run_id"]
        r = seeded.get(f"/runs/{run_id}")
        assert r.status_code == 200
        events = [rec["event"] for rec in r.json()["records"]]
        assert events == ["start", "intermediate", "result"]
        inter = r.json()["records"][1]
        assert inter["ground_rule_count"] >= 1
        assert inter["status_counts"]["proved"] >= 1

    def test_unknown_run_is_422(self, seeded) -> None:
        r = seeded.get("/runs/run-doesnotexist")
        assert r.status_code == 422

    def test_failed_run_logs_failure_category(self, client) -> None:
        # malformed literal still opens a run and records the error
        client.post("/cases/b/theory", json={
            "rules": [{"id": "r", "kind": "default",
                       "body": ["p(X)"], "head": "q(X)"}],
            "priorities": [],
        })
        client.put("/cases/b/evidence", json={"literals": ["p(t)"]})
        client.post("/cases/b/query", json={"literal": "bad literal!!"})
        # fetch most recent run from in-memory logger attached to the app
        records = client._logger.records()  # type: ignore[attr-defined]
        error_records = [r for r in records if r["event"] == "error"]
        assert error_records
        assert error_records[-1]["error_code"] == "invalid_input"
