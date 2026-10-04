"""服务层聚合测试:手算小整数、负权、超范围、混密钥、回绕拒绝。

参考答案来自 tests/reference.py(独立实现)或手算字面量。
"""
import pytest
from phe.paillier import PaillierPublicKey

from app import crypto_adapter as crypto
from app.errors import ErrorCategory, ServiceError
from tests.reference import reference_weighted_sum


def _submit_plaintext(service, batch, participant, value, weight,
                      declared_abs=None, fingerprint=None):
    """本地参与者:用批次公钥加密后提交(模拟外部参与者的正常流程)。"""
    pub = PaillierPublicKey(int(batch["n"]))
    enc = crypto.encrypt_signed(pub, value)
    blob = crypto.serialize(enc)
    return service.submit(
        batch_id=batch["batch_id"],
        participant_id=participant,
        c=int(blob["c"]),
        exponent=blob["e"],
        weight=weight,
        key_fingerprint=fingerprint or batch["key_fingerprint"],
        declared_abs=declared_abs if declared_abs is not None else abs(value),
    )


def test_weighted_sum_matches_hand_computed(service, rlog):
    batch = service.create_batch(label="hand-sum")
    values = [3, -2, 7]
    weights = [2, -1, 1]
    expected = 15  # 手算: 3*2 + (-2)*(-1) + 7*1 = 6 + 2 + 7
    assert reference_weighted_sum(values, weights) == expected

    for i, (v, w) in enumerate(zip(values, weights)):
        _submit_plaintext(service, batch, f"p{i}", v, w)
    service.aggregate(batch["batch_id"])
    got = int(service.decrypt(batch["batch_id"])["value"])
    rlog("weighted-sum", values=values, weights=weights,
         expected=expected, actual=got, basis="手算加权和")
    assert got == expected

    verdict = service.verify(
        batch["batch_id"],
        [(f"p{i}", v) for i, v in enumerate(values)])
    rlog("verify", verdict=verdict["verdict"], reason=verdict["reason"])
    assert verdict["verdict"] == "MATCH"


def test_negative_weights_only(service, rlog):
    batch = service.create_batch(label="neg-weights")
    values, weights = [5, 5], [-1, -2]
    expected = reference_weighted_sum(values, weights)  # -15
    for i, (v, w) in enumerate(zip(values, weights)):
        _submit_plaintext(service, batch, f"p{i}", v, w)
    service.aggregate(batch["batch_id"])
    got = int(service.decrypt(batch["batch_id"])["value"])
    rlog("negative-weights", values=values, weights=weights,
         expected=expected, actual=got, basis="负权和应为负值而非回绕")
    assert got == expected == -15


def test_submit_rejects_weight_out_of_range(service):
    batch = service.create_batch(label="bad-weight")
    too_big = service.settings.max_weight_abs + 1
    with pytest.raises(ServiceError) as exc_info:
        _submit_plaintext(service, batch, "p0", 1, too_big)
    assert exc_info.value.category is ErrorCategory.OUT_OF_RANGE


def test_submit_rejects_declared_abs_out_of_range(service):
    batch = service.create_batch(label="bad-declared")
    with pytest.raises(ServiceError) as exc_info:
        _submit_plaintext(service, batch, "p0", 1, 1,
                          declared_abs=service.settings.max_plaintext_abs + 1)
    assert exc_info.value.category is ErrorCategory.OUT_OF_RANGE


def test_submit_rejects_overflow_risk(service, rlog):
    batch = service.create_batch(label="tight-bound", sum_bound=100)
    _submit_plaintext(service, batch, "p0", 60, 1)   # used=60
    rlog("overflow-risk", used=60, next_risk=50, bound=100,
         basis="60+50>100 必须拒绝,防止模回绕")
    with pytest.raises(ServiceError) as exc_info:
        _submit_plaintext(service, batch, "p1", 50, 1)
    assert exc_info.value.category is ErrorCategory.OVERFLOW_RISK


