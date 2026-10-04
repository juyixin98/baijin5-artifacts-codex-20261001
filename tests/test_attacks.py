"""攻击与异常夹具:集合变更攻击、越权恢复、阶段违规、资源耗尽.

每个用例断言具体的拒绝类别(input_error / state_conflict /
resource_exhausted / computation_failure)与错误码,并验证
被拒绝的运行未被污染(能继续或已明确中止)。
"""

import base64
import os

import pytest

from secagg.errors import ErrorCategory, SecAggError

from .reference import quantization_tolerance, reference_sum
from .test_e2e import make_vectors


def _fake_share() -> list:
    return [1, base64.b64encode(os.urandom(66)).decode()]


def _drive_to_recovery(h, ids, vectors, dropped=None):
    """驱动到 RECOVERY 阶段; dropped 中的客户端在轮2掉线。"""
    h.round0()
    h.round1()
    alive = [c for c in ids if c not in (dropped or [])]
    h.round2(vectors, participants=alive)
    return alive


def test_mask_and_secret_overlap_rejected(harness_factory):
    """核心约束: 同一目标同时索取掩码种子与私钥份额 -> 409 拒绝并审计。"""
    ids = ["c1", "c2", "c3", "c4"]
    h = harness_factory(ids, threshold=2, vector_len=4)
    vectors = make_vectors(ids, 4)
    alive = _drive_to_recovery(h, ids, vectors, dropped=["c4"])

    malicious = {
        "client_id": "c1",
        "b_shares": {v: _fake_share() for v in alive},
        "sk_shares": {"c1": _fake_share(), "c4": _fake_share()},  # c1 重叠!
    }
    resp = h.http.post(f"/runs/{h.run_id}/recovery", json=malicious)
    assert resp.status_code == 409
    body = resp.json()
    assert body["category"] == "state_conflict"
    assert body["code"] == "mask_and_secret_overlap"
    assert body["detail"]["overlap"] == ["c1"]

    # 审计日志记录了攻击判断理由
    events = [r["event"] for r in h.audit()]
    assert "reject_recovery" in events

    # 运行未被污染: 诚实客户端照常完成
    h.round3(participants=alive)
    result = h.result()
    assert result["status"] == "DONE"
    from secagg.encoding import decode_vector

    decoded = decode_vector(result["sum"], h.params)
    expected = reference_sum({c: vectors[c] for c in alive})
    tol = quantization_tolerance(len(alive), h.params.scale)
    assert all(abs(g - w) <= tol for g, w in zip(decoded, expected))


def test_recovery_targets_must_match_frozen_set(harness_factory):
    """恢复目标集合与冻结集合不符(缺目标/多目标) -> 409。"""
    ids = ["c1", "c2", "c3"]
    h = harness_factory(ids, threshold=2, vector_len=4)
    vectors = make_vectors(ids, 4)
    alive = _drive_to_recovery(h, ids, vectors)

    short = {"client_id": "c1",
             "b_shares": {"c1": _fake_share(), "c2": _fake_share()},  # 缺 c3
             "sk_shares": {}}
    resp = h.http.post(f"/runs/{h.run_id}/recovery", json=short)
    assert resp.status_code == 409
    assert resp.json()["code"] == "recovery_set_mismatch"

    extra = {"client_id": "c1",
             "b_shares": {v: _fake_share() for v in ids} | {"ghost": _fake_share()},
             "sk_shares": {}}
    resp = h.http.post(f"/runs/{h.run_id}/recovery", json=extra)
    assert resp.status_code == 409
    assert resp.json()["code"] == "recovery_set_mismatch"


def test_recovery_from_dropped_sender_rejected(harness_factory):
    """掉线(不在冻结活跃集合)的客户端试图提交恢复 -> 400。"""
    ids = ["c1", "c2", "c3", "c4"]
    h = harness_factory(ids, threshold=2, vector_len=4)
    vectors = make_vectors(ids, 4)
    _drive_to_recovery(h, ids, vectors, dropped=["c4"])

    resp = h.http.post(f"/runs/{h.run_id}/recovery", json={
        "client_id": "c4",
        "b_shares": {v: _fake_share() for v in ["c1", "c2", "c3"]},
        "sk_shares": {"c4": _fake_share()},
    })
    assert resp.status_code == 400
    assert resp.json()["code"] == "sender_not_active"


def test_late_joiner_rejected_after_keys_phase(harness_factory):
    """轮0结束后新客户端注册/未知客户端注册 -> 明确拒绝。"""
    ids = ["c1", "c2", "c3"]
    h = harness_factory(ids, threshold=2, vector_len=4)

    # 未声明的客户端在轮0注册 -> 400
    resp = h.http.post(f"/runs/{h.run_id}/keys", json={
        "client_id": "ghost",
        "c_pk": base64.b64encode(os.urandom(32)).decode(),
        "s_pk": base64.b64encode(os.urandom(32)).decode(),
    })
    assert resp.status_code == 400
    assert resp.json()["code"] == "unknown_client"

    h.round0()
    # 轮0已结束, 声明内客户端再注册 -> 409 阶段冲突
    resp = h.http.post(f"/runs/{h.run_id}/keys", json={
        "client_id": "c1",
        "c_pk": base64.b64encode(os.urandom(32)).decode(),
        "s_pk": base64.b64encode(os.urandom(32)).decode(),
    })
    assert resp.status_code == 409
    assert resp.json()["code"] == "wrong_phase"


