"""Service-level tests: golden tree vectors, determinism, separation,
state conflicts, audit trail, and log hygiene."""

from __future__ import annotations

import json

import pytest

from keytree.crypto_adapter import MAX_OKM_LEN
from keytree.errors import ResourceExhaustedError, StateConflictError
from keytree.identity import KeyIdentity
from keytree.service import KeyTreeService

from .conftest import read_log


def test_matches_independent_golden_vectors(service, golden_tree):
    """Service (cryptography backend) must reproduce the vectors generated
    independently by tools/independent_vector_gen.py (PyCryptodome only)."""
    for vec in golden_tree:
        identity = KeyIdentity(
            tenant=vec["tenant"],
            purpose=vec["purpose"],
            version=vec["version"],
            context=bytes.fromhex(vec["context_hex"]),
        )
        result = service.derive(identity, length=vec["length"])
        assert result.key_id == vec["key_id"]
        assert result.key_bytes.hex() == vec["key_hex"]
        assert result.fingerprint == vec["fingerprint"]


def test_same_request_is_deterministic(service, identity):
    a = service.derive(identity)
    b = service.derive(identity)
    assert a.key_bytes == b.key_bytes
    assert a.key_id == b.key_id
    assert a.fingerprint == b.fingerprint
    assert a.run_id != b.run_id  # distinct runs, same key


def test_different_purposes_are_separated(service):
    enc = service.derive(KeyIdentity("acme", "encryption", 1, b""))
    sig = service.derive(KeyIdentity("acme", "signing", 1, b""))
    assert enc.key_bytes != sig.key_bytes
    assert enc.key_id != sig.key_id


def test_tenant_version_context_all_separate(service):
    base = KeyIdentity("acme", "encryption", 1, b"")
    others = [
        KeyIdentity("globex", "encryption", 1, b""),
        KeyIdentity("acme", "encryption", 2, b""),
        KeyIdentity("acme", "encryption", 1, b"ctx"),
    ]
    keys = {service.derive(base).key_bytes}
    for other in others:
        keys.add(service.derive(other).key_bytes)
    assert len(keys) == 4


def test_oversize_length_is_resource_exhausted(service, identity):
    with pytest.raises(ResourceExhaustedError) as excinfo:
        service.derive(identity, length=MAX_OKM_LEN + 1)
    assert excinfo.value.category == "resource_exhausted"


def test_failed_run_is_audited_with_category(service, identity, store):
    run_id = "deadbeef" * 4
    with pytest.raises(ResourceExhaustedError):
        service.derive(identity, length=MAX_OKM_LEN + 1, run_id=run_id)
    events = store.audit_for_run(run_id)
    assert len(events) == 1
    assert events[0]["outcome"] == "failure"
    assert events[0]["error_category"] == "resource_exhausted"


def test_conflicting_reregistration_is_state_conflict(service, identity, store):
    result = service.derive(identity)
    # simulate a tampered/foreign registry row for the same key_id
    store._conn.execute(
        "UPDATE registry SET fingerprint = ? WHERE key_id = ?",
        ("00" * 16, result.key_id),
    )
    store._conn.commit()
    with pytest.raises(StateConflictError) as excinfo:
        service.derive(identity)
    assert excinfo.value.category == "state_conflict"


def test_display_name_rebinding_is_state_conflict(service, store):
    a = service.derive(KeyIdentity("acme", "encryption", 1, b""), display_name="prod")
    b = service.derive(KeyIdentity("acme", "signing", 1, b""))
    with pytest.raises(StateConflictError):
        store.bind_name("prod", b.key_id)
    # rebinding the same name to the same key is idempotent
    store.bind_name("prod", a.key_id)


def test_successful_run_audit_and_log(service, identity, store, log_file):
    result = service.derive(identity)
    events = store.audit_for_run(result.run_id)
    assert [e["outcome"] for e in events] == ["success"]
    detail = json.loads(events[0]["detail_json"])
    assert detail["fingerprint"] == result.fingerprint

    records = read_log(log_file)
    run_records = [r for r in records if r["run_id"] == result.run_id]
    names = [r["event"] for r in run_records]
    assert names[0] == "input_validated"
    assert names[-1] == "completed"
    assert names.count("level_derived") == 4  # tenant, purpose, version, context
    for r in run_records:
        assert "rationale" in r


def test_key_material_never_appears_in_logs(service, identity, log_file, root_hex):
    result = service.derive(identity)
    content = log_file.read_text()
    assert root_hex not in content
    assert result.key_bytes.hex() not in content
    # safe identifiers MAY appear
    assert result.key_id in content
    assert result.fingerprint in content


def test_key_material_not_in_registry_or_audit(service, identity, store):
    result = service.derive(identity)
    row = store.get_registered(result.key_id)
    assert result.key_bytes.hex() not in json.dumps(row)
    events = store.audit_for_run(result.run_id)
    assert result.key_bytes.hex() not in json.dumps(events)
