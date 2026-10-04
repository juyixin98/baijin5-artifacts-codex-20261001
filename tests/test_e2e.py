"""端到端测试:3-5 客户端、不同阶段掉线、重复恢复消息、阈值中止.

每个用例断言具体聚合结果(对照独立明文参考)或具体失败类别,
不只检查接口可调用。失败时 conftest 自动转储带 run_id 的审计日志。
"""

from secagg.encoding import decode_vector

from .reference import quantization_tolerance, reference_sum

# 确定性明文夹具: 值域远小于 elem_bound/scale, 含负数与小数
def make_vectors(client_ids: list[str], length: int) -> dict[str, list[float]]:
    return {
        cid: [((i + 1) * 1.5 + j * 0.25 - 3.0) for j in range(length)]
        for i, cid in enumerate(sorted(client_ids))
    }


def assert_sum_matches(result: dict, expected_vectors: dict[str, list[float]],
                       params) -> None:
    assert result["status"] == "DONE"
    decoded = decode_vector(result["sum"], params)
    expected = reference_sum(expected_vectors)
    tol = quantization_tolerance(len(expected_vectors), params.scale)
    assert len(decoded) == len(expected)
    for got, want in zip(decoded, expected):
        assert abs(got - want) <= tol, f"got={got} want={want} tol={tol}"


def test_three_clients_no_dropout(harness_factory):
    ids = ["c1", "c2", "c3"]
    h = harness_factory(ids, threshold=2, vector_len=4)
    vectors = make_vectors(ids, 4)
    h.round0()
    h.round1()
    h.round2(vectors)
    h.round3()
    assert_sum_matches(h.result(), vectors, h.params)


def test_five_clients_no_dropout(harness_factory):
    ids = ["a", "b", "c", "d", "e"]
    h = harness_factory(ids, threshold=3, vector_len=8)
    vectors = make_vectors(ids, 8)
    h.round0()
    h.round1()
    h.round2(vectors)
    h.round3()
    assert_sum_matches(h.result(), vectors, h.params)


def test_dropout_before_masked_input_recovers(harness_factory):
    """c3 在轮2前掉线: 其成对掩码由服务器重建私钥后抵消,
    总和 = 存活客户端明文之和。"""
    ids = ["c1", "c2", "c3", "c4"]
    h = harness_factory(ids, threshold=3, vector_len=4)
    vectors = make_vectors(ids, 4)
    h.round0()
    h.round1()
    alive = ["c1", "c2", "c4"]
    h.round2(vectors, participants=alive)
    sets = h.clients["c1"].transport.get(f"/runs/{h.run_id}/active_set")
    assert sets["active"] == alive
    assert sets["dropped"] == ["c3"]
    h.round3(participants=alive)
    expected = {c: vectors[c] for c in alive}
    assert_sum_matches(h.result(), expected, h.params)


def test_dropout_after_masked_input_recovers(harness_factory):
    """c2 提交掩码输入后掉线(轮3不响应): 其输入仍在总和中,
    其余存活客户端份额足够门限, 强制推进后完成。"""
    ids = ["c1", "c2", "c3", "c4"]
    h = harness_factory(ids, threshold=3, vector_len=4)
    vectors = make_vectors(ids, 4)
    h.round0()
    h.round1()
    h.round2(vectors)  # 全员提交掩码输入
    h.round3(participants=["c1", "c3", "c4"])  # c2 掉线
    h.advance()  # 强制推进: c2 的恢复消息缺失, 但份额已达门限
    assert_sum_matches(h.result(), vectors, h.params)


def test_dropout_in_round1_excluded_from_sum(harness_factory):
    """c4 注册密钥后掉线(轮1不参与): 不进入参与者集合,
    总和只含完成轮1的客户端。"""
    ids = ["c1", "c2", "c3", "c4"]
    h = harness_factory(ids, threshold=2, vector_len=4)
    vectors = make_vectors(ids, 4)
    h.round0()
    alive = ["c1", "c2", "c3"]
    h.round1(participants=alive)
    h.round2(vectors, participants=alive)
    h.round3(participants=alive)
    expected = {c: vectors[c] for c in alive}
    assert_sum_matches(h.result(), expected, h.params)


def test_below_threshold_aborts_explicitly(harness_factory):
    """5 客户端 t=3, 仅 2 人提交掩码输入 -> 明确中止, 不输出结果。"""
    ids = ["a", "b", "c", "d", "e"]
    h = harness_factory(ids, threshold=3, vector_len=4)
    vectors = make_vectors(ids, 4)
    h.round0()
    h.round1()
    for cid in ["a", "b"]:
        h.clients[cid].submit_masked_input(vectors[cid])
    resp = h.http.post(f"/runs/{h.run_id}/advance")
    assert resp.status_code == 422
    body = resp.json()
    assert body["category"] == "computation_failure"
    assert body["code"] == "below_threshold"

    result = h.result()
    assert result["status"] == "ABORTED"
    assert "低于门限" in result["reason"]


def test_duplicate_recovery_same_content_is_idempotent(harness_factory):
    """同一客户端重复提交逐字节相同的恢复消息 -> 幂等接受, 不出错。"""
    ids = ["c1", "c2", "c3"]
    h = harness_factory(ids, threshold=2, vector_len=4)
    vectors = make_vectors(ids, 4)
    h.round0()
    h.round1()
    h.round2(vectors)
    first = h.clients["c1"].send_recovery()
    assert first["recorded"] and not first["idempotent_replay"]
    replay = h.clients["c1"].send_recovery()
    assert replay["idempotent_replay"] is True
    h.round3(participants=["c2", "c3"])
    assert_sum_matches(h.result(), vectors, h.params)


def test_duplicate_recovery_different_content_rejected(harness_factory):
    """同一客户端重复提交内容不同的恢复消息 -> 409 状态冲突。"""
    import base64
    import os

    ids = ["c1", "c2", "c3"]
    h = harness_factory(ids, threshold=2, vector_len=4)
    vectors = make_vectors(ids, 4)
    h.round0()
    h.round1()
    h.round2(vectors)
    h.clients["c1"].send_recovery()

    forged = {
        "client_id": "c1",
        "b_shares": {v: [1, base64.b64encode(os.urandom(66)).decode()]
                     for v in ids},
        "sk_shares": {},
    }
    resp = h.http.post(f"/runs/{h.run_id}/recovery", json=forged)
    assert resp.status_code == 409
    assert resp.json()["category"] == "state_conflict"
    assert resp.json()["code"] == "dup_recovery"

    # 攻击未破坏运行: 其余客户端正常完成
    h.round3(participants=["c2", "c3"])
    assert_sum_matches(h.result(), vectors, h.params)


def test_two_dropouts_at_different_stages(harness_factory):
    """组合场景: c5 轮1掉线, c2 轮2掉线, 其余 3 人完成, t=3。"""
    ids = ["c1", "c2", "c3", "c4", "c5"]
    h = harness_factory(ids, threshold=3, vector_len=6)
    vectors = make_vectors(ids, 6)
    h.round0()
    h.round1(participants=["c1", "c2", "c3", "c4"])  # c5 掉线
    alive = ["c1", "c3", "c4"]
    h.round2(vectors, participants=alive)            # c2 掉线
    h.round3(participants=alive)
    expected = {c: vectors[c] for c in alive}
    assert_sum_matches(h.result(), expected, h.params)
