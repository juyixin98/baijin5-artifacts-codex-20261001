"""Service-level query correctness: representations, collisions, domain
separation, and the uncertain-outcome path."""
from sensitive_layer.crypto.blind_index import compute_index
from sensitive_layer.protocol.framing import index_message
from tests.conftest import INDEX_KEY_V1


def test_same_value_different_representation(service):
    service.put_record(request_id="t1", record_id="r1", field="email",
                       purpose="lookup:email", value="  Alice@Example.COM ")
    result = service.query(request_id="t2", field="email",
                           purpose="lookup:email", value="alice@example.com")
    assert result["confirmed"] == ["r1"]
    assert result["filtered_candidates"] == 0
    assert result["uncertain"] == []


def test_same_value_across_records(service):
    for rid in ("r1", "r2"):
        service.put_record(request_id="t", record_id=rid, field="email",
                           purpose="lookup:email", value="alice@example.com")
    result = service.query(request_id="t", field="email",
                           purpose="lookup:email", value="ALICE@example.com")
    assert result["confirmed"] == ["r1", "r2"]


def test_absent_value_returns_empty(service):
    service.put_record(request_id="t", record_id="r1", field="email",
                       purpose="lookup:email", value="alice@example.com")
    result = service.query(request_id="t", field="email",
                           purpose="lookup:email", value="nobody@example.com")
    assert result["confirmed"] == []
    assert result["filtered_candidates"] == 0


def _colliding_pair(bits):
    """Two distinct emails whose short blind indexes collide (found via the
    core index function — this only constructs the test input; the assertion
    below is on query behaviour, not on the index itself)."""
    seen = {}
    n = 0
    while True:
        value = f"user{n}@example.com"
        msg = index_message("lookup:email", "nfkc-casefold-v1", value)
        idx = compute_index(INDEX_KEY_V1, msg, bits)
        if idx in seen and seen[idx] != value:
            return seen[idx], value
        seen[idx] = value
        n += 1


def test_forced_short_index_collision_is_filtered(make_service):
    service = make_service(index_bits=8)  # 256 buckets: collisions guaranteed
    value_a, value_b = _colliding_pair(8)
    service.put_record(request_id="t", record_id="ra", field="email",
                       purpose="lookup:email", value=value_a)
    service.put_record(request_id="t", record_id="rb", field="email",
                       purpose="lookup:email", value=value_b)

    result = service.query(request_id="t", field="email",
                           purpose="lookup:email", value=value_a)
    # Both records are index candidates; only the true match may be confirmed.
    assert result["confirmed"] == ["ra"]
    assert result["filtered_candidates"] == 1

    result_b = service.query(request_id="t", field="email",
                             purpose="lookup:email", value=value_b)
    assert result_b["confirmed"] == ["rb"]
    assert result_b["filtered_candidates"] == 1


def test_domain_separation_across_purposes(service):
    service.put_record(request_id="t", record_id="r1", field="email",
                       purpose="lookup:email", value="dave@example.com")
    # Same value, same field type, different purpose: must not match.
    result = service.query(request_id="t", field="email",
                           purpose="alias:email", value="dave@example.com")
    assert result["confirmed"] == []
    # And it is found under its own purpose.
    result = service.query(request_id="t", field="email",
                           purpose="lookup:email", value="dave@example.com")
    assert result["confirmed"] == ["r1"]


def test_undecryptable_candidate_reported_uncertain(service):
    service.put_record(request_id="t", record_id="r1", field="email",
                       purpose="lookup:email", value="alice@example.com")
    # Corrupt the stored ciphertext directly (simulates torn write / wrong key).
    row = service.repo.get_record("r1", "email", "lookup:email")
    blob = bytearray(row["ciphertext"])
    blob[-1] ^= 0x01
    service.repo.upsert_record("r1", "email", "lookup:email",
                               bytes(blob), row["enc_key_version"])

    result = service.query(request_id="t", field="email",
                           purpose="lookup:email", value="alice@example.com")
    assert result["confirmed"] == []
    assert result["uncertain"] == [{"record_id": "r1", "reason": "decrypt_failed"}]


def test_query_response_is_explainable(service):
    service.put_record(request_id="t", record_id="r1", field="email",
                       purpose="lookup:email", value="alice@example.com")
    result = service.query(request_id="t", field="email",
                           purpose="lookup:email", value="alice@example.com")
    assert result["index_versions_queried"] == [1]
    assert any("decrypt-confirm" in step for step in result["trace"])
    assert "leak equality" in result["notice"]
