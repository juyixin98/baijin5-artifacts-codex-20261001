"""Independent reference helpers for tests.

These functions intentionally do NOT import the service's encoding or
aggregation code.  Expected values come from plain integer arithmetic and
textbook Paillier math written out here, so the tested core is checked
against something other than itself.
"""

from __future__ import annotations

import math


def reference_weighted_sum(values: list[int], weights: list[int]) -> int:
    """Plain-integer reference: sum(w_i * v_i)."""
    assert len(values) == len(weights)
    total = 0
    for v, w in zip(values, weights):
        total += w * v
    return total


def reference_encode(value: int, n: int) -> int:
    """Signed encoding reference: value mod n."""
    return value % n


def reference_decode(raw: int, n: int, bound: int) -> int | None:
    """Signed decoding reference; None marks the ambiguous zone."""
    if raw <= bound:
        return raw
    if raw >= n - bound:
        return raw - n
    return None


def reference_aggregate_ciphertext(
    ciphertexts: list[int], weights: list[int], n: int
) -> int:
    """Textbook homomorphic weighted sum: prod(c_i^(w_i mod n)) mod n^2."""
    nsquare = n * n
    acc = 1
    for c, w in zip(ciphertexts, weights):
        acc = (acc * pow(c, w % n, nsquare)) % nsquare
    return acc


def reference_decrypt(ciphertext: int, n: int, p: int, q: int) -> int:
    """Textbook Paillier decryption with g = n + 1."""
    nsquare = n * n
    lam = math.lcm(p - 1, q - 1)
    mu = pow(lam, -1, n)
    return ((pow(ciphertext, lam, nsquare) - 1) // n) * mu % n
