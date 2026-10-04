"""Independent verification: the platform's accept/reject decisions are
cross-checked against reference implementations that are NOT the code
under test (PyCryptodome signature verification, the openssl CLI, and a
direct validity-window check). Expected outcomes are hand-written in this
file, not derived from the core implementation."""
from __future__ import annotations

import pytest

from trustlab.errors import FailureClass
from trustlab.verify_ref import (openssl_verify,
                                 pycryptodome_verify_leaf_signature,
                                 validity_window)

from .conftest import HANDSHAKE_ERRORS, connect_client, wait_failures

# Hand-written reference answers: what each client cert SHOULD do against
# a trust bundle containing only the OLD client CA.
EXPECTED_AGAINST_OLD_ROOT = {
    "client_a": "accept",
    "client_b": "reject:UNTRUSTED_ROOT",
    "client_expired": "reject:CERT_EXPIRED",
    "client_wrong_eku": "reject:WRONG_PURPOSE",
}


def test_pycryptodome_signature_crosscheck(fixtures):
    # Valid chains verify under PyCryptodome's own RSA implementation.
    for leaf, issuer in (
        (fixtures.client_a, fixtures.ca_old),
        (fixtures.client_b, fixtures.ca_new),
        (fixtures.server, fixtures.ca_server),
    ):
        report = pycryptodome_verify_leaf_signature(leaf.cert_der,
                                                    issuer.cert_der)
        assert report.ok, report.detail

    # A leaf checked against the WRONG issuer must fail.
    report = pycryptodome_verify_leaf_signature(fixtures.client_a.cert_der,
                                                fixtures.ca_new.cert_der)
    assert not report.ok


def test_validity_window_crosscheck(fixtures):
    assert validity_window(fixtures.client_a.cert_der).ok
    expired = validity_window(fixtures.client_expired.cert_der)
    assert not expired.ok
    assert "expired" in expired.detail


def test_openssl_cli_agrees_with_core(service, fixtures, tmp_path):
    outcomes = {}

    sess = connect_client(service, fixtures, tmp_path,
                          fixtures.client_a, "a")
    outcomes["client_a"] = "accept"
    sess.close()

    for name, issued in (
        ("client_b", fixtures.client_b),
        ("client_expired", fixtures.client_expired),
        ("client_wrong_eku", fixtures.client_wrong_eku),
    ):
        with pytest.raises(HANDSHAKE_ERRORS):
            connect_client(service, fixtures, tmp_path, issued, name)
    failures = wait_failures(service, 3)
    for failure in failures:
        cls = failure["failure_class"]
        if cls == FailureClass.UNTRUSTED_ROOT.value:
            outcomes["client_b"] = "reject:UNTRUSTED_ROOT"
        elif cls == FailureClass.CERT_EXPIRED.value:
            outcomes["client_expired"] = "reject:CERT_EXPIRED"
        elif cls == FailureClass.WRONG_PURPOSE.value:
            outcomes["client_wrong_eku"] = "reject:WRONG_PURPOSE"

    # The core's decisions match the hand-written reference table.
    assert outcomes == EXPECTED_AGAINST_OLD_ROOT

    # The openssl CLI, run as an independent process, agrees on each cert.
    checks = [
        ("client_a", fixtures.client_a, True),
        ("client_b", fixtures.client_b, False),
        ("client_expired", fixtures.client_expired, False),
        ("client_wrong_eku", fixtures.client_wrong_eku, False),
    ]
    for name, issued, expect_ok in checks:
        report = openssl_verify(issued.cert_pem, fixtures.ca_old.cert_pem,
                                purpose="sslclient")
        if report.skipped:
            pytest.skip("openssl binary not available")
        assert report.ok is expect_ok, f"{name}: {report.detail}"

    # And PyCryptodome confirms the accepted leaf's signature independently.
    report = pycryptodome_verify_leaf_signature(fixtures.client_a.cert_der,
                                                fixtures.ca_old.cert_der)
    assert report.ok
