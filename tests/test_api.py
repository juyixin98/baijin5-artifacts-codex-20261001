"""End-to-end HTTP API tests via FastAPI TestClient.

Assert concrete payloads and specific error codes; also verify the request
id is associated with both response and structured log.
"""

from __future__ import annotations

import logging

from tests.oracle import mine_reference


def _create_corpus(client, name, transactions):
    response = client.post("/corpora", json={"name": name, "transactions": transactions})
    assert response.status_code == 201, response.text
    return response.json()


def _closed_set(payload):
    return {
        frozenset(r["itemset"]): (r["support"], r["maximal"]) for r in payload["results"]
    }


def test_health_reports_versions(client):
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["kernel_version"]
    assert body["budget_unit"] == "dfs_node_visit"
    assert response.headers["X-Request-ID"]


def test_request_id_is_echoed_and_generated(client):
    response = client.get("/health", headers={"X-Request-ID": "rid-123"})
    assert response.headers["X-Request-ID"] == "rid-123"
    assert response.json()["request_id"] == "rid-123"


def test_create_corpus_reports_normalization(client):
    body = _create_corpus(client, "c", [["a", "a", "b"], [], ["a"]])
    assert body["transaction_count"] == 3
    assert body["item_count"] == 2
    assert body["empty_transaction_count"] == 1
    assert body["duplicate_item_occurrences"] == 1
    assert body["kernel_version"]


def test_create_corpus_validation_error_envelope(client):
    response = client.post("/corpora", json={"name": "", "transactions": []})
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert body["request_id"] == response.headers["X-Request-ID"]


def test_create_corpus_schema_error_is_422(client):
    response = client.post("/corpora", json={"name": "x"})  # missing transactions
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_unknown_corpus_and_job_are_404_with_codes(client):
    r1 = client.post("/corpora/missing/jobs", json={"min_support": 1})
    assert r1.status_code == 404
    assert r1.json()["error"]["code"] == "CORPUS_NOT_FOUND"

    r2 = client.post("/jobs/missing/advance", json={"budget": 10})
    assert r2.status_code == 404
    assert r2.json()["error"]["code"] == "JOB_NOT_FOUND"


def test_min_support_boundary_over_http(client):
    _create_corpus(client, "b", [["a"], ["b"]])
    cid = client.get("/corpora").json()[0]["corpus_id"]

    ok = client.post(f"/corpora/{cid}/jobs", json={"min_support": 2, "budget": 50})
    assert ok.status_code == 201
    assert ok.json()["status"] == "COMPLETED"

    too_high = client.post(f"/corpora/{cid}/jobs", json={"min_support": 3})
    assert too_high.status_code == 400
    assert too_high.json()["error"]["code"] == "MIN_SUPPORT_INVALID"

    zero = client.post(f"/corpora/{cid}/jobs", json={"min_support": 0})
    assert zero.status_code == 422  # rejected at schema level (ge=1)


def test_budget_slice_and_resume_matches_oracle(client):
    transactions = [
        ["a", "b", "c"], ["a", "b", "d"], ["a", "c"], ["b", "c", "d"], ["a", "d"],
    ]
    _create_corpus(client, "mine", transactions)
    cid = client.get("/corpora").json()[0]["corpus_id"]

    created = client.post(
        f"/corpora/{cid}/jobs", json={"min_support": 2, "budget": 1}
    ).json()
    assert created["status"] == "RUNNING"
    assert created["partial"] is True
    # Partial results must never carry finalized maximal flags.
    assert all(r["maximal"] is False for r in created["results"])
    accumulated = [tuple(r["itemset"]) for r in created["results"]]

    job_id = created["job_id"]
    rounds = 0
    while created["status"] != "COMPLETED":
        created = client.post(f"/jobs/{job_id}/advance", json={"budget": 1}).json()
        keys = [tuple(r["itemset"]) for r in created["results"]]
        # Monotonic, no duplicates across resumes.
        assert keys[: len(accumulated)] == accumulated
        assert len(keys) == len(set(keys))
        accumulated = keys
        rounds += 1
        assert rounds < 100

    _, closed_ref, maximal_ref = mine_reference(transactions, 2)
    got = _closed_set(created)
    assert set(got) == set(closed_ref)
    for key, (support, maximal) in got.items():
        assert support == closed_ref[key]
        assert maximal == (key in maximal_ref)
    assert created["total_budget_used"] == created["nodes_visited"]


