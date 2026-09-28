"""Edge-branch tests for API routes, session registry and ASGI entrypoint."""

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


class TestOpRouteEdges:
    def test_unknown_binary_op(self, client):
        _create(client, [1, 2], "int64", "a")
        _create(client, [3, 4], "int64", "b")
        response = client.post(
            "/api/v1/ops/binary", json={"left": "a", "right": "b", "op": "nope"})
        assert response.status_code == 422
        assert response.json()["error"]["category"] == "SHAPE_MISMATCH"

    def test_unknown_unary_op(self, client):
        _create(client, [1, 2], "int64", "a")
        response = client.post(
            "/api/v1/ops/unary", json={"tensor": "a", "op": "sqrt"})
        assert response.status_code == 422

    def test_unknown_scalar_op(self, client):
        _create(client, [1, 2], "int64", "a")
        response = client.post(
            "/api/v1/ops/scalar",
            json={"tensor": "a", "op": "bogus", "value": 2})
        assert response.status_code == 422

    def test_scalar_op_divide_promotes(self, client):
        _create(client, [4, 6], "int64", "a")
        response = client.post(
            "/api/v1/ops/scalar",
            json={"tensor": "a", "op": "divide", "value": 2})
        handle = response.json()["handle"]
        values = client.get(f"/api/v1/tensors/{handle}/values").json()
        assert values["values"] == [2.0, 3.0]

    def test_comparison_endpoint(self, client):
        _create(client, [1, 2], "int64", "a")
        _create(client, [2, 2], "int64", "b")
        response = client.post(
            "/api/v1/ops/binary",
            json={"left": "a", "right": "b", "op": "less"})
        handle = response.json()["handle"]
        assert client.get(f"/api/v1/tensors/{handle}/values").json()[
            "values"] == [1, 0]


class TestIndexDecoding:
    def test_newaxis_and_ellipsis_tokens(self, client):
        _create(client, np.arange(6).reshape(2, 3).tolist(), "int64", "a")
        response = client.post(
            "/api/v1/tensors/a/slice",
            json={"index": ["ellipsis", "newaxis"]})
        assert response.status_code == 201
        assert response.json()["tensor"]["shape"] == [2, 3, 1]

    def test_bad_string_token_rejected(self, client):
        _create(client, [1, 2, 3], "int64", "a")
        response = client.post(
            "/api/v1/tensors/a/slice", json={"index": ["wat"]})
        assert response.status_code == 400
        assert response.json()["error"]["category"] == "INVALID_INDEX"

    def test_boolean_entry_rejected(self, client):
        _create(client, [1, 2, 3], "int64", "a")
        response = client.post(
            "/api/v1/tensors/a/slice", json={"index": [True]})
        assert response.status_code == 400

    def test_bad_slice_length_rejected(self, client):
        _create(client, [1, 2, 3], "int64", "a")
        response = client.post(
            "/api/v1/tensors/a/slice", json={"index": [[0, 2]]})
        assert response.status_code == 400


class TestGraphCrud:
    def test_list_get_delete(self, client):
        graph = {
            "nodes": [{"id": "n", "op": "neg", "inputs": ["x"]}],
            "outputs": ["n"], "inputs": ["x"], "handle": "g1",
        }
        client.post("/api/v1/graphs", json=graph)
        listing = client.get("/api/v1/graphs")
        assert "g1" in listing.json()["handles"]
        fetched = client.get("/api/v1/graphs/g1")
        assert fetched.json()["graph"]["nodes"][0]["id"] == "n"
        assert client.delete("/api/v1/graphs/g1").status_code == 200
        assert client.get("/api/v1/graphs/g1").status_code == 404

    def test_execute_unknown_output_rejected(self, client):
        graph = {
            "nodes": [{"id": "n", "op": "neg", "inputs": ["x"]}],
            "outputs": ["n"], "inputs": ["x"], "handle": "g2",
        }
        client.post("/api/v1/graphs", json=graph)
        _create(client, [1], "int64", "a")
        response = client.post(
            "/api/v1/graphs/g2/execute",
            json={"bindings": {"x": "a"}, "outputs": ["ghost"]})
        assert response.status_code == 422
        assert response.json()["error"]["category"] == "GRAPH_ERROR"


class TestTensorAstypeAndSqueeze:
    def test_astype_copies_and_changes_dtype(self, client):
        _create(client, [1, 2, 3], "int64", "a")
        response = client.post(
            "/api/v1/tensors/a/astype", json={"dtype": "float32"})
        body = response.json()
        assert body["copied"] is True
        assert body["tensor"]["dtype"] == "float32"

    def test_materialize_f_order(self, client):
        _create(client, [[[1, 2]]], "int64", "a")
        response = client.post(
            "/api/v1/tensors/a/materialize", json={"order": "F"})
        assert response.json()["copied"] is True
        assert response.json()["tensor"]["f_contiguous"] is True


class TestMainEntrypoint:
    def test_app_module_exposes_application(self):
        from tensorcraft.main import app
        assert app is not None
        assert app.version == "0.1.0"

    def test_element_limit_returns_413(self):
        from fastapi.testclient import TestClient
        from tensorcraft.api import create_app
        from tensorcraft.config import load_config
        small_config = load_config().with_overrides(
            **{"storage.max_elements": 3})
        client = TestClient(create_app(small_config))
        response = client.post(
            "/api/v1/tensors", json={"data": [1, 2, 3, 4], "dtype": "int64"})
        assert response.status_code == 413
        assert response.json()["error"]["category"] == "STORAGE_LIMIT_EXCEEDED"


class TestAssignTensorEndpoint:
    def test_assign_tensor_broadcast_and_buffer_flag(self, client):
        _create(client, [[0, 0], [0, 0]], "int64", "dst")
        _create(client, [[7]], "int64", "src")
        response = client.post(
            "/api/v1/tensors/dst/assign/tensor",
            json={"source": "src", "policy": "reject"})
        assert response.status_code == 200
        body = response.json()
        # A size-1 source broadcasts to the 2x2 destination.
        assert body["report"]["elements_written"] == 4
        values = client.get("/api/v1/tensors/dst/values").json()["values"]
        assert values == [[7, 7], [7, 7]]

    def test_assign_unknown_source_404(self, client):
        _create(client, [0, 0], "int64", "dst")
        response = client.post(
            "/api/v1/tensors/dst/assign/tensor",
            json={"source": "ghost", "policy": "reject"})
        assert response.status_code == 404

    def test_assign_incompatible_shape_422(self, client):
        _create(client, [0, 0, 0], "int64", "dst")
        _create(client, [1, 2], "int64", "src")
        response = client.post(
            "/api/v1/tensors/dst/assign/tensor",
            json={"source": "src", "policy": "reject"})
        assert response.status_code == 422
        assert response.json()["error"]["category"] == "BROADCAST_ERROR"
