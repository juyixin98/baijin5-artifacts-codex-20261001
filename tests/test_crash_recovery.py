"""Crash-recovery tests: a process dying mid-finalize must not leak
plaintext and must leave the stream finalizable after restart."""

from __future__ import annotations

import pytest

from sae.service import ChunkedCryptoService

PLAINTEXT = b"crash-recovery payload " * 100


def _stage_full_stream(service, mid):
    chunks = [PLAINTEXT[:500], PLAINTEXT[500:1500], PLAINTEXT[1500:]]
    for seq, pt in enumerate(chunks):
        service.submit_chunk(
            request_id=f"req-{seq}", message_id=mid, seq=seq,
            final=(seq == len(chunks) - 1), plaintext=pt,
        )


def test_crash_before_release_commit(settings):
    svc = ChunkedCryptoService(settings)
    mid = svc.create_message(request_id="req-c")["message_id"]
    _stage_full_stream(svc, mid)

    # Simulate a power cut at the worst moment: all chunks authenticated,
    # release transaction not yet committed.
    def die(_message_id):
        raise RuntimeError("simulated power cut")

    svc._before_release_commit = die
    with pytest.raises(RuntimeError):
        svc.finalize(request_id="req-fin-1", message_id=mid)

    # Nothing was released; the message is still open.
    assert svc.get_status(mid)["status"] == "open"
    assert svc._store.get_released(mid) is None
    svc.close()

    # "Restart the process": a brand-new service object on the same DB.
    svc2 = ChunkedCryptoService(settings)
    try:
        result = svc2.finalize(request_id="req-fin-2", message_id=mid)
        assert result["size"] == len(PLAINTEXT)
        assert svc2.get_plaintext(request_id="req-g", message_id=mid) == PLAINTEXT
        assert svc2.get_status(mid)["status"] == "released"
    finally:
        svc2.close()


def test_crash_between_chunk_submissions(settings):
    svc = ChunkedCryptoService(settings)
    mid = svc.create_message(request_id="req-c")["message_id"]
    svc.submit_chunk(request_id="r0", message_id=mid, seq=0, final=False,
                     plaintext=PLAINTEXT[:500])
    svc.close()  # abrupt stop with a partial stream staged

    svc2 = ChunkedCryptoService(settings)
    try:
        status = svc2.get_status(mid)
        assert status["status"] == "open"
        assert status["received_seqs"] == [0]
        # Continue where the crashed process left off.
        svc2.submit_chunk(request_id="r1", message_id=mid, seq=1, final=False,
                          plaintext=PLAINTEXT[500:1500])
        svc2.submit_chunk(request_id="r2", message_id=mid, seq=2, final=True,
                          plaintext=PLAINTEXT[1500:])
        svc2.finalize(request_id="req-fin", message_id=mid)
        assert svc2.get_plaintext(request_id="req-g", message_id=mid) == PLAINTEXT
    finally:
        svc2.close()
