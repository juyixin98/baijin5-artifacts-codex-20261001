"""Handshake failure categories: expiry, wrong key usage, missing client
certificate, and the explainable no-common-trust-period case."""
from __future__ import annotations

import pytest

from trustlab.errors import FailureClass
from trustlab.explainer import explain_untrusted_root

from .conftest import HANDSHAKE_ERRORS, connect_client, wait_failures


def test_expired_certificate(service, fixtures, tmp_path):
    with pytest.raises(HANDSHAKE_ERRORS):
        connect_client(service, fixtures, tmp_path,
                       fixtures.client_expired, "expired")
    failures = wait_failures(service, 1)
    failure = failures[-1]
    assert failure["failure_class"] == FailureClass.CERT_EXPIRED.value
    assert failure["layer"] == "tls"
    assert failure["verify_code"] == 10  # X509_V_ERR_CERT_HAS_EXPIRED
    assert failure["run_id"] == service.run_id


def test_wrong_eku_certificate(service, fixtures, tmp_path):
    # Certificate carries only serverAuth EKU; must not work as a client.
    with pytest.raises(HANDSHAKE_ERRORS):
        connect_client(service, fixtures, tmp_path,
                       fixtures.client_wrong_eku, "wrongeku")
    failures = wait_failures(service, 1)
    failure = failures[-1]
    assert failure["failure_class"] == FailureClass.WRONG_PURPOSE.value
    # Caught either by OpenSSL's purpose check (tls) or by the
    # application-layer EKU re-check; both are recorded explicitly.
    assert failure["layer"] in ("tls", "application")
    if failure["layer"] == "tls":
        assert failure["verify_code"] == 26  # X509_V_ERR_INVALID_PURPOSE


def test_missing_client_certificate(service, fixtures, tmp_path):
    with pytest.raises(HANDSHAKE_ERRORS):
        connect_client(service, fixtures, tmp_path, None)
    failures = wait_failures(service, 1)
    assert failures[-1]["failure_class"] == FailureClass.NO_CLIENT_CERT.value


def test_hard_cutover_has_no_common_trust_period(service, fixtures,
                                                 tmp_path):
    # Hard cutover: v2 trusts ONLY the new root, no overlap version.
    service.bundles.create([fixtures.ca_new.cert_pem], kind="manual",
                           note="hard cutover without overlap")

    with pytest.raises(HANDSHAKE_ERRORS):
        connect_client(service, fixtures, tmp_path, fixtures.client_a, "a")
    failures = wait_failures(service, 1)
    failure = failures[-1]
    assert failure["failure_class"] == FailureClass.UNTRUSTED_ROOT.value

    # The failure record carries an explanation derived from bundle history.
    explanation = failure["explanation"]
    assert explanation is not None
    assert explanation["active_bundle_version"] == 2
    assert len(explanation["retired_roots"]) == 1

    # A definitive per-client explanation via the diagnostic path.
    specific = explain_untrusted_root(
        service.store,
        presented_chain_pem=[fixtures.client_a.cert_pem,
                             fixtures.ca_old.cert_pem],
    )
    assert specific["common_trust_period"] is False
    assert specific["presented_root"] in specific["retired_roots"]
    assert any("no common trust period" in line.lower()
               or "NO common trust period" in line
               for line in specific["reasoning"])


def test_overlap_rotation_explains_ended_common_trust(service, fixtures,
                                                      tmp_path):
    # Proper rotation WITH overlap, then old root retired.
    service.bundles.begin_rotation([fixtures.ca_new.cert_pem])
    service.bundles.end_rotation([fixtures.ca_new.cert_pem])

    with pytest.raises(HANDSHAKE_ERRORS):
        connect_client(service, fixtures, tmp_path, fixtures.client_a, "a")
    wait_failures(service, 1)

    specific = explain_untrusted_root(
        service.store,
        presented_chain_pem=[fixtures.client_a.cert_pem,
                             fixtures.ca_old.cert_pem],
    )
    # Overlap existed (v2) but has ended - a different explanation than
    # the hard-cutover case, and distinguishable in the record.
    assert specific["common_trust_period"] is True
    assert any("common trust period existed" in line
               for line in specific["reasoning"])
