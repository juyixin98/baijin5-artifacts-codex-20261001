"""服务接口测试：状态语义、错误类别、逐列报告与敏感数据脱敏。"""
import logging

import pytest
from fastapi.testclient import TestClient

from config.settings import SolverSettings
from irsolver.service import create_app

from tests.fixtures import (
    make_batch_system,
    make_random_system,
    make_singular_system,
)


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(SolverSettings()))


@pytest.fixture(scope="module")
def well():
    return make_random_system(n=8, kappa=1e2, seed=11, name="well")


def _payload(fx, **extra):
    body = {
        "a": [list(row) for row in fx.A.text],
        "b": [list(row) for row in fx.B.text],
    }
    body.update(extra)
    return body


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_solve_well_conditioned(client, well):
    resp = client.post("/solve", json=_payload(well))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "converged"
    assert body["request_id"]
    col = body["columns"][0]
    assert col["status"] == "converged"
    assert col["tier_used"] == "fp32"
    assert col["eta_componentwise"] <= 1e-10
    assert col["trace"], "迭代轨迹必须随响应返回"
    x = [row[0] for row in body["solution"]]
    truth = well.x_true64(0)
    assert max(abs(a - b) for a, b in zip(x, truth)) <= 1e-10
    assert body["condition"]["kappa"] == pytest.approx(1e2, rel=0.5)
    assert body["diagnostics"]["journal"], "决策日志必须随响应返回"
    assert "condition_estimate" in body["matrix_summary"]


def test_solve_batch_reports_each_column(client):
    fx = make_batch_system(n=8, kappa=1e6, seed=99)
    resp = client.post("/solve", json=_payload(fx))
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["columns"]) == 3
    assert [c["index"] for c in body["columns"]] == [0, 1, 2]
    assert all(c["status"] == "converged" for c in body["columns"])
    assert len(body["solution"]) == 8 and len(body["solution"][0]) == 3


def test_solve_singular_returns_singular_status(client):
    fx = make_singular_system(n=8, seed=5)
    resp = client.post("/solve", json=_payload(fx))
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "singular"
    assert body["solution"] is None
    assert body["condition"]["rank_deficient"] is True
    assert all(c["status"] == "singular" for c in body["columns"])


def test_invalid_input_returns_422_with_reason(client):
    resp = client.post("/solve", json={"a": [["1", "2"], ["3"]], "b": [["1"], ["2"]]})
    assert resp.status_code == 422
    assert resp.json()["error"]["reason"] == "ragged"
    resp = client.post("/solve", json={"a": [["1", "0"], ["0", "nan"]], "b": [["1"], ["2"]]})
    assert resp.status_code == 422
    assert resp.json()["error"]["reason"] == "non_finite"
    resp = client.post("/solve", json={"a": [["1", "2", "3"]], "b": [["1"]]})
    assert resp.status_code == 422
    assert resp.json()["error"]["reason"] == "not_square"
    assert resp.json()["request_id"]


def test_solution_text_roundtrip(client, well):
    resp = client.post("/solve", json=_payload(well, include_solution_text=True))
    body = resp.json()
    text_col = body["columns"][0]["solution_text"]
    assert text_col == [repr(v) for v in body["columns"][0]["solution"]]


def test_sensitive_mode_redacts_numerics(client, well, caplog):
    with caplog.at_level(logging.INFO, logger="irsolver"):
        resp = client.post("/solve", json=_payload(well, sensitive=True))
    assert resp.status_code == 200
    body = resp.json()
    summary = body["matrix_summary"]
    assert "fingerprint" in summary and "shape_a" in summary
    assert "norm_a_fro" not in summary
    assert "condition_estimate" not in summary
    assert body["condition"] is None
    assert body["accuracy_note"] is None
    # 日志同样不得出现数值画像
    assert "kappa" not in caplog.text
    assert "eta_comp" not in caplog.text


def test_non_sensitive_logs_include_diagnostics(client, well, caplog):
    with caplog.at_level(logging.INFO, logger="irsolver"):
        client.post("/solve", json=_payload(well))
    assert "kappa" in caplog.text
    assert "eta_comp" in caplog.text
