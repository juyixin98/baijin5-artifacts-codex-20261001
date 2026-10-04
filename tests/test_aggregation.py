"""Service-level aggregation tests.

Expected values are hand-computed plaintext arithmetic; ciphertext-side
expectations come from tests/reference.py (independent implementation),
never from the service's own code.
"""

import pytest

from app import crypto_adapter
from app.encoding import EncodingParams
from app.errors import ErrorCategory, PaillierServiceError
from app.service import STATE_AGGREGATED, STATE_DECRYPTED, STATE_OPEN
from app.verifier import STATUS_FAIL, STATUS_PASS, IndependentVerifier
from tests.reference import reference_encode, reference_weighted_sum

from tests.conftest import TEST_PARAMS, run_logger


def _submit(service, run_id, batch, participant, value, weight, with_fixture=True):
    """Encrypt client-side, then submit ciphertext + coefficient."""
    ciphertext = service.client_encrypt(batch["batch_id"], value)
    return service.submit_contribution(
        run_id,
        batch_id=batch["batch_id"],
        participant_id=participant,
        key_id=batch["key_id"],
        ciphertext=ciphertext,
        coefficient=weight,
        plaintext_fixture=value if with_fixture else None,
    )


def test_hand_computed_weighted_sum(service, batch, run_id):
    # values [3, -2, 7], weights [2, 5, -1] -> 6 - 10 - 7 = -11 (by hand)
    values, weights = [3, -2, 7], [2, 5, -1]
    for i, (v, w) in enumerate(zip(values, weights)):
        _submit(service, run_id, batch, f"participant-{i}", v, w)
    service.aggregate(run_id, batch["batch_id"])
    result = service.decrypt_result(run_id, batch["batch_id"])
    expected = reference_weighted_sum(values, weights)
    run_logger.info(
        "CASE hand_computed run_id=%s inputs=%s weights=%s expected=%s actual=%s",
        run_id, values, weights, expected, result["plaintext"],
    )
    assert expected == -11  # guard the reference itself
    assert result["plaintext"] == -11


def test_negative_weights_produce_negative_total(service, batch, run_id):
    # values [10, 4], weights [-3, 2] -> -30 + 8 = -22 (by hand)
    values, weights = [10, 4], [-3, 2]
    for i, (v, w) in enumerate(zip(values, weights)):
        _submit(service, run_id, batch, f"p{i}", v, w)
    service.aggregate(run_id, batch["batch_id"])
    result = service.decrypt_result(run_id, batch["batch_id"])
    assert result["plaintext"] == -22


def test_zero_coefficient_contributes_nothing(service, batch, run_id):
    _submit(service, run_id, batch, "p0", 5, 0)
    _submit(service, run_id, batch, "p1", 9, 1)
    service.aggregate(run_id, batch["batch_id"])
    result = service.decrypt_result(run_id, batch["batch_id"])
    assert result["plaintext"] == 9


def test_client_encrypt_rejects_out_of_range_plaintext(service, batch, run_id):
    # max_plaintext_abs = 1000 in TEST_PARAMS
    with pytest.raises(PaillierServiceError) as excinfo:
        service.client_encrypt(batch["batch_id"], 1001)
    assert excinfo.value.category == ErrorCategory.PLAINTEXT_OUT_OF_RANGE


def test_coefficient_out_of_range_rejected(service, batch, run_id):
    ciphertext = service.client_encrypt(batch["batch_id"], 1)
    with pytest.raises(PaillierServiceError) as excinfo:
        service.submit_contribution(
            run_id, batch["batch_id"], "p0", batch["key_id"],
            ciphertext, coefficient=101,  # max_coefficient_abs = 100
        )
    assert excinfo.value.category == ErrorCategory.COEFFICIENT_OUT_OF_RANGE


