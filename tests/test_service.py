"""Service-level tests: determinism, separation, identity, error taxonomy,
log hygiene, and independent recomputation of the derivation tree."""

from __future__ import annotations

import logging

import pytest

from kds import service as service_module
from kds.crypto_adapter import MAX_DERIVE_LEN, hkdf_sha256_reference
from kds.encoding import encode_fields
from kds.errors import (
    ComputationError,
    InputError,
    ResourceExhaustedError,
)
from kds.identity import KeyIdentity
from kds.logging_config import assert_clean_of
from kds.service import TREE_KEY_LEN, DerivationTreeService
from kds.state import StateStore

IDENT = KeyIdentity(tenant="tenant-alpha", purpose="encryption", version=1, context="nightly")


def independent_tree_derive(root: bytes, identity: KeyIdentity, length: int) -> bytes:
    """Recompute the expected output with the *reference* backend, so the
    expected value is not produced by the primary implementation path."""
    node = root
    for level, value in (
        ("tenant", identity.tenant),
        ("purpose", identity.purpose),
        ("version", identity.version),
        ("context", identity.context),
    ):
        node = hkdf_sha256_reference(
            node, encode_fields("kds1", "level", level, value), TREE_KEY_LEN
        )
    return hkdf_sha256_reference(
        node, encode_fields("kds1", "output", identity.key_id), length
    )


def test_determinism_same_request_same_key(service):
    a = service.derive(IDENT)
    b = service.derive(IDENT)
    assert a.key == b.key
    assert a.key_id == b.key_id
    assert a.run_id != b.run_id  # runs are distinguishable for replay


def test_output_matches_independent_reference(service, root_key):
    derived = service.derive(IDENT, length=48)
    assert derived.key == independent_tree_derive(root_key, IDENT, 48)


def test_purpose_separation(service):
    enc = service.derive(IDENT)
    sig = service.derive(
        KeyIdentity(tenant="tenant-alpha", purpose="signing", version=1, context="nightly")
    )
    assert enc.key != sig.key
    assert enc.key_id != sig.key_id


def test_tenant_version_context_separation(service):
    base = service.derive(IDENT).key
    variants = [
        KeyIdentity(tenant="tenant-beta", purpose="encryption", version=1, context="nightly"),
        KeyIdentity(tenant="tenant-alpha", purpose="encryption", version=2, context="nightly"),
        KeyIdentity(tenant="tenant-alpha", purpose="encryption", version=1, context="weekly"),
    ]
    for variant in variants:
        assert service.derive(variant).key != base


def test_label_boundary_collision_counterexample(service):
    """Tenant "ab" + purpose "c" vs tenant "a" + purpose "bc" must not
    collide, even though naive string joining would merge them."""
    left = service.derive(
        KeyIdentity(tenant="ab", purpose="c", version=1, context="x")
    )
    right = service.derive(
        KeyIdentity(tenant="a", purpose="bc", version=1, context="x")
    )
    assert left.key != right.key
    assert left.key_id != right.key_id


def test_display_name_does_not_affect_identity(service, tenant_fixtures):
    # Fixture tenants alpha and gamma share a display name on purpose.
    alpha = KeyIdentity(tenant="tenant-alpha", purpose="encryption", version=1, context="nightly")
    gamma = KeyIdentity(tenant="tenant-gamma", purpose="encryption", version=1, context="nightly")
    service.register(alpha, "Alpha (synthetic)")
    service.register(gamma, "Alpha (synthetic)")
    assert alpha.key_id != gamma.key_id
    assert service.derive(alpha).key != service.derive(gamma).key


def test_invalid_label_is_input_error(service):
    with pytest.raises(InputError):
        KeyIdentity(tenant="Tenant With Spaces", purpose="encryption", version=1, context="x")
    with pytest.raises(InputError):
        KeyIdentity(tenant="tenant-alpha", purpose="", version=1, context="x")


def test_invalid_version_is_input_error():
    with pytest.raises(InputError):
        KeyIdentity(tenant="t", purpose="p", version=0, context="c")


def test_length_bounds_error_categories(service):
    with pytest.raises(InputError):
        service.derive(IDENT, length=0)
    with pytest.raises(ResourceExhaustedError):
        service.derive(IDENT, length=MAX_DERIVE_LEN + 1)


def test_backend_failure_is_computation_error(service, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("backend exploded")

    monkeypatch.setattr(service_module, "hkdf_sha256", boom)
    with pytest.raises(ComputationError):
        service.derive(IDENT)


def test_audit_trail_records_run_and_reason(service, store):
    ok = service.derive(IDENT)
    with pytest.raises(ResourceExhaustedError):
        service.derive(IDENT, length=MAX_DERIVE_LEN + 1)

    ok_entries = store.list_audit(run_id=ok.run_id)
    assert len(ok_entries) == 1
    assert ok_entries[0]["event"] == "derive"
    assert ok_entries[0]["outcome"] == "ok"
    assert ok_entries[0]["key_id"] == IDENT.key_id
    assert ok_entries[0]["request_hash"]

    errors = [e for e in store.list_audit() if e["outcome"] == "error"]
    assert len(errors) == 1
    assert errors[0]["reason"] == "resource_exhausted"


def test_no_key_material_in_logs_or_audit(root_key, store, caplog):
    logger = logging.getLogger("kds.test-hygiene")
    svc = DerivationTreeService(root_key, store, logger=logger)
    with caplog.at_level(logging.DEBUG, logger="kds.test-hygiene"):
        derived = svc.derive(IDENT)
        svc.register(IDENT, "nightly key")
        with pytest.raises(ResourceExhaustedError):
            svc.derive(IDENT, length=MAX_DERIVE_LEN + 1)

    assert_clean_of(caplog.text, root_key, derived.key)
    for entry in store.list_audit():
        assert_clean_of(str(entry), root_key, derived.key)
