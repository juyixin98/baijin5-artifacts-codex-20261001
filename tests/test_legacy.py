"""Legacy connection retention: removing a trust root must NOT silently
revoke connections that authenticated earlier; explicit revocation is a
separate administrative act."""
from __future__ import annotations

import pytest

from trustlab import frames
from trustlab.errors import FailureClass

from .conftest import HANDSHAKE_ERRORS, connect_client, wait_audit, wait_failures


def test_legacy_connection_survives_root_removal(service, fixtures, tmp_path):
    # client-a connects under v1 (old root).
    sess = connect_client(service, fixtures, tmp_path, fixtures.client_a, "a")
    conn_id = sess.connection_id
    assert sess.bundle_version == 1

    # Rotate all the way to new-root-only (v2 overlap, v3 final).
    service.bundles.begin_rotation([fixtures.ca_new.cert_pem])
    service.bundles.end_rotation([fixtures.ca_new.cert_pem])

    # The established connection still works at the application layer.
    pong = sess.ping()
    assert pong["type"] == "pong"
    who = sess.whoami()
    assert who["identity"]["common_name"] == "client-a"
    assert who["bundle_version"] == 1  # authenticated under v1 snapshot

    # The connection registry marks it legacy, not revoked.
    rows = service.store.list_connections(run_id=service.run_id)
    row = next(r for r in rows if r["id"] == conn_id)
    assert row["closed_at"] is None
    assert row["revoked"] is False
    assert row["bundle_version"] == 1

    # The audit trail states explicitly that root removal did not revoke it.
    swap = wait_audit(service, category="STATE_CHANGE",
                      event="trust_context_swapped")
    assert "does not retroactively revoke" in swap["reasoning"]

    # A NEW connection from the same client certificate now fails.
    with pytest.raises(HANDSHAKE_ERRORS):
        connect_client(service, fixtures, tmp_path, fixtures.client_a, "a2")
    failures = wait_failures(service, 1)
    assert failures[-1]["failure_class"] == FailureClass.UNTRUSTED_ROOT.value

    # Explicit administrative revocation is a separate, recorded act.
    service.data_plane.revoke_connection(conn_id)
    row = service.store.get_connection(conn_id)
    assert row["revoked"] is True
    assert row["close_reason"] == "revoked"
    with pytest.raises(HANDSHAKE_ERRORS + (frames.ConnectionClosed,)):
        sess.ping()


def test_revocation_requires_known_open_connection(service, fixtures,
                                                   tmp_path):
    from trustlab.errors import InputError, StateConflict

    with pytest.raises(InputError):
        service.data_plane.revoke_connection("does-not-exist")

    sess = connect_client(service, fixtures, tmp_path, fixtures.client_a, "a")
    service.data_plane.revoke_connection(sess.connection_id)
    with pytest.raises(StateConflict):
        service.data_plane.revoke_connection(sess.connection_id)
