"""Fixture integrity: re-derive every frozen RFC 7677 expectation at test time.

The committed JSON fixture is a *recorded* independent oracle. To guard
against a tampered or stale fixture, these tests recompute the whole chain
from the RFC password/salt with the stdlib oracle and require equality with
the fixture, and also confirm the fixture's p=/v= values match RFC 7677
literals. Nothing here imports production crypto.
"""
from __future__ import annotations

import base64

from ._oracle_stdlib import derive_all


def test_fixture_intermediate_hex_recomputes_via_stdlib(rfc_vector) -> None:
    oracle = derive_all(
        rfc_vector["password"],
        base64.b64decode(rfc_vector["salt_b64"]),
        rfc_vector["iteration_count"],
        rfc_vector["client_first_bare"],
        rfc_vector["server_first"],
        rfc_vector["client_final_without_proof"],
    )
    expected = rfc_vector["intermediate_hex"]
    assert oracle.salted_password.hex() == expected["SaltedPassword"]
    assert oracle.client_key.hex() == expected["ClientKey"]
    assert oracle.stored_key.hex() == expected["StoredKey"]
    assert oracle.server_key.hex() == expected["ServerKey"]
    assert oracle.client_signature.hex() == expected["ClientSignature"]
    assert oracle.client_proof.hex() == expected["ClientProof"]
    assert oracle.server_signature.hex() == expected["ServerSignature"]


def test_fixture_wire_proofs_match_rfc_literals(rfc_vector) -> None:
    # The p= and v= wire fields as published in RFC 7677 Appendix 3.
    assert rfc_vector["client_final"].endswith(",p=" + rfc_vector["client_proof_b64"])
    assert rfc_vector["server_final"] == "v=" + rfc_vector["server_signature_b64"]
    assert rfc_vector["client_proof_b64"] == "dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ="
    assert rfc_vector["server_signature_b64"] == "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="


def test_fixture_nonce_concatenation_is_stated_correctly(rfc_vector) -> None:
    assert rfc_vector["server_nonce"] == rfc_vector["client_nonce"] + rfc_vector["server_nonce_fragment"]
    assert rfc_vector["server_nonce"].startswith(rfc_vector["client_nonce"])
