"""API tests: execution, rejection diagnostics, gradcheck endpoint.

Concrete expected values (hand-derived):
* program x@w with x=[[1,2],[3,4]], w=identity -> loss=sum=10,
  dL/dx = ones(2,2) @ w.T = [[1,1],[1,1]],
  dL/dw = x.T @ ones(2,2) = [[4,4],[6,6]].
"""

import numpy as np
from fastapi.testclient import TestClient

from minigrad.api import app

client = TestClient(app)

MATMUL_PROGRAM = {
    "inputs": {
        "x": {"data": [[1, 2], [3, 4]], "requires_grad": True},
        "w": {"data": [[1, 0], [0, 1]], "requires_grad": True},
    },
    "program": [
        {"op": "matmul", "out": "y", "args": ["x", "w"]},
        {"op": "sum", "out": "loss", "args": ["y"]},
    ],
    "loss": "loss",
    "gradients": ["x", "w"],
}


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_execute_returns_concrete_gradients_and_request_id():
    response = client.post("/v1/graph/execute", json=MATMUL_PROGRAM)
    assert response.status_code == 200
    body = response.json()
    assert body["request_id"]
    assert response.headers["x-request-id"] == body["request_id"]
    assert body["loss"] == 10.0
    np.testing.assert_allclose(body["gradients"]["x"], [[1, 1], [1, 1]])
    np.testing.assert_allclose(body["gradients"]["w"], [[4, 4], [6, 6]])
    diag = body["diagnostics"][0]
    assert diag["status"] == "accepted"
    assert diag["request_id"] == body["request_id"]


def test_inplace_mutation_rejected_with_diagnostic():
    program = {
        "inputs": {"x": {"data": [1.0, 2.0, 3.0], "requires_grad": True}},
        "program": [
            {"op": "mul", "out": "y", "args": ["x", "x"]},
            {"op": "setitem", "args": ["x"], "kwargs": {"index": [0], "value": 9.0}},
            {"op": "sum", "out": "loss", "args": ["y"]},
        ],
        "loss": "loss",
        "gradients": ["x"],
    }
    response = client.post("/v1/graph/execute", json=program)
    assert response.status_code == 409
    body = response.json()
    diag = body["diagnostics"][0]
    assert diag["status"] == "rejected"
    assert diag["component"] == "backward"
    assert "in-place" in diag["reason"]
    assert diag["state"]["op"] == "mul"
    assert diag["state"]["expected_version"] == 0
    assert diag["state"]["actual_version"] == 1


def test_retain_graph_via_api():
    # Double-backward rejection (GraphFreedError) cannot be triggered in a
    # one-shot execute call; it is covered in test_graph_release.py. Here we
    # assert retain_graph=True is accepted and yields the correct gradient.
    program = {
        "inputs": {"x": {"data": 2.0, "requires_grad": True}},
        "program": [{"op": "mul", "out": "loss", "args": ["x", "x"]}],
        "loss": "loss",
        "gradients": ["x"],
        "retain_graph": True,
    }
    response = client.post("/v1/graph/execute", json=program)
    assert response.status_code == 200
    np.testing.assert_allclose(response.json()["gradients"]["x"], 4.0)


def test_no_grad_vs_zero_grad_over_api():
    program = {
        "inputs": {
            "used": {"data": [1.0, 2.0], "requires_grad": True},
            "unused": {"data": [3.0, 4.0], "requires_grad": True},
            "dead": {"data": [-1.0, -2.0], "requires_grad": True},
        },
        "program": [
            {"op": "mul", "out": "a", "args": ["used", 2.0]},
            {"op": "relu", "out": "r", "args": ["dead"]},
            {"op": "add", "out": "loss", "args": ["a", "r"]},
            {"op": "sum", "out": "total", "args": ["loss"]},
        ],
        "loss": "total",
        "gradients": ["used", "unused", "dead"],
    }
    response = client.post("/v1/graph/execute", json=program)
    assert response.status_code == 200
    grads = response.json()["gradients"]
    np.testing.assert_allclose(grads["used"], [2.0, 2.0])
    assert grads["unused"] is None            # no gradient
    np.testing.assert_allclose(grads["dead"], [0.0, 0.0])  # zero gradient


def test_unknown_loss_name_rejected():
    program = dict(MATMUL_PROGRAM, loss="nope")
    response = client.post("/v1/graph/execute", json=program)
    assert response.status_code == 422
    assert response.json()["diagnostics"][0]["status"] == "rejected"


def test_gradcheck_endpoint_accepted():
    response = client.post("/v1/gradcheck/broadcast_add_mul")
    assert response.status_code == 200
    body = response.json()
    report = body["report"]
    assert report["case"] == "broadcast_add_mul"
    assert report["status"] == "accepted"
    assert body["diagnostics"][0]["status"] == "accepted"
    ratios = [p["max_error_ratio"] for p in report["params"]]
    assert all(r <= 1.0 for r in ratios)


def test_gradcheck_unknown_case_404():
    response = client.post("/v1/gradcheck/does_not_exist")
    assert response.status_code == 404
    assert response.json()["diagnostics"][0]["status"] == "rejected"


def test_list_cases():
    response = client.get("/v1/gradcheck")
    assert response.status_code == 200
    assert "broadcast_add_mul" in response.json()["cases"]


def test_diagnostics_never_contain_raw_values():
    program = {
        "inputs": {"secret": {"data": [12345.6789], "requires_grad": True}},
        "program": [
            {"op": "mul", "out": "y", "args": ["secret", "secret"]},
            {"op": "setitem", "args": ["secret"], "kwargs": {"index": [0], "value": 0.0}},
            {"op": "sum", "out": "loss", "args": ["y"]},
        ],
        "loss": "loss",
        "gradients": ["secret"],
    }
    response = client.post("/v1/graph/execute", json=program)
    assert response.status_code == 409
    assert "12345" not in response.text  # masked: no raw tensor values leaked