def test_submit_rejects_key_mismatch(service):
    batch = service.create_batch(label="key-binding")
    with pytest.raises(ServiceError) as exc_info:
        _submit_plaintext(service, batch, "p0", 1, 1,
                          fingerprint="0" * 64)
    assert exc_info.value.category is ErrorCategory.KEY_MISMATCH


def test_mixed_key_ciphertext_is_detected(service, rlog):
    """用另一把密钥加密的密文提交到本批次:要么范围校验拒绝,
    要么解密后与明文参考不一致(VERIFY_MISMATCH / DECRYPT_OVERFLOW)。"""
    batch = service.create_batch(label="mixed-keys")
    _submit_plaintext(service, batch, "honest", 4, 1)

    other_pub, _ = crypto.generate_keypair(service.settings.key_size)
    foreign = crypto.serialize(crypto.encrypt_signed(other_pub, 100))
    try:
        service.submit(batch_id=batch["batch_id"], participant_id="intruder",
                       c=int(foreign["c"]), exponent=foreign["e"], weight=1,
                       key_fingerprint=batch["key_fingerprint"],
                       declared_abs=100)
        rejected_at_submit = False
    except ServiceError as exc:
        assert exc.category is ErrorCategory.OUT_OF_RANGE
        rejected_at_submit = True
    rlog("mixed-key-submit", rejected_at_submit=rejected_at_submit,
         basis="外key密文应在提交或验证阶段被发现")

    if rejected_at_submit:
        return
    service.aggregate(batch["batch_id"])
    verdict = service.verify(batch["batch_id"], [("honest", 4),
                                                 ("intruder", 100)])
    rlog("mixed-key-verify", verdict=verdict["verdict"],
         reason=verdict["reason"], basis="混入密文不得静默通过")
    assert verdict["verdict"] in ("MISMATCH", "DECRYPT_OVERFLOW")


def test_decrypt_refuses_wraparound_from_dishonest_declaration(service, rlog):
    """参与者低报 declared_abs 绕过事前上界:解码阶段必须拒绝回绕结果。"""
    batch = service.create_batch(label="dishonest", sum_bound=100)
    # 真实值 80,但声明 10;两次后真实和 160 > 上界 100
    _submit_plaintext(service, batch, "liar0", 80, 1, declared_abs=10)
    _submit_plaintext(service, batch, "liar1", 80, 1, declared_abs=10)
    service.aggregate(batch["batch_id"])
    rlog("wraparound", true_sum=160, bound=100,
         basis="真实和越界,解码必须 OVERFLOW_DETECTED 而非返回回绕值")
    with pytest.raises(ServiceError) as exc_info:
        service.decrypt(batch["batch_id"])
    assert exc_info.value.category is ErrorCategory.OVERFLOW_DETECTED


def test_aggregate_requires_submissions(service):
    batch = service.create_batch(label="empty")
    with pytest.raises(ServiceError) as exc_info:
        service.aggregate(batch["batch_id"])
    assert exc_info.value.category is ErrorCategory.VALIDATION


def test_decrypt_requires_aggregate(service):
    batch = service.create_batch(label="no-agg")
    with pytest.raises(ServiceError) as exc_info:
        service.decrypt(batch["batch_id"])
    assert exc_info.value.category is ErrorCategory.NOT_FOUND


def test_unknown_batch(service):
    with pytest.raises(ServiceError) as exc_info:
        service.get_batch("no-such-batch")
    assert exc_info.value.category is ErrorCategory.NOT_FOUND


def test_audit_trail_records_run_and_steps(service):
    batch = service.create_batch(label="audit")
    _submit_plaintext(service, batch, "p0", 1, 1)
    service.aggregate(batch["batch_id"])
    service.decrypt(batch["batch_id"])
    events = [e["event"] for e in service.list_audit(batch["batch_id"])]
    assert events == ["BATCH_CREATED", "SUBMISSION_ACCEPTED",
                      "AGGREGATE_COMPUTED", "DECRYPT_OK"]
    for entry in service.list_audit(batch["batch_id"]):
        assert entry["run_id"] == service.run_id