def test_masked_input_after_freeze_rejected(harness_factory):
    """活跃集合冻结后补交掩码输入(集合变更攻击)-> 409。"""
    ids = ["c1", "c2", "c3", "c4"]
    h = harness_factory(ids, threshold=2, vector_len=4)
    vectors = make_vectors(ids, 4)
    _drive_to_recovery(h, ids, vectors, dropped=["c4"])

    # c4 在冻结后试图补交掩码输入
    resp = h.http.post(f"/runs/{h.run_id}/masked", json={
        "client_id": "c4", "vector": [0] * 4})
    assert resp.status_code == 409
    assert resp.json()["code"] == "wrong_phase"


def test_masked_input_from_non_participant_rejected(harness_factory):
    """未参与轮1的客户端在轮2混入 -> 400。"""
    ids = ["c1", "c2", "c3", "c4"]
    h = harness_factory(ids, threshold=2, vector_len=4)
    vectors = make_vectors(ids, 4)
    h.round0()
    h.round1(participants=["c1", "c2", "c3"])  # c4 未参与轮1
    resp = h.http.post(f"/runs/{h.run_id}/masked", json={
        "client_id": "c4", "vector": [0] * 4})
    assert resp.status_code == 400
    assert resp.json()["code"] == "unknown_client"


def test_duplicate_masked_input_rejected(harness_factory):
    ids = ["c1", "c2", "c3"]
    h = harness_factory(ids, threshold=2, vector_len=4)
    vectors = make_vectors(ids, 4)
    h.round0()
    h.round1()
    h.clients["c1"].submit_masked_input(vectors["c1"])
    resp = h.http.post(f"/runs/{h.run_id}/masked", json={
        "client_id": "c1", "vector": [0] * 4})
    assert resp.status_code == 409
    assert resp.json()["code"] == "dup_masked"


def test_share_recipients_mismatch_rejected(harness_factory):
    """轮1份额接收方集合不完整(试图跳过某客户端)-> 400。"""
    ids = ["c1", "c2", "c3"]
    h = harness_factory(ids, threshold=2, vector_len=4)
    h.round0()
    resp = h.http.post(f"/runs/{h.run_id}/shares", json={
        "client_id": "c1",
        "ciphertexts": {"c2": base64.b64encode(os.urandom(48)).decode()},
        # 缺 c3
    })
    assert resp.status_code == 400
    assert resp.json()["code"] == "share_recipients_mismatch"


def test_client_refuses_tampered_active_set(harness_factory):
    """客户端侧防御: 服务器呈现被篡改的活跃集合时, 客户端拒绝响应恢复。"""

    class TamperingTransport:
        """包装真实传输, 在 active_set 响应里塞入幻影客户端。"""

        def __init__(self, inner):
            self._inner = inner

        def post(self, path, payload):
            return self._inner.post(path, payload)

        def get(self, path):
            out = self._inner.get(path)
            if path.endswith("/active_set"):
                out = dict(out)
                out["active"] = sorted(out["active"] + ["ghost"])
            return out

    ids = ["c1", "c2", "c3"]
    h = harness_factory(ids, threshold=2, vector_len=4)
    vectors = make_vectors(ids, 4)
    h.round0()
    h.round1()
    h.round2(vectors)

    victim = h.clients["c1"]
    victim.transport = TamperingTransport(victim.transport)
    with pytest.raises(SecAggError) as exc:
        victim.send_recovery()
    assert exc.value.category is ErrorCategory.STATE_CONFLICT
    assert exc.value.code == "set_changed"

    # 其余客户端不受影响, 运行正常完成
    h.round3(participants=["c2", "c3"])
    # c1 未提交恢复, 强制推进( t=2, c2/c3 的份额足够 )
    h.advance()
    assert h.result()["status"] == "DONE"


def test_client_refuses_second_different_recovery(harness_factory):
    """客户端本地防重放: 第二次被呈现不同冻结集合时拒绝再发。"""

    class FlipTransport:
        def __init__(self, inner, flipped):
            self._inner = inner
            self._flipped = flipped

        def post(self, path, payload):
            return self._inner.post(path, payload)

        def get(self, path):
            out = self._inner.get(path)
            if path.endswith("/active_set") and self._flipped:
                out = dict(out)
                out["active"] = list(reversed(out["active"])) + ["ghost"]
            return out

    ids = ["c1", "c2", "c3"]
    h = harness_factory(ids, threshold=2, vector_len=4)
    vectors = make_vectors(ids, 4)
    h.round0()
    h.round1()
    h.round2(vectors)

    victim = h.clients["c1"]
    real = victim.transport
    victim.send_recovery()  # 第一次正常提交
    victim.transport = FlipTransport(real, flipped=True)
    with pytest.raises(SecAggError) as exc:
        victim.send_recovery()
    assert exc.value.category is ErrorCategory.STATE_CONFLICT


def test_resource_limits_enforced(harness_factory):
    """资源耗尽类: 向量超长 / 客户端数超限 / 求和溢出上界。"""
    # 向量长度超限
    h = harness_factory(["c1", "c2", "c3"], threshold=2, vector_len=4)
    resp = h.http.post("/runs", json={
        "client_ids": ["a", "b", "c"], "threshold": 2,
        "vector_len": 10, "max_vector_len": 8})
    assert resp.status_code == 413
    assert resp.json()["category"] == "resource_exhausted"

    # 客户端数超过份额上限
    resp = h.http.post("/runs", json={
        "client_ids": [f"c{i}" for i in range(65)], "threshold": 2,
        "vector_len": 4})
    assert resp.status_code == 413
    assert resp.json()["code"] == "too_many_clients"

    # max_clients * elem_bound 超过 R/2 -> 防溢出拒绝
    resp = h.http.post("/runs", json={
        "client_ids": ["a", "b", "c"], "threshold": 2,
        "vector_len": 4, "elem_bound": 1 << 62})
    assert resp.status_code == 413
    assert resp.json()["code"] == "overflow_risk"
