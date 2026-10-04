"""HTTP API tests: full flow, structured errors, audit trail, meta."""

from tests.conftest import TEST_PARAMS, run_logger


def _create_batch(client, label="api-batch"):
    resp = client.post(
        "/batches",
        json={
            "label": label,
            "key_size": 1024,
            "max_plaintext_abs": TEST_PARAMS.max_plaintext_abs,
            "max_coefficient_abs": TEST_PARAMS.max_coefficient_abs,
            "max_aggregate_abs": TEST_PARAMS.max_aggregate_abs,
        },
    )
    assert resp.status_code == 201, resp.text
    return resp.json()["batch"]


def _encrypt(client, batch, value):
    """Client-side encryption via the service helper (local fixture)."""
    service = client.app.state.service
    return service.client_encrypt(batch["batch_id"], value)


def test_full_flow_over_http(api_client, run_id):
    batch = _create_batch(api_client)
    values, weights = [3, -2, 7], [2, 5, -1]  # hand-computed: -11
    for i, (v, w) in enumerate(zip(values, weights)):
        resp = api_client.post(
            f"/batches/{batch['batch_id']}/contributions",
            json={
                "participant_id": f"p{i}",
                "key_id": batch["key_id"],
                "ciphertext": str(_encrypt(api_client, batch, v)),
                "coefficient": w,
                "plaintext_fixture": v,
            },
        )
        assert resp.status_code == 201, resp.text

    resp = api_client.post(f"/batches/{batch['batch_id']}/aggregate")
    assert resp.status_code == 200, resp.text
    agg = resp.json()["aggregate"]
    assert agg["contribution_count"] == 3
    assert len(agg["steps"]) == 3  # per-contribution computation steps

    resp = api_client.post(f"/batches/{batch['batch_id']}/decrypt")
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    run_logger.info(
        "CASE api_full_flow run_id=%s expected=-11 actual=%s",
        run_id, result["plaintext"],
    )
    assert result["plaintext"] == -11

    resp = api_client.post(f"/batches/{batch['batch_id']}/verify")
    assert resp.status_code == 200, resp.text
    verification = resp.json()["verification"]
    assert verification["status"] == "PASS"
    assert verification["expected_plaintext"] == -11

    resp = api_client.get(f"/batches/{batch['batch_id']}/audit")
    assert resp.status_code == 200
    events = [row["event"] for row in resp.json()["audit"]]
    assert events == [
        "BATCH_CREATED",
        "CONTRIBUTION_ACCEPTED",
        "CONTRIBUTION_ACCEPTED",
        "CONTRIBUTION_ACCEPTED",
        "AGGREGATE_COMPUTED",
        "RESULT_DECRYPTED",
        "VERIFICATION_DONE",
    ]
    # Every audit row carries a run id for correlation.
    assert all(row["run_id"] for row in resp.json()["audit"])


def test_error_responses_carry_category(api_client):
    batch = _create_batch(api_client)
    ciphertext = _encrypt(api_client, batch, 1)

    # coefficient out of range -> structured 422
    resp = api_client.post(
        f"/batches/{batch['batch_id']}/contributions",
        json={
            "participant_id": "p0",
            "key_id": batch["key_id"],
            "ciphertext": str(ciphertext),
            "coefficient": TEST_PARAMS.max_coefficient_abs + 1,
        },
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["category"] == "COEFFICIENT_OUT_OF_RANGE"

    # key mismatch -> structured 400
    resp = api_client.post(
        f"/batches/{batch['batch_id']}/contributions",
        json={
            "participant_id": "p0",
            "key_id": "sha256:" + "0" * 64,
            "ciphertext": str(ciphertext),
            "coefficient": 1,
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "KEY_MISMATCH"

    # malformed ciphertext -> structured 400
    resp = api_client.post(
        f"/batches/{batch['batch_id']}/contributions",
        json={
            "participant_id": "p0",
            "key_id": batch["key_id"],
            "ciphertext": "not-a-number",
            "coefficient": 1,
        },
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "CIPHERTEXT_INVALID"

    # unknown batch -> structured 404
    resp = api_client.get("/batches/nope")
    assert resp.status_code == 404
    assert resp.json()["error"]["category"] == "BATCH_NOT_FOUND"

    # aggregate on empty batch -> structured 409
    resp = api_client.post(f"/batches/{batch['batch_id']}/aggregate")
    assert resp.status_code == 409
    assert resp.json()["error"]["category"] == "BATCH_STATE_INVALID"


def test_meta_reports_versions_and_scope(api_client):
    resp = api_client.get("/meta")
    assert resp.status_code == 200
    meta = resp.json()
    for key in (
        "service_version",
        "python_version",
        "phe_version",
        "fastapi_version",
        "cryptography_version",
    ):
        assert meta[key], key
    assert "ciphertext_addition" in meta["supported_operations"]
    assert "ciphertext_multiplication" in meta["unsupported_operations"]


def test_health(api_client):
    assert api_client.get("/health").json() == {"status": "ok"}
