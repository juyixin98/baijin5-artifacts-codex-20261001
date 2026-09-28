"""End-to-end HTTP tests via FastAPI's TestClient.

These cover request identity propagation, error-category -> HTTP-status
mapping, and the full tensor/view/op/graph/training/verify flows.
"""

from __future__ import annotations

import numpy as np
import pytest


def _create(client, data, dtype="float64", handle=None):
    body = {"data": data, "dtype": dtype}
    if handle is not None:
        body["handle"] = handle
    response = client.post("/api/v1/tensors", json=body)
    assert response.status_code == 201, response.text
    return response.json()["handle"]


class TestMeta:
    def test_health(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["version"] == "0.1.0"

    def test_openapi_documented(self, client):
        response = client.get("/openapi.json")
        assert response.status_code == 200
        paths = response.json()["paths"]
        assert "/api/v1/tensors" in paths


class TestRequestIdentity:
    def test_echoes_provided_request_id(self, client):
        response = client.get(
            "/health", headers={"X-Request-ID": "trace-xyz"})
        assert response.headers["X-Request-ID"] == "trace-xyz"

    def test_generates_request_id_when_absent(self, client):
        response = client.get("/health")
        generated = response.headers["X-Request-ID"]
        assert generated.startswith("req-")

    def test_error_body_includes_request_id(self, client):
        _create(client, [[1, 2]], "int64", "a")
        response = client.post(
            "/api/v1/tensors/a/slice", json={"index": [99]})
        assert response.json()["request_id"]


class TestTensorEndpoints:
    def test_create_describe_values(self, client):
        handle = _create(client, [[1, 2], [3, 4]], "int64")
        response = client.get(f"/api/v1/tensors/{handle}")
        description = response.json()["tensor"]
        assert description["shape"] == [2, 2]
        assert description["strides"] == [2, 1]
        assert description["c_contiguous"] is True
        values = client.get(f"/api/v1/tensors/{handle}/values").json()
        assert values["values"] == [[1, 2], [3, 4]]

    def test_transpose_then_reshape_copy_flag(self, client):
        _create(client, [[1, 2, 3], [4, 5, 6]], "int64", "a")
        transposed = client.post(
            "/api/v1/tensors/a/transpose", json={}).json()["handle"]
        response = client.post(
            f"/api/v1/tensors/{transposed}/reshape",
            json={"shape": [6], "order": "C"})
        body = response.json()
        assert body["copied"] is True
        assert body["tensor"]["storage_token"] != \
            client.get("/api/v1/tensors/a").json()["tensor"]["storage_token"]

    def test_slice_reverse_and_broadcast(self, client):
        _create(client, [1, 2, 3, 4], "int64", "v")
        response = client.post(
            "/api/v1/tensors/v/slice",
            json={"index": [[None, None, -1]]})
        assert response.json()["tensor"]["strides"] == [-1]

        _create(client, [[5], [6], [7]], "int64", "col")
        response = client.post(
            "/api/v1/tensors/col/broadcast", json={"shape": [3, 2]})
        assert response.json()["tensor"]["strides"] == [1, 0]

    def test_overlapping_write_rejected_over_http(self, client):
        _create(client, [1, 2, 3, 4, 5], "int64", "base")
        # (3,3) sliding window over 5 elements, strides (1,1): overlaps.
        response = client.post(
            "/api/v1/tensors/base/strided-view",
            json={"shape": [3, 3], "strides": [1, 1], "offset": 0})
        assert response.status_code == 201
        window = response.json()["handle"]
        assert response.json()["self_overlapping"] is True

        rejected = client.post(
            f"/api/v1/tensors/{window}/assign/scalar",
            json={"value": 7, "policy": "reject"})
        assert rejected.status_code == 409
        body = rejected.json()
        assert body["error"]["category"] == "OVERLAPPING_WRITE"
        assert body["error"]["details"]["positions"] == 9
        assert body["error"]["details"]["unique_elements"] == 5
        # The parent storage is untouched after a refusal.
        values = client.get("/api/v1/tensors/base/values").json()["values"]
        assert values == [1, 2, 3, 4, 5]

        # The same write under temp_copy succeeds deterministically.
        allowed = client.post(
            f"/api/v1/tensors/{window}/assign/scalar",
            json={"value": 7, "policy": "temp_copy"})
        assert allowed.status_code == 200
        assert allowed.json()["report"]["temp_copy"] is True
        values = client.get("/api/v1/tensors/base/values").json()["values"]
        assert values == [7, 7, 7, 7, 7]

    def test_strided_view_out_of_bounds_rejected(self, client):
        _create(client, [1, 2, 3], "int64", "base")
        response = client.post(
            "/api/v1/tensors/base/strided-view",
            json={"shape": [3, 3], "strides": [1, 1], "offset": 0})
        assert response.status_code == 422
        assert response.json()["error"]["category"] == "INVALID_STRIDE"

    def test_invalid_policy_payload_rejected(self, client):
        _create(client, [[10, 11], [20, 21]], "int64", "m")
        response = client.post(
            "/api/v1/tensors/m/assign/scalar",
            json={"value": 1, "policy": "bogus"})
        assert response.status_code == 422

    def test_delete_and_404(self, client):
        handle = _create(client, [1], "int64")
        assert client.delete(f"/api/v1/tensors/{handle}").status_code == 200
        response = client.get(f"/api/v1/tensors/{handle}")
        assert response.status_code == 404
        assert response.json()["error"]["category"] == "STATE_ERROR"

    def test_unsupported_dtype_rejected(self, client):
        response = client.post(
            "/api/v1/tensors", json={"data": [1, 2], "dtype": "complex128"})
        assert response.status_code == 422
        assert response.json()["error"]["category"] == "UNSUPPORTED_DTYPE"


class TestErrorStatusMapping:
    def test_oob_is_400(self, client):
        _create(client, [[1, 2]], "int64", "a")
        response = client.post(
            "/api/v1/tensors/a/slice", json={"index": [5]})
        assert response.status_code == 400
        assert response.json()["error"]["category"] == "INDEX_OUT_OF_BOUNDS"

    def test_size_mismatch_is_422(self, client):
        _create(client, [1, 2, 3, 4], "int64", "a")
        response = client.post(
            "/api/v1/tensors/a/reshape", json={"shape": [7], "order": "C"})
        assert response.status_code == 422
        assert response.json()["error"]["category"] == "SIZE_MISMATCH"

    def test_invalid_request_body_is_422(self, client):
        response = client.post(
            "/api/v1/tensors", json={"dtype": "int64"})  # missing data
        assert response.status_code == 422
        assert response.json()["error"]["category"] == "INVALID_REQUEST"


class TestOpsEndpoints:
    def test_binary_and_reduce_chain(self, client):
        _create(client, [[1.0, 2.0], [3.0, 4.0]], handle="a")
        _create(client, [[10.0, 20.0], [30.0, 40.0]], handle="b")
        add = client.post(
            "/api/v1/ops/binary",
            json={"left": "a", "right": "b", "op": "add"}).json()
        assert add["broadcast_shape"] == [2, 2]
        total = client.post(
            "/api/v1/ops/reduce_sum", json={"tensor": add["handle"]}).json()
        assert total["tensor"]["shape"] == []

    def test_broadcast_add_outer_sum(self, client):
        _create(client, [[1], [2], [3]], "int64", "col")
        _create(client, [[10, 20]], "int64", "row")
        wide_col = client.post(
            "/api/v1/tensors/col/broadcast",
            json={"shape": [3, 2]}).json()["handle"]
        wide_row = client.post(
            "/api/v1/tensors/row/broadcast",
            json={"shape": [3, 2]}).json()["handle"]
        response = client.post(
            "/api/v1/ops/binary",
            json={"left": wide_col, "right": wide_row, "op": "add"})
        values = client.get(
            f"/api/v1/tensors/{response.json()['handle']}/values").json()
        assert values["values"] == [[11, 21], [12, 22], [13, 23]]

    def test_matmul_endpoint(self, client):
        _create(client, [[1, 2], [3, 4]], "int64", "a")
        _create(client, [[5, 6], [7, 8]], "int64", "b")
        response = client.post(
            "/api/v1/ops/matmul", json={"left": "a", "right": "b"})
        values = client.get(
            f"/api/v1/tensors/{response.json()['handle']}/values").json()
        assert values["values"] == [[19, 22], [43, 50]]


class TestGraphEndpoints:
    def _build_transpose_reshape_graph(self, client):
        graph = {
            "nodes": [
                {"id": "t", "op": "transpose", "inputs": ["x"]},
                {"id": "flat", "op": "reshape", "inputs": ["t"],
                 "params": {"shape": [6], "order": "C"}},
            ],
            "outputs": ["flat"], "inputs": ["x"], "handle": "tr",
        }
        response = client.post("/api/v1/graphs", json=graph)
        assert response.status_code == 201, response.text

    def test_graph_execute_trace(self, client):
        self._build_transpose_reshape_graph(client)
        _create(client, [[1, 2, 3], [4, 5, 6]], "int64", "a")
        response = client.post(
            "/api/v1/graphs/tr/execute",
            json={"bindings": {"x": "a"}, "include_values": True})
        body = response.json()
        assert body["values"]["flat"] == [1, 4, 2, 5, 3, 6]
        traces = {t["node_id"]: t for t in body["trace"]}
        assert traces["t"]["copied"] is False
        assert traces["t"]["aliases_input"] is not None
        assert traces["flat"]["copied"] is True
        # Explainability: shape/strides and timing present per node.
        assert traces["flat"]["shape"] == [6]
        assert "elapsed_us" in traces["flat"]

    def test_invalid_graph_rejected(self, client):
        response = client.post("/api/v1/graphs", json={
            "nodes": [
                {"id": "a", "op": "neg", "inputs": ["b"]},
                {"id": "b", "op": "neg", "inputs": ["a"]},
            ],
            "outputs": ["a"],
        })
        assert response.status_code == 422
        assert response.json()["error"]["category"] == "GRAPH_ERROR"

    def test_graph_missing_binding(self, client):
        self._build_transpose_reshape_graph(client)
        response = client.post(
            "/api/v1/graphs/tr/execute", json={"bindings": {}})
        assert response.status_code == 422
        assert response.json()["error"]["category"] == "GRAPH_ERROR"


class TestTrainingEndpoints:
    @pytest.fixture
    def regression_data(self, client):
        rng = np.random.default_rng(7)
        n, d = 300, 3
        X = rng.normal(size=(n, d))
        w = np.array([1.0, -2.0, 0.5])
        y = X @ w + 0.4 + rng.normal(scale=1e-3, size=n)
        _create(client, X.tolist(), "float64", "X")
        _create(client, y.tolist(), "float64", "y")
        return w

    def test_full_training_flow(self, client, regression_data):
        response = client.post(
            "/api/v1/training",
            json={"features": "X", "targets": "y",
                  "learning_rate": 0.1, "max_steps": 200})
        assert response.status_code == 201
        handle = response.json()["handle"]

        run = client.post(
            f"/api/v1/training/{handle}/run", json={"steps": 200})
        assert run.status_code == 200
        status = run.json()["status"]
        assert status["state"] in {"ready", "completed"}
        assert status["last_loss"] < 1e-4

        weights = client.get(
            f"/api/v1/training/{handle}/weights").json()
        np.testing.assert_allclose(weights["weights"], regression_data, atol=1e-2)
        np.testing.assert_allclose(weights["bias"], [0.4], atol=1e-2)

        predictions = client.get(
            f"/api/v1/training/{handle}/predictions")
        assert predictions.status_code == 200
        assert len(predictions.json()["predictions"]) == 300

    def test_background_pause_resume(self, client, regression_data):
        client.post(
            "/api/v1/training",
            json={"features": "X", "targets": "y",
                  "learning_rate": 0.01, "max_steps": 1_000_000})
        handle = client.get("/api/v1/training").json()["handles"][-1]
        client.post(
            f"/api/v1/training/{handle}/run",
            json={"steps": 1_000_000, "background": True})
        paused = client.post(f"/api/v1/training/{handle}/pause")
        assert paused.status_code == 200
        settled = client.post(
            f"/api/v1/training/{handle}/wait", json={"timeout": 10})
        assert settled.json()["settled"] is True
        assert settled.json()["status"]["state"] == "paused"
        resumed = client.post(f"/api/v1/training/{handle}/resume")
        assert resumed.json()["status"]["state"] == "running"

    def test_training_rejects_bad_inputs(self, client):
        _create(client, [1.0, 2.0], "float64", "bad_x")
        _create(client, [1.0, 2.0], "float64", "good_y")
        response = client.post(
            "/api/v1/training",
            json={"features": "bad_x", "targets": "good_y"})
        assert response.status_code == 422
        assert response.json()["error"]["category"] == "STATE_ERROR"


class TestVerificationEndpoints:
    def test_list_and_run_named_scenario(self, client):
        listing = client.get("/api/v1/verify/scenarios")
        assert "transpose_reshape_2d" in listing.json()["scenarios"]
        response = client.get(
            "/api/v1/verify/scenarios/transpose_reshape_2d")
        body = response.json()
        assert body["ok"] is True
        assert body["summary"]["failed"] == 0

    def test_unknown_scenario_404(self, client):
        response = client.get("/api/v1/verify/scenarios/nope")
        assert response.status_code == 404

    def test_custom_scenario(self, client):
        steps = [
            {"op": "input", "name": "a",
             "data": [[1, 2], [3, 4]], "dtype": "int64"},
            {"op": "transpose", "name": "t", "input": "a"},
        ]
        response = client.post("/api/v1/verify/scenarios", json={"steps": steps})
        assert response.json()["summary"]["failed"] == 0

    def test_verify_all_green(self, client):
        response = client.get("/api/v1/verify/all")
        body = response.json()
        assert body["ok"] is True
        assert body["totals"]["memory_check_failures"] == 0

    def test_memory_checks_endpoint(self, client):
        response = client.get("/api/v1/verify/memory-checks")
        body = response.json()
        assert body["ok"] is True
        names = {check["name"] for check in body["checks"]}
        assert "overlap_write_rejected" in names
        assert "broadcast_zero_strides" in names
