"""集成测试: HTTP 全链路 (建库/装载/查询/运行回放/错误分类)。

使用 FastAPI TestClient + 临时 SQLite/日志目录, 不监听真实端口。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import SAMPLES


@pytest.fixture
def client(settings):
    app = create_app(settings)
    with TestClient(app) as test_client:
        yield test_client


def _sample(name: str) -> str:
    return Path(SAMPLES / name).read_text(encoding="utf-8")


def _create_kb(client, kb_id="kb1", name="测试知识库"):
    resp = client.post("/api/kbs", json={"kb_id": kb_id, "name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


# --------------------------------------------------------------------------- #
# 基础端点
# --------------------------------------------------------------------------- #

def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_create_and_list_kb(client):
    _create_kb(client, "alpha", "Alpha")
    resp = client.get("/api/kbs")
    assert resp.status_code == 200
    ids = [kb["kb_id"] for kb in resp.json()["knowledge_bases"]]
    assert "alpha" in ids


def test_duplicate_kb_is_state_conflict(client):
    _create_kb(client, "dup")
    resp = client.post("/api/kbs", json={"kb_id": "dup", "name": "x"})
    assert resp.status_code == 409
    assert resp.json()["category"] == "state_conflict"


def test_query_missing_kb_is_state_conflict(client):
    resp = client.post(
        "/api/kbs/ghost/query", json={"query": "Flies(t)"}
    )
    assert resp.status_code == 409
    assert resp.json()["category"] == "state_conflict"


# --------------------------------------------------------------------------- #
# 鸟样例: 接受 / 否决 / 枚举
# --------------------------------------------------------------------------- #

def test_birds_theory_full_lifecycle(client):
    _create_kb(client, "birds")
    resp = client.put(
        "/api/kbs/birds/theory", json={"theory": _sample("birds.rdrl")}
    )
    assert resp.status_code == 200, resp.text
    stats = resp.json()["stats"]
    assert stats["facts"] == 3
    assert stats["ground_instances"] >= 2

    # tweety 会飞
    r1 = client.post("/api/kbs/birds/query", json={"query": "Flies(tweety)"})
    assert r1.status_code == 200
    a1 = r1.json()["answers"][0]
    assert a1["status"] == "accepted"
    assert a1["reason_code"] == "ACCEPTED_GROUNDED"
    assert a1["supporting_chains"][0]["rule_id"] == "r1"

    # polly 被企鹅默认否决
    r2 = client.post("/api/kbs/birds/query", json={"query": "Flies(polly)"})
    a2 = r2.json()["answers"][0]
    assert a2["status"] == "rejected"
    assert a2["reason_code"] == "REJECTED_OPPOSITE_GROUNDED"
    counter = a2["defeated_chains"][0]["counter_chains"][0]
    assert counter["rule"] == "r2"
    assert "r2 > r1" in counter["detail"]

    # 变量枚举
    r3 = client.post("/api/kbs/birds/query", json={"query": "Flies(X)"})
    answers = r3.json()["answers"]
    statuses = {a["goal"]: a["status"] for a in answers}
    assert statuses == {"Flies(tweety)": "accepted", "Flies(polly)": "rejected"}
    # 置换必须回带
    substs = {a["goal"]: a["substitution"] for a in answers}
    assert substs["Flies(tweety)"]["X"] == "tweety"


def test_nixon_diamond_conflict_is_preserved(client):
    _create_kb(client, "nixon")
    resp = client.put(
        "/api/kbs/nixon/theory", json={"theory": _sample("nixon_diamond.rdrl")}
    )
    assert resp.status_code == 200, resp.text

    r = client.post("/api/kbs/nixon/query", json={"query": "Pacifist(nixon)"})
    answer = r.json()["answers"][0]
    assert answer["status"] == "undecided"
    assert answer["reason_code"] == "PENDING_MUTUAL_CONFLICT"
    assert answer["chain_counts"]["pending"] == 1
    assert answer["chain_counts"]["supporting"] == 0
    detail = answer["pending_chains"][0]["counter_chains"][0]["detail"]
    assert "不可比较" in detail


def test_naf_missing_evidence_is_not_false(client):
    _create_kb(client, "naf")
    client.put(
        "/api/kbs/naf/theory", json={"theory": _sample("naf_open_world.rdrl")}
    )
    r = client.post("/api/kbs/naf/query", json={"query": "Wingless(tweety)"})
    answer = r.json()["answers"][0]
    assert answer["status"] == "no_evidence"
    assert answer["reason_code"] == "NO_EVIDENCE_AT_ALL"


# --------------------------------------------------------------------------- #
# 错误分类的 HTTP 映射
# --------------------------------------------------------------------------- #

def test_priority_cycle_is_input_error_over_http(client):
    _create_kb(client, "cyc")
    resp = client.put(
        "/api/kbs/cyc/theory",
        json={"theory": _sample("priority_cycle.rdrl.bad")},
    )
    assert resp.status_code == 422
    body = resp.json()
    assert body["category"] == "input_error"
    assert body["error"] == "RuleLanguageError"
    assert body["details"]["cycle"][0] == body["details"]["cycle"][-1]


def test_strict_contradiction_is_state_conflict_over_http(client):
    _create_kb(client, "contra")
    resp = client.put(
        "/api/kbs/contra/theory",
        json={"theory": _sample("strict_contradiction.rdrl.bad")},
    )
    assert resp.status_code == 409
    assert resp.json()["category"] == "state_conflict"
    # 装载失败不应污染知识库 (无规则/事实残留)
    runs = client.get("/api/kbs").json()["knowledge_bases"]
    contra = next(kb for kb in runs if kb["kb_id"] == "contra")
    assert contra["stats"] == {}


def test_malformed_request_body_is_input_error(client):
    _create_kb(client, "body")
    resp = client.post("/api/kbs/body/query", json={"wrong_field": "x"})
    assert resp.status_code == 422
    assert resp.json()["category"] == "input_error"


def test_syntax_error_is_input_error(client):
    _create_kb(client, "syn")
    resp = client.put(
        "/api/kbs/syn/theory", json={"theory": "@r Flies(X) := Bird(X)"}
    )
    assert resp.status_code == 422
    assert resp.json()["category"] == "input_error"


# --------------------------------------------------------------------------- #
# 增量事实
# --------------------------------------------------------------------------- #

def test_add_facts_then_query(client):
    _create_kb(client, "inc")
    client.put(
        "/api/kbs/inc/theory",
        json={"theory": "@r Flies(X) := Bird(X)."},
    )
    resp = client.post(
        "/api/kbs/inc/facts", json={"facts": ["Bird(tweety).", "Bird(polly)."]}
    )
    assert resp.status_code == 200
    assert resp.json()["inserted"] == 2

    r = client.post("/api/kbs/inc/query", json={"query": "Flies(X)"})
    goals = {a["goal"] for a in r.json()["answers"]}
    assert goals == {"Flies(tweety)", "Flies(polly)"}


# --------------------------------------------------------------------------- #
# 运行回放与日志
# --------------------------------------------------------------------------- #

def test_run_is_replayable_with_run_id(client):
    _create_kb(client, "log")
    client.put("/api/kbs/log/theory", json={"theory": _sample("birds.rdrl")})
    r = client.post("/api/kbs/log/query", json={"query": "Flies(polly)"})
    run_id = r.json()["run_id"]
    assert run_id.startswith("run-")

    replay = client.get(f"/api/runs/{run_id}")
    assert replay.status_code == 200
    record = replay.json()
    assert record["request"] == {"kb_id": "log", "query": "Flies(polly)"}
    assert record["status_summary"] == "ok"
    answer = record["result"]["answers"][0]
    assert answer["status"] == "rejected"

    # 运行列表里也能找到
    listing = client.get("/api/runs").json()["runs"]
    assert any(row["run_id"] == run_id for row in listing)


def test_failed_run_is_recorded_with_category(client):
    _create_kb(client, "fail")
    resp = client.put(
        "/api/kbs/fail/theory",
        json={"theory": _sample("priority_cycle.rdrl.bad")},
    )
    assert resp.status_code == 422
    run_id = resp.json()["run_id"]
    record = client.get(f"/api/runs/{run_id}").json()
    assert record["error_category"] == "input_error"
    assert record["error_name"] == "RuleLanguageError"
    assert record["request"]["kb_id"] == "fail"


def test_jsonl_logs_contain_run_id_and_intermediate_state(client, settings):
    _create_kb(client, "filelog")
    client.put(
        "/api/kbs/filelog/theory", json={"theory": _sample("birds.rdrl")}
    )
    r = client.post(
        "/api/kbs/filelog/query", json={"query": "Flies(tweety)"}
    )
    run_id = r.json()["run_id"]

    request_log = settings.request_log.read_text(encoding="utf-8")
    trace_log = settings.kernel_trace.read_text(encoding="utf-8")
    assert run_id in request_log
    # 中间状态: 接地/论证规模 + 查询答案理由码
    assert run_id in trace_log
    trace_records = [
        json.loads(line) for line in trace_log.splitlines() if run_id in line
    ]
    stages = {rec["stage"] for rec in trace_records}
    assert {"loaded", "compiled", "query_answers"} <= stages
    query_rec = next(
        rec for rec in trace_records if rec["stage"] == "query_answers"
    )
    assert query_rec["state"]["answers"][0]["reason_code"] == "ACCEPTED_GROUNDED"


def test_error_log_separates_error_category(client, settings):
    _create_kb(client, "errlog")
    client.put(
        "/api/kbs/errlog/theory",
        json={"theory": _sample("strict_contradiction.rdrl.bad")},
    )
    error_lines = [
        json.loads(line) for line in settings.error_log.read_text().splitlines()
    ]
    assert error_lines, "错误必须写入独立错误日志"
    assert error_lines[0]["error"]["category"] == "state_conflict"


# --------------------------------------------------------------------------- #
# 规则重排在 HTTP 层结论一致
# --------------------------------------------------------------------------- #

def test_reorder_gives_same_http_result(client):
    _create_kb(client, "order_a")
    _create_kb(client, "order_b")
    text = _sample("birds.rdrl")
    code_lines = [
        line for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith(("%", "#"))
    ]
    code = "\n".join(code_lines)
    statements = [s.strip() for s in code.split(".") if s.strip()]
    assert len(statements) == 6  # 3 事实 + 2 规则 + 1 优先关系
    reordered = ".\n".join(reversed(statements)) + "."

    client.put("/api/kbs/order_a/theory", json={"theory": text})
    client.put("/api/kbs/order_b/theory", json={"theory": reordered})

    def statuses(kb):
        r = client.post(f"/api/kbs/{kb}/query", json={"query": "Flies(X)"})
        return {a["goal"]: (a["status"], a["reason_code"]) for a in r.json()["answers"]}

    assert statuses("order_a") == statuses("order_b")