def test_empty_corpus_cannot_start_job_and_can_be_inspected(client):
    created = _create_corpus(client, "empty", [])
    assert created["transaction_count"] == 0
    assert created["item_count"] == 0
    # No positive integer threshold is satisfiable: explicit failure class.
    response = client.post(
        f"/corpora/{created['corpus_id']}/jobs", json={"min_support": 1}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MIN_SUPPORT_INVALID"


def test_query_labels_infrequent_when_threshold_given(client):
    _create_corpus(client, "freq", [["a"], ["b"]])
    cid = client.get("/corpora").json()[0]["corpus_id"]
    body = client.post(
        f"/corpora/{cid}/query", json={"items": ["a"], "min_support": 2}
    ).json()
    assert body["support"] == 1
    assert body["is_frequent"] is False
    assert body["transaction_count"] == 2
    assert body["is_closed"] is True


def test_large_budget_rejected_with_category(client):
    _create_corpus(client, "z", [["a"]])
    cid = client.get("/corpora").json()[0]["corpus_id"]
    response = client.post(
        f"/corpora/{cid}/jobs", json={"min_support": 1, "budget": 999_999}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BUDGET_INVALID"


def test_independent_query_endpoint(client):
    transactions = [["a", "b", "c"], ["a", "b", "c"], ["c"]]
    _create_corpus(client, "q", transactions)
    cid = client.get("/corpora").json()[0]["corpus_id"]

    # Non-closed {a}: closure {a,b,c}, same support 2.
    body = client.post(
        f"/corpora/{cid}/query", json={"items": ["a"], "min_support": 2}
    ).json()
    assert body["support"] == 2
    assert body["is_frequent"] is True
    assert body["closure"] == ["a", "b", "c"]
    assert body["closure_support"] == 2
    assert body["is_closed"] is False
    assert body["same_support_supersets"] == [["a", "b", "c"]]
    assert "independent SQL index" in body["computed_by"]

    # Closed {c} at support 3.
    body_c = client.post(f"/corpora/{cid}/query", json={"items": ["c"]}).json()
    assert body_c["is_closed"] is True
    assert body_c["is_frequent"] is None  # no threshold supplied
    assert body_c["same_support_supersets"] == []


def test_query_duplicate_items_collapsed_and_unknown_rejected(client):
    _create_corpus(client, "q2", [["a", "b"], ["a"]])
    cid = client.get("/corpora").json()[0]["corpus_id"]

    body = client.post(f"/corpora/{cid}/query", json={"items": ["a", "a"]}).json()
    assert body["itemset"] == ["a"]
    assert body["support"] == 2

    missing = client.post(f"/corpora/{cid}/query", json={"items": ["a", "zzz"]})
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "ITEM_NOT_IN_DOMAIN"

    empty = client.post(f"/corpora/{cid}/query", json={"items": []})
    assert empty.status_code == 400
    assert empty.json()["error"]["code"] == "VALIDATION_ERROR"


def test_structured_logs_carry_request_id_and_steps(client, caplog):
    with caplog.at_level(logging.INFO, logger="cfim"):
        client.get("/health", headers={"X-Request-ID": "rid-log"})
    steps = {getattr(r, "step", None) for r in caplog.records}
    assert "request.received" in steps
    assert "request.completed" in steps
    correlated = [r for r in caplog.records if getattr(r, "request_id", None)]
    assert correlated, "every request-scoped log line must carry the request id"
    assert all(r.request_id == "rid-log" for r in correlated)
    assert all(getattr(r, "location") for r in correlated)


def test_failure_log_line_is_structured_separately(client, caplog):
    with caplog.at_level(logging.WARNING, logger="cfim"):
        client.post("/corpora/nope/jobs", json={"min_support": 1})
    failure_records = [r for r in caplog.records if getattr(r, "failure", None)]
    assert failure_records, "a failing request must emit a structured failure line"
    assert failure_records[0].failure["code"] == "CORPUS_NOT_FOUND"
    assert getattr(failure_records[0], "location")


def test_job_results_persist_across_app_restart(settings):
    # First app instance writes a partial job; a fresh instance over the same
    # SQLite file resumes it, proving state durability, not just in-memory.
    from fastapi.testclient import TestClient

    from cfim.service import create_app
    from cfim.store import Store

    app1 = create_app(settings=settings, store=Store(settings.db_path))
    with TestClient(app1) as c1:
        c1.post("/corpora", json={"name": "persist", "transactions": [["a", "b"], ["a"], ["a", "b"]]})
        cid = c1.get("/corpora").json()[0]["corpus_id"]
        job = c1.post(f"/corpora/{cid}/jobs", json={"min_support": 2, "budget": 1}).json()
        job_id = job["job_id"]
    app1.state.store.close()

    app2 = create_app(settings=settings, store=Store(settings.db_path))
    with TestClient(app2) as c2:
        resumed = c2.post(f"/jobs/{job_id}/advance", json={"budget": 100}).json()
        assert resumed["status"] == "COMPLETED"
        got = {frozenset(r["itemset"]): r["support"] for r in resumed["results"]}
        _, closed_ref, _ = mine_reference([["a", "b"], ["a"], ["a", "b"]], 2)
        assert got == dict(closed_ref)
    app2.state.store.close()
