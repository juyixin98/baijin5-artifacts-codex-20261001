"""Pin the protocol encoding against reference values computed independently
of the package (pure hashlib / stdlib hmac recomputation, see README).

These are the tests that guard against the implementation silently changing
its byte layout: the expected digests below are hardcoded, not produced by
the code under test.
"""

import hashlib
import hmac

from commit_reveal.crypto.commitment import combine_seed, compute_commitment
from commit_reveal.crypto.draw import draw_index

ROUND_ID = "round-vector-1"
ALICE_VALUE = "00" * 32
ALICE_SALT = "ff" * 16
BOB_VALUE = "11" * 32

# Computed with an independent hashlib-only snippet (recorded in README).
EXPECTED_COMMITMENT = (
    "3875e2fb26cf440fba09ac9a7f60b460f1f94c7eff2cb66108720a935df3950d"
)
EXPECTED_SEED = (
    "cb432498ff4363ae67a484af7491b1c626e433c1488c84f7bb791929b4334764"
)
EXPECTED_DRAW_INDEX_N2 = 1
EXPECTED_DRAW_INDEX_N3 = 0


def test_commitment_matches_independent_vector():
    got = compute_commitment(
        ROUND_ID, "alice", bytes.fromhex(ALICE_VALUE), bytes.fromhex(ALICE_SALT)
    )
    assert got == EXPECTED_COMMITMENT


def test_seed_matches_independent_vector():
    got = combine_seed(
        ROUND_ID,
        [("bob", bytes.fromhex(BOB_VALUE)),  # passed out of order on purpose
         ("alice", bytes.fromhex(ALICE_VALUE))],
    )
    assert got == EXPECTED_SEED


def test_seed_is_order_independent():
    a = combine_seed(ROUND_ID, [("alice", bytes.fromhex(ALICE_VALUE)),
                                ("bob", bytes.fromhex(BOB_VALUE))])
    b = combine_seed(ROUND_ID, [("bob", bytes.fromhex(BOB_VALUE)),
                                ("alice", bytes.fromhex(ALICE_VALUE))])
    assert a == b


def test_draw_matches_independent_vector():
    assert draw_index(EXPECTED_SEED, 2) == EXPECTED_DRAW_INDEX_N2
    assert draw_index(EXPECTED_SEED, 3) == EXPECTED_DRAW_INDEX_N3


def test_reference_recomputation_agrees():
    """Belt-and-braces: redo the reference computation inline (stdlib only)."""

    def enc(fields):
        out = b""
        for f in fields:
            out += len(f).to_bytes(4, "big") + f
        return out

    ref_commitment = hashlib.sha256(
        b"CRP1-COMMIT-v1"
        + enc([ROUND_ID.encode(), b"alice",
               bytes.fromhex(ALICE_VALUE), bytes.fromhex(ALICE_SALT)])
    ).hexdigest()
    assert ref_commitment == EXPECTED_COMMITMENT

    ref_seed = hashlib.sha256(
        b"CRP1-SEED-v1"
        + enc([ROUND_ID.encode(), b"alice", bytes.fromhex(ALICE_VALUE),
               b"bob", bytes.fromhex(BOB_VALUE)])
    ).hexdigest()
    assert ref_seed == EXPECTED_SEED

    block = hmac.new(bytes.fromhex(EXPECTED_SEED),
                     b"CRP1-DRAW-v1" + (0).to_bytes(4, "big"),
                     hashlib.sha256).digest()
    assert int.from_bytes(block[:16], "big") % 2 == EXPECTED_DRAW_INDEX_N2
