"""Identity tests: key_id is stable, display-name-independent, validated."""

from __future__ import annotations

import pytest

from keytree.errors import InputValidationError
from keytree.identity import KeyIdentity


def test_key_id_is_deterministic():
    a = KeyIdentity(tenant="acme", purpose="encryption", version=1, context=b"")
    b = KeyIdentity(tenant="acme", purpose="encryption", version=1, context=b"")
    assert a.key_id == b.key_id
    assert a.key_id.startswith("kdt1_")


def test_identity_fields_all_affect_key_id():
    base = KeyIdentity(tenant="acme", purpose="encryption", version=1, context=b"")
    variants = [
        KeyIdentity(tenant="globex", purpose="encryption", version=1, context=b""),
        KeyIdentity(tenant="acme", purpose="signing", version=1, context=b""),
        KeyIdentity(tenant="acme", purpose="encryption", version=2, context=b""),
        KeyIdentity(tenant="acme", purpose="encryption", version=1, context=b"x"),
    ]
    ids = {base.key_id} | {v.key_id for v in variants}
    assert len(ids) == 5


def test_display_name_is_not_part_of_identity():
    """Two identities differing only in how a caller would label them are
    the same identity; the model has no display-name field at all."""
    a = KeyIdentity(tenant="acme", purpose="encryption", version=1, context=b"")
    assert not hasattr(a, "display_name")
    # key_id depends only on the identity tuple:
    assert a.key_id == KeyIdentity("acme", "encryption", 1, b"").key_id


def test_similar_labels_do_not_collide():
    """('ab','c') vs ('a','bc') style confusion across tenant/purpose."""
    a = KeyIdentity(tenant="ab", purpose="c", version=0, context=b"")
    b = KeyIdentity(tenant="a", purpose="bc", version=0, context=b"")
    assert a.descriptor() != b.descriptor()
    assert a.key_id != b.key_id


@pytest.mark.parametrize("tenant", ["", "ACME", "a b", "a/b", "-a", "x" * 65])
def test_invalid_tenant_rejected(tenant):
    with pytest.raises(InputValidationError) as excinfo:
        KeyIdentity(tenant=tenant, purpose="p", version=0, context=b"")
    assert excinfo.value.category == "input_error"


@pytest.mark.parametrize("version", [-1, 2**32, 1.5, "1", True])
def test_invalid_version_rejected(version):
    with pytest.raises(InputValidationError):
        KeyIdentity(tenant="acme", purpose="p", version=version, context=b"")


def test_oversize_context_rejected():
    with pytest.raises(InputValidationError):
        KeyIdentity(tenant="acme", purpose="p", version=0, context=b"x" * 257)


def test_non_bytes_context_rejected():
    with pytest.raises(InputValidationError):
        KeyIdentity(tenant="acme", purpose="p", version=0, context="text")
