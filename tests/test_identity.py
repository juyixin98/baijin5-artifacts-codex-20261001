"""Client identity must come from the verified certificate fields of the
established TLS session - never from application-layer claims."""
from __future__ import annotations

from cryptography.hazmat.primitives import hashes

from trustlab import frames

from .conftest import connect_client


def test_identity_matches_verified_certificate(service, fixtures, tmp_path):
    sess = connect_client(service, fixtures, tmp_path, fixtures.client_a, "a")
    raw_fp = fixtures.client_a.cert.fingerprint(hashes.SHA256()).hex()
    expected_fp = ":".join(raw_fp[i:i + 2] for i in range(0, 64, 2))
    identity = sess.identity
    assert identity["common_name"] == "client-a"
    assert identity["issuer_common_name"] == "client-ca-old"
    assert identity["sha256_fingerprint"] == expected_fp
    assert identity["serial_hex"] == format(
        fixtures.client_a.cert.serial_number, "x"
    )

    # whoami re-reads the identity from the TLS session, not from frames.
    who = sess.whoami()
    assert who["identity"] == identity
    sess.close()


def test_client_cannot_override_identity_via_frames(service, fixtures,
                                                    tmp_path):
    sess = connect_client(service, fixtures, tmp_path, fixtures.client_a, "a")
    # A malicious frame claiming a different identity is just an unknown
    # frame type / ignored payload; whoami still reports the cert identity.
    frames.write_frame(sess.sock, {
        "type": "whoami",
        "claimed_identity": {"common_name": "admin"},
    })
    response = sess.read_frame()
    assert response["type"] == "identity"
    assert response["identity"]["common_name"] == "client-a"
    sess.close()
