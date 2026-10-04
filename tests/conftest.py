"""测试基座:本地驱动器( Harness )、失败时审计转储、独立参考值.

- Harness 通过 ASGI 传输在进程内驱动真实 FastAPI 应用与真实客户端,
  掉线通过"不再调用该客户端后续轮次"模拟。
- 每个用例打印 run_id;失败时自动转储该运行的审计日志
  (阶段、事件、判断理由),便于重放定位。
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient

from secagg.client import SecAggClient
from secagg.encoding import EncodingParams
from secagg.server import create_app
from secagg.state import StateStore
from secagg.transport import HttpTransport

logger = logging.getLogger("secagg.tests")

DEFAULT_SCALE = 1 << 20
DEFAULT_ELEM_BOUND = 1 << 40


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    rep = outcome.get_result()
    setattr(item, "rep_" + rep.when, rep)


class Harness:
    """一次聚合运行的测试驱动器。"""

    def __init__(self, client_ids: list[str], threshold: int, vector_len: int,
                 scale: int = DEFAULT_SCALE, elem_bound: int = DEFAULT_ELEM_BOUND):
        self.store = StateStore(":memory:")
        self.app = create_app(self.store)
        self.http = TestClient(self.app)
        self.transport = HttpTransport(self.http)
        resp = self.http.post("/runs", json={
            "client_ids": client_ids,
            "threshold": threshold,
            "vector_len": vector_len,
            "scale": scale,
            "elem_bound": elem_bound,
        })
        assert resp.status_code == 200, resp.text
        self.run_id = resp.json()["run_id"]
        self.client_ids = sorted(client_ids)
        self.threshold = threshold
        self.vector_len = vector_len
        self.params = EncodingParams(
            scale=scale, elem_bound=elem_bound, max_clients=len(client_ids))
        self.clients = {
            cid: SecAggClient(cid, self.run_id, self.transport,
                              self.params, threshold, vector_len)
            for cid in self.client_ids
        }
        logger.info("harness created run_id=%s clients=%s t=%d",
                    self.run_id, self.client_ids, threshold)

    # ---- 轮次驱动: 不在 participants 中的客户端即视为掉线 ----

    def round0(self, participants: list[str] | None = None) -> None:
        for cid in participants or self.client_ids:
            self.clients[cid].advertise_keys()
        self.advance()

    def round1(self, participants: list[str] | None = None) -> None:
        for cid in participants or self.client_ids:
            self.clients[cid].share_keys()
        self.advance()

    def round2(self, vectors: dict[str, list[float]],
               participants: list[str] | None = None) -> None:
        for cid in participants or self.client_ids:
            self.clients[cid].submit_masked_input(vectors[cid])
        self.advance()

    def round3(self, participants: list[str] | None = None) -> dict:
        out = {}
        for cid in participants or self.client_ids:
            out[cid] = self.clients[cid].send_recovery()
        return out

    def advance(self) -> dict:
        resp = self.http.post(f"/runs/{self.run_id}/advance")
        assert resp.status_code == 200, resp.text
        return resp.json()

    def result(self) -> dict:
        return self.http.get(f"/runs/{self.run_id}/result").json()

    def audit(self) -> list[dict]:
        return self.http.get(f"/runs/{self.run_id}/audit").json()

    def dump_audit(self) -> str:
        lines = [f"run_id={self.run_id} 审计日志:"]
        for row in self.audit():
            lines.append(f"  [{row['phase']:>8}] {row['event']}: {row['rationale']}")
        return "\n".join(lines)


@pytest.fixture
def harness_factory(request):
    created: list[Harness] = []

    def make(client_ids, threshold, vector_len, **kw) -> Harness:
        h = Harness(client_ids, threshold, vector_len, **kw)
        created.append(h)
        return h

    yield make

    rep = getattr(request.node, "rep_call", None)
    if rep is not None and rep.failed:
        for h in created:
            print("\n" + h.dump_audit())
