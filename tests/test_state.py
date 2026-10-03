"""State-store tests: registration conflicts, quotas, audit persistence."""

from __future__ import annotations

import pytest

from kds.errors import ResourceExhaustedError, StateConflictError
from kds.identity import KeyIdentity
from kds.state import StateStore

IDENT = KeyIdentity(tenant="tenant-alpha", purpose="encryption", version=1, context="nightly")


def test_register_is_idempotent_for_same_display_name(store):
    assert store.register_key(IDENT, "nightly key", run_id="r1") is True
    assert store.register_key(IDENT, "nightly key", run_id="r2") is False


def test_conflicting_display_name_is_state_conflict(store):
    store.register_key(IDENT, "nightly key", run_id="r1")
    with pytest.raises(StateConflictError):
        store.register_key(IDENT, "renamed key", run_id="r2")


def test_get_key_returns_metadata_only(store):
    store.register_key(IDENT, "nightly key", run_id="r1")
    record = store.get_key(IDENT.key_id)
    assert record is not None
    assert record["tenant"] == "tenant-alpha"
    assert record["display_name"] == "nightly key"
    assert "key" not in record  # no key material column
    assert store.get_key("kds1-" + "0" * 32) is None


def test_tenant_quota_is_resource_exhausted(tmp_path):
    store = StateStore(str(tmp_path / "q.sqlite3"), max_keys_per_tenant=2)
    try:
        for i in range(2):
            ident = KeyIdentity(
                tenant="tenant-alpha", purpose="encryption", version=1, context=f"ctx-{i}"
            )
            store.register_key(ident, f"key {i}", run_id=f"r{i}")
        with pytest.raises(ResourceExhaustedError):
            store.register_key(
                KeyIdentity(
                    tenant="tenant-alpha", purpose="encryption", version=1, context="ctx-2"
                ),
                "key 2",
                run_id="r2",
            )
        # A different tenant is unaffected by the exhausted quota.
        store.register_key(
            KeyIdentity(tenant="tenant-beta", purpose="encryption", version=1, context="ctx-0"),
            "other tenant key",
            run_id="r3",
        )
    finally:
        store.close()


def test_audit_persists_across_reopen(tmp_path):
    path = str(tmp_path / "audit.sqlite3")
    store = StateStore(path)
    store.record_audit(
        run_id="run-1", event="derive", outcome="error", reason="input",
        key_id=IDENT.key_id, request_hash="abc123",
    )
    store.close()

    reopened = StateStore(path)
    try:
        entries = reopened.list_audit(run_id="run-1")
        assert len(entries) == 1
        assert entries[0]["reason"] == "input"
        assert entries[0]["outcome"] == "error"
    finally:
        reopened.close()
