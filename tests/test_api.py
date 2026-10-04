"""接口层端到端测试:完整流程、错误信封、失败类别、审计与版本可见性。"""
from phe.paillier import PaillierPublicKey

from app import crypto_adapter as crypto
from tests.reference import reference_weighted_sum


def _encrypt_for(batch, value):
    pub = PaillierPublicKey(int(batch["n"]))
    return crypto.serialize(crypto.encrypt_signed(pub, value))


def test_full_flow_over_http(client, rlog):
    # 1. 健康检查:版本与运行身份可见
    health = client.get("/health").json()["data"]
    assert health["run_id"]
    assert health["phe"] and health["python"]
    rlog("health", **health)

    # 2. 创建批次
    resp = client.post("/batches", json={"label": "e2e"})
    assert resp.status_code == 201
    batch = resp.json()["data"]
    batch_id = batch["batch_id"]

    # 3. 三个参与者提交密文
    values = {"alice": 4, "bob": -6, "carol": 10}
    weights = {"alice": 2, "bob": 3, "carol": -1}
    for pid, v in values.items():
        blob = _encrypt_for(batch, v)
        resp = client.post(f"/batches/{batch_id}/submissions", json={
            "participant_id": pid,
            "ciphertext": blob["c"],
            "exponent": blob["e"],
            "weight": str(weights[pid]),
            "key_fingerprint": batch["key_fingerprint"],
            "declared_abs": str(abs(v)),
        })
        assert resp.status_code == 201, resp.text

    # 4. 聚合 + 解密
    agg = client.post(f"/batches/{batch_id}/aggregate").json()["data"]
    assert agg["submission_count"] == 3
    dec = client.post(f"/batches/{batch_id}/decrypt").json()["data"]
    expected = reference_weighted_sum(list(values.values()),
                                      list(weights.values()))
    rlog("e2e-decrypt", values=values, weights=weights,
         expected=expected, actual=dec["value"], basis="独立参考直算")
    assert int(dec["value"]) == expected == -20  # 8 - 18 - 10

    # 5. 独立验证接口
    resp = client.post(f"/batches/{batch_id}/verify", json={
        "reference": [{"participant_id": p, "value": str(v)}
                      for p, v in values.items()]
    })
    verdict = resp.json()["data"]
    assert verdict["verdict"] == "MATCH"

    # 6. 审计可回溯且带 run_id
    audit = client.get(f"/batches/{batch_id}/audit").json()["data"]
    events = [e["event"] for e in audit]
    assert "BATCH_CREATED" in events and "VERIFY_MATCH" in events
    assert all(e["run_id"] == health["run_id"] for e in audit)


def test_error_envelope_and_categories(client, rlog):
    batch = client.post("/batches", json={}).json()["data"]
    batch_id = batch["batch_id"]

    # 未知批次 -> 404 NOT_FOUND
    resp = client.get("/batches/nope")
    assert resp.status_code == 404
    body = resp.json()
    assert body["ok"] is False and body["error"]["category"] == "NOT_FOUND"

    # 非法密文 -> 400 VALIDATION,绝不返回成功
    resp = client.post(f"/batches/{batch_id}/submissions", json={
        "participant_id": "x", "ciphertext": "not-a-number",
        "exponent": 0, "weight": "1",
        "key_fingerprint": batch["key_fingerprint"], "declared_abs": "1",
    })
    assert resp.status_code == 400
    assert resp.json()["error"]["category"] == "VALIDATION"

    # 错误指纹 -> 409 KEY_MISMATCH
    blob = _encrypt_for(batch, 1)
    resp = client.post(f"/batches/{batch_id}/submissions", json={
        "participant_id": "x", "ciphertext": blob["c"],
        "exponent": 0, "weight": "1",
        "key_fingerprint": "f" * 64, "declared_abs": "1",
    })
    assert resp.status_code == 409
    assert resp.json()["error"]["category"] == "KEY_MISMATCH"
    rlog("error-categories", checked=["NOT_FOUND", "VALIDATION", "KEY_MISMATCH"],
         basis="失败必须带类别且 ok=false")


def test_unsupported_ciphertext_multiplication(client):
    batch = client.post("/batches", json={}).json()["data"]
    resp = client.post(f"/batches/{batch['batch_id']}/multiply-ciphertexts")
    assert resp.status_code == 400
    body = resp.json()
    assert body["ok"] is False
    assert body["error"]["category"] == "UNSUPPORTED_OPERATION"


def test_decrypt_before_aggregate_is_not_found(client):
    batch = client.post("/batches", json={}).json()["data"]
    resp = client.post(f"/batches/{batch['batch_id']}/decrypt")
    assert resp.status_code == 404
    assert resp.json()["error"]["category"] == "NOT_FOUND"