def test_mixed_keys_rejected(service, batch, run_id):
    other = service.create_batch(run_id, label="other-batch", params=TEST_PARAMS)
    # Encrypt under the OTHER batch's key, present its key_id to this batch.
    ciphertext = service.client_encrypt(other["batch_id"], 5)
    with pytest.raises(PaillierServiceError) as excinfo:
        service.submit_contribution(
            run_id, batch["batch_id"], "p0", other["key_id"], ciphertext, 1
        )
    assert excinfo.value.category == ErrorCategory.KEY_MISMATCH
    # Rejection is audited.
    events = [r["event"] for r in service.storage.list_audit(batch["batch_id"])]
    assert "CONTRIBUTION_REJECTED" in events


def test_spoofed_key_id_fails_closed_at_decrypt_or_verify(service, batch, run_id):
    """A client that lies about key_id can get a foreign ciphertext stored
    (the server cannot detect this cryptographically — documented trust
    assumption), but the pipeline must fail closed: decryption lands in the
    ambiguous zone OR the verifier flags the mismatch.  It must never
    surface as a plausible-looking plaintext."""
    other = service.create_batch(run_id, label="foreign", params=TEST_PARAMS)
    foreign_ciphertext = service.client_encrypt(other["batch_id"], 5)
    try:
        service.submit_contribution(
            run_id, batch["batch_id"], "mallory", batch["key_id"],
            foreign_ciphertext, 1, plaintext_fixture=5,
        )
    except PaillierServiceError as exc:
        # Foreign ciphertext is not even a residue mod this batch's n^2.
        assert exc.category == ErrorCategory.CIPHERTEXT_INVALID
        return
    service.aggregate(run_id, batch["batch_id"])
    try:
        result = service.decrypt_result(run_id, batch["batch_id"])
    except PaillierServiceError as exc:
        assert exc.category == ErrorCategory.DECODE_AMBIGUOUS
        return
    # If decryption happened to yield an in-range residue, the verifier
    # must catch the disagreement instead.
    report = IndependentVerifier(service.storage).verify(batch["batch_id"])
    assert report.status == STATUS_FAIL
    assert result["plaintext"] != 5


def test_aggregate_bound_exceeded_rejected(service, run_id):
    params = EncodingParams(
        max_plaintext_abs=100, max_coefficient_abs=10, max_aggregate_abs=150
    )
    batch = service.create_batch(run_id, label="tight-bound", params=params)
    # Each contribution with |w|=1 reserves worst-case 100; the second
    # would push bound_used to 200 > 150.
    _submit(service, run_id, batch, "p0", 1, 1)
    with pytest.raises(PaillierServiceError) as excinfo:
        _submit(service, run_id, batch, "p1", 1, 1)
    assert excinfo.value.category == ErrorCategory.AGGREGATE_BOUND_EXCEEDED


def test_decode_ambiguous_on_malicious_plaintext(service, run_id):
    """A malicious client bypasses client-side range checks (encrypting a
    huge plaintext directly).  The sum leaves the guaranteed range, so
    decrypt must raise DECODE_AMBIGUOUS — never return a 'negative' value."""
    params = EncodingParams(
        max_plaintext_abs=10, max_coefficient_abs=10, max_aggregate_abs=50
    )
    batch = service.create_batch(run_id, label="malicious", params=params)
    public_key = crypto_adapter.reconstruct_public_key(int(batch["public_key_n"]))
    # Bypass encoding.encode_signed: encrypt 1000 directly (|1000| > 10).
    evil = crypto_adapter.encrypt_encoded(public_key, 1000)
    service.submit_contribution(
        run_id, batch["batch_id"], "mallory", batch["key_id"], evil, 1
    )
    service.aggregate(run_id, batch["batch_id"])
    with pytest.raises(PaillierServiceError) as excinfo:
        service.decrypt_result(run_id, batch["batch_id"])
    assert excinfo.value.category == ErrorCategory.DECODE_AMBIGUOUS
    # The failure is audited, not swallowed.
    events = [r["event"] for r in service.storage.list_audit(batch["batch_id"])]
    assert "DECODE_FAILED" in events


