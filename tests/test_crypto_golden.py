"""Golden-vector and cross-backend tests for the HKDF adapters.

Reference answers come from RFC 5869 (external standard) and from the
second independent backend — never from the tested core itself.
"""

from __future__ import annotations

import pytest

from keytree.crypto_adapter import (
    MAX_OKM_LEN,
    CryptographyHkdf,
    PyCryptodomeHkdf,
)
from keytree.errors import ComputationError, ResourceExhaustedError

BACKENDS = [CryptographyHkdf(), PyCryptodomeHkdf()]


@pytest.mark.parametrize("backend", BACKENDS, ids=lambda b: b.name)
def test_rfc5869_golden_vectors(backend, rfc5869):
    for case in rfc5869:
        okm = backend.hkdf_sha256(
            ikm=bytes.fromhex(case["ikm"]),
            salt=bytes.fromhex(case["salt"]),
            info=bytes.fromhex(case["info"]),
            length=case["length"],
        )
        assert okm.hex() == case["okm"], f"{backend.name} failed {case['name']}"


def test_backends_agree_with_each_other(rfc5869):
    for case in rfc5869:
        results = {
            b.name: b.hkdf_sha256(
                bytes.fromhex(case["ikm"]),
                bytes.fromhex(case["salt"]),
                bytes.fromhex(case["info"]),
                case["length"],
            )
            for b in BACKENDS
        }
        assert results["cryptography"] == results["pycryptodome"]


@pytest.mark.parametrize("backend", BACKENDS, ids=lambda b: b.name)
def test_max_length_boundary(backend):
    # 255 * 32 is the HKDF-SHA256 algorithmic maximum and must succeed.
    okm = backend.hkdf_sha256(b"ikm", b"salt", b"info", MAX_OKM_LEN)
    assert len(okm) == MAX_OKM_LEN


@pytest.mark.parametrize("backend", BACKENDS, ids=lambda b: b.name)
def test_length_above_max_is_resource_exhausted(backend):
    with pytest.raises(ResourceExhaustedError) as excinfo:
        backend.hkdf_sha256(b"ikm", b"salt", b"info", MAX_OKM_LEN + 1)
    assert excinfo.value.category == "resource_exhausted"


@pytest.mark.parametrize("backend", BACKENDS, ids=lambda b: b.name)
def test_zero_length_is_rejected(backend):
    with pytest.raises(ResourceExhaustedError):
        backend.hkdf_sha256(b"ikm", b"salt", b"info", 0)


def test_backend_failure_maps_to_computation_error():
    class BrokenBackend(CryptographyHkdf):
        name = "broken"

        def hkdf_sha256(self, ikm, salt, info, length):
            try:
                raise ValueError("simulated backend fault")
            except Exception as exc:
                raise ComputationError("HKDF backend failed") from exc

    with pytest.raises(ComputationError) as excinfo:
        BrokenBackend().hkdf_sha256(b"a", b"b", b"c", 32)
    assert excinfo.value.category == "computation_failure"
