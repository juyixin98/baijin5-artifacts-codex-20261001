"""Round-trip tests: different chunkings and out-of-order submission
must all release byte-identical plaintext."""

from __future__ import annotations

import random

import pytest


def _submit(service, message_id: str, chunks: list[bytes], order=None):
    order = order if order is not None else list(range(len(chunks)))
    for seq in order:
        service.submit_chunk(
            request_id=f"req-{message_id}-{seq}",
            message_id=message_id,
            seq=seq,
            final=(seq == len(chunks) - 1),
            plaintext=chunks[seq],
        )


def _roundtrip(service, plaintext: bytes, chunk_size: int, order=None) -> bytes:
    chunks = [plaintext[i:i + chunk_size] for i in range(0, len(plaintext), chunk_size)]
    if not chunks:
        chunks = [b""]  # empty message = single empty final chunk
    mid = service.create_message(request_id="req-create")["message_id"]
    _submit(service, mid, chunks, order)
    result = service.finalize(request_id="req-fin", message_id=mid)
    assert result["size"] == len(plaintext)
    assert result["chunks"] == len(chunks)
    return service.get_plaintext(request_id="req-get", message_id=mid)


@pytest.mark.parametrize("chunk_size", [1, 3, 7, 1024, 65536])
def test_chunking_does_not_change_plaintext(service, chunk_size):
    plaintext = bytes(random.Random(42).randbytes(10_000))
    assert _roundtrip(service, plaintext, chunk_size) == plaintext


def test_single_chunk_message(service):
    plaintext = b"\x00\x01\x02" * 1000
    assert _roundtrip(service, plaintext, len(plaintext)) == plaintext


def test_empty_message(service):
    assert _roundtrip(service, b"", 1) == b""


def test_out_of_order_submission(service):
    plaintext = bytes(random.Random(7).randbytes(10_000))
    chunk_size = 100
    n = (len(plaintext) + chunk_size - 1) // chunk_size
    order = list(range(n))
    random.Random(13).shuffle(order)
    assert order != sorted(order)  # prove the test really shuffles
    assert _roundtrip(service, plaintext, chunk_size, order) == plaintext


def test_same_plaintext_different_chunkings_both_release(service):
    plaintext = b"deterministic content " * 500
    assert _roundtrip(service, plaintext, 64) == plaintext
    assert _roundtrip(service, plaintext, 4096) == plaintext


def test_different_messages_get_different_ciphertext(service):
    pt = b"identical content"
    cts = []
    for _ in range(2):
        mid = service.create_message(request_id="req-c")["message_id"]
        r = service.submit_chunk(
            request_id="req-s", message_id=mid, seq=0, final=True, plaintext=pt
        )
        cts.append(r["ciphertext"])
    assert cts[0] != cts[1], "per-message key/nonce-base must randomize ciphertext"