def test_state_machine(service, batch, run_id):
    assert service.describe_batch(batch["batch_id"])["state"] == STATE_OPEN
    with pytest.raises(PaillierServiceError) as excinfo:
        service.aggregate(run_id, batch["batch_id"])  # empty batch
    assert excinfo.value.category == ErrorCategory.BATCH_STATE_INVALID
    _submit(service, run_id, batch, "p0", 1, 1)
    service.aggregate(run_id, batch["batch_id"])
    assert service.describe_batch(batch["batch_id"])["state"] == STATE_AGGREGATED
    with pytest.raises(PaillierServiceError) as excinfo:
        _submit(service, run_id, batch, "p1", 1, 1)  # closed after aggregate
    assert excinfo.value.category == ErrorCategory.BATCH_STATE_INVALID
    service.decrypt_result(run_id, batch["batch_id"])
    assert service.describe_batch(batch["batch_id"])["state"] == STATE_DECRYPTED


def test_unknown_batch_raises_not_found(service, run_id):
    with pytest.raises(PaillierServiceError) as excinfo:
        service.describe_batch("no-such-batch")
    assert excinfo.value.category == ErrorCategory.BATCH_NOT_FOUND


def test_verification_pass_on_honest_run(service, batch, run_id):
    values, weights = [8, -3, 2], [1, 4, -5]
    for i, (v, w) in enumerate(zip(values, weights)):
        _submit(service, run_id, batch, f"p{i}", v, w)
    service.aggregate(run_id, batch["batch_id"])
    service.decrypt_result(run_id, batch["batch_id"])
    report = IndependentVerifier(service.storage).verify(batch["batch_id"])
    run_logger.info(
        "CASE verify_pass run_id=%s expected=%s report=%s",
        run_id, reference_weighted_sum(values, weights), report.to_dict(),
    )
    assert report.status == STATUS_PASS
    assert report.expected_plaintext == 8 - 12 - 10  # = -14 by hand
    assert report.service_plaintext == -14
    assert report.independent_plaintext == -14


def test_verification_fails_on_tampered_fixture(service, batch, run_id):
    """If the recorded plaintext fixture is tampered with after the fact,
    the verifier must report FAIL — proving the check is not vacuous."""
    _submit(service, run_id, batch, "p0", 6, 2)
    _submit(service, run_id, batch, "p1", 1, 1)
    service.aggregate(run_id, batch["batch_id"])
    service.decrypt_result(run_id, batch["batch_id"])
    # Tamper with the stored fixture directly in the database.
    with service.storage._lock, service.storage._conn:
        service.storage._conn.execute(
            "UPDATE contributions SET plaintext_fixture = '999' "
            "WHERE batch_id = ? AND participant_id = 'p0'",
            (batch["batch_id"],),
        )
    report = IndependentVerifier(service.storage).verify(batch["batch_id"])
    assert report.status == STATUS_FAIL
    assert report.expected_plaintext == 999 * 2 + 1
    assert report.service_plaintext == 13


def test_verification_unverifiable_without_fixtures(service, batch, run_id):
    _submit(service, run_id, batch, "p0", 4, 1, with_fixture=False)
    service.aggregate(run_id, batch["batch_id"])
    service.decrypt_result(run_id, batch["batch_id"])
    report = IndependentVerifier(service.storage).verify(batch["batch_id"])
    assert report.status == "UNVERIFIABLE"
    assert report.status != STATUS_PASS  # never a silent pass


def test_raw_residue_matches_independent_reference(service, batch, run_id):
    _submit(service, run_id, batch, "p0", -7, 3)
    service.aggregate(run_id, batch["batch_id"])
    result = service.decrypt_result(run_id, batch["batch_id"])
    n = int(service.describe_batch(batch["batch_id"])["public_key_n"])
    assert int(result["raw_residue"]) == reference_encode(-21, n)
    assert result["plaintext"] == -21
