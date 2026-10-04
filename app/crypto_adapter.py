"""Crypto adapter: the only module that talks to the `phe` Paillier library.

Everything above this layer works on plain Python ints (encoded residues
and ciphertext integers), so the cryptographic dependency is isolated and
replaceable.  Only genuinely-Paillier operations are exposed:

    encrypt / decrypt            (raw residues in Z_n)
    ciphertext addition          E(a) * E(b)  = E(a + b)
    plaintext scalar multiply    E(a) ** k    = E(k * a)   (k may be negative)

No ciphertext-ciphertext multiplication, no comparison, no branching on
encrypted data — the scheme does not support them and neither do we.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from phe import EncryptedNumber, paillier

from .errors import ErrorCategory, PaillierServiceError

DEFAULT_KEY_SIZE = 2048
MIN_KEY_SIZE = 1024


@dataclass
class PaillierKeypair:
    public_key: paillier.PaillierPublicKey
    private_key: paillier.PaillierPrivateKey


def generate_keypair(n_length: int = DEFAULT_KEY_SIZE) -> PaillierKeypair:
    if n_length < MIN_KEY_SIZE:
        raise PaillierServiceError(
            ErrorCategory.ENCODING_PARAM_INVALID,
            f"key size {n_length} below minimum {MIN_KEY_SIZE}",
            {"n_length": n_length},
        )
    pub, priv = paillier.generate_paillier_keypair(n_length=n_length)
    return PaillierKeypair(public_key=pub, private_key=priv)


def key_fingerprint(public_key: paillier.PaillierPublicKey) -> str:
    """Stable key identifier used to bind ciphertexts to a batch's key."""
    digest = hashlib.sha256(str(public_key.n).encode("ascii")).hexdigest()
    return f"sha256:{digest}"


def reconstruct_public_key(n: int) -> paillier.PaillierPublicKey:
    return paillier.PaillierPublicKey(n)


def reconstruct_private_key(
    public_key: paillier.PaillierPublicKey, p: int, q: int
) -> paillier.PaillierPrivateKey:
    return paillier.PaillierPrivateKey(public_key, p, q)


def validate_ciphertext(public_key: paillier.PaillierPublicKey, ciphertext: int) -> None:
    """A Paillier ciphertext must be a residue mod n^2."""
    if isinstance(ciphertext, bool) or not isinstance(ciphertext, int):
        raise PaillierServiceError(
            ErrorCategory.CIPHERTEXT_INVALID,
            "ciphertext must be an integer",
            {"ciphertext": repr(ciphertext)},
        )
    nsquare = public_key.nsquare
    if not 0 <= ciphertext < nsquare:
        raise PaillierServiceError(
            ErrorCategory.CIPHERTEXT_INVALID,
            "ciphertext is not a residue mod n^2 for this public key",
            {"ciphertext_bits": ciphertext.bit_length() if ciphertext >= 0 else -1},
        )


def encrypt_encoded(public_key: paillier.PaillierPublicKey, encoded: int) -> int:
    """Encrypt an already-encoded residue (0 <= encoded < n)."""
    if not 0 <= encoded < public_key.n:
        raise PaillierServiceError(
            ErrorCategory.PLAINTEXT_OUT_OF_RANGE,
            "encoded plaintext is not a residue mod n",
            {"encoded_bits": encoded.bit_length() if encoded >= 0 else -1},
        )
    return public_key.raw_encrypt(encoded)


def decrypt_to_encoded(
    private_key: paillier.PaillierPrivateKey, ciphertext: int
) -> int:
    """Decrypt to the raw encoded residue; signed decoding happens upstream."""
    return private_key.raw_decrypt(ciphertext)


def add_ciphertexts(
    public_key: paillier.PaillierPublicKey, ciphertext_a: int, ciphertext_b: int
) -> int:
    """E(a) + E(b) = E(a + b).  Deterministic (no re-randomisation)."""
    enc_a = EncryptedNumber(public_key, ciphertext_a, 0)
    enc_b = EncryptedNumber(public_key, ciphertext_b, 0)
    return (enc_a + enc_b).ciphertext(be_secure=False)


def multiply_by_scalar(
    public_key: paillier.PaillierPublicKey, ciphertext: int, scalar: int
) -> int:
    """E(a) * k = E(k * a) via the canonical group operation c^(k mod n).

    This is textbook Paillier scalar multiplication and matches phe's
    EncryptedNumber.__mul__ exactly for 0 <= k < n - max_int; for negative
    scalars phe returns a different (equivalent) representative via a
    modular-inverse trick, so we use the canonical form directly to keep
    aggregation deterministic and independently reproducible.  Negative
    scalars act mod n, which the encoding layer reads as signed
    multiplication.
    """
    if isinstance(scalar, bool) or not isinstance(scalar, int):
        raise PaillierServiceError(
            ErrorCategory.COEFFICIENT_OUT_OF_RANGE,
            "scalar must be an integer",
            {"scalar": repr(scalar)},
        )
    return pow(ciphertext, scalar % public_key.n, public_key.nsquare)
