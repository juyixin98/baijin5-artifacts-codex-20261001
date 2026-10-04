"""Overlapping trust-root rotation: old and new roots coexist, then the
old root is retired. Handshake outcomes AND application-level identity
are asserted at every phase."""
from __future__ import annotations

import pytest

from trustlab.errors import FailureClass

from .conftest import HANDSHAKE_ERRORS, connect_client, wait_failures


def test_overlapping_rotation(service, fixtures, tmp_path):
    # Phase 1: v1 trusts only the old client CA.
    sess_a = connect_client(service, fixtures, tmp_path,
                            fixtures.client_a, "a1")
    assert sess_a.bundle_version == 1
    assert sess_a.identity["common_name"] == "client-a"
    assert sess_a.identity["issuer_common_name"] == "client-ca-old"

    # client-b (new CA) is rejected while v1 is active.
    with pytest.raises(HANDSHAKE_ERRORS):
        connect_client(service, fixtures, tmp_path, fixtures.client_b, "b1")
    failures = wait_failures(service, 1)
    assert failures[-1]["failure_class"] == FailureClass.UNTRUSTED_ROOT.value

    # Phase 2: overlap - v2 trusts old AND new roots.
    overlap = service.bundles.begin_rotation([fixtures.ca_new.cert_pem])
    assert overlap.version == 2
    assert overlap.kind == "rotation_overlap"
    assert len(overlap.roots_pem) == 2

    sess_b = connect_client(service, fixtures, tmp_path,
                            fixtures.client_b, "b2")
    assert sess_b.bundle_version == 2
    assert sess_b.identity["common_name"] == "client-b"

    # Old-root clients are still accepted during the overlap.
    sess_a2 = connect_client(service, fixtures, tmp_path,
                             fixtures.client_a, "a2")
    assert sess_a2.bundle_version == 2

    # Phase 3: rotation finalized - v3 trusts only the new root.
    final = service.bundles.end_rotation([fixtures.ca_new.cert_pem])
    assert final.version == 3
    assert final.kind == "rotation_final"
    assert len(final.roots_pem) == 1

    with pytest.raises(HANDSHAKE_ERRORS):
        connect_client(service, fixtures, tmp_path, fixtures.client_a, "a3")
    failures = wait_failures(service, 2)
    assert failures[-1]["failure_class"] == FailureClass.UNTRUSTED_ROOT.value

    # New-root clients still authenticate, and the application identity
    # comes from the verified certificate.
    sess_b2 = connect_client(service, fixtures, tmp_path,
                             fixtures.client_b, "b3")
    assert sess_b2.bundle_version == 3
    who = sess_b2.whoami()
    assert who["type"] == "identity"
    assert who["identity"]["common_name"] == "client-b"
    assert who["identity"]["issuer_common_name"] == "client-ca-new"

    for sess in (sess_a, sess_b, sess_a2, sess_b2):
        sess.close()
