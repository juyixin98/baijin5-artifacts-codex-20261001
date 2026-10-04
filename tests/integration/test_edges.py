"""Edge-case unit tests: config, limits, stream lifecycle branches."""

from __future__ import annotations

import pytest

from app.config import Settings
from app.core.errors import (
    IncompleteStreamError, LimitError, ProtocolCodingError, StateError,
    StorageError,
)
from app.core.crypto import CryptographyBackend
from app.core.sender import encode_message


@pytest.mark.unit
def test_settings_defaults_and_overrides():
    s = Settings.from_env({})
    assert s.backend == "cryptography"
    assert s.max_segment_bytes > 0
    s2 = Settings.from_env({
        "SSEA_MAX_SEGMENT_BYTES": "10", "SSEA_PORT": "9999",
        "SSEA_AEAD_BACKEND": "pycryptodome", "SSEA_KEY_FILE": "/tmp/k",
    })
    assert s2.max_segment_bytes == 10
    assert s2.port == 9999
    assert s2.backend == "pycryptodome"
    assert s2.key_file == "/tmp/k"


@pytest.mark.unit
@pytest.mark.parametrize("bad", [
    {"SSEA_MAX_SEGMENT_BYTES": "abc"},
    {"SSEA_MAX_SEGMENT_BYTES": "-1"},
    {"SSEA_PORT": "0"},
])
def test_settings_rejects_invalid_ints(bad):
    with pytest.raises(LimitError):
        Settings.from_env(bad)


@pytest.mark.integration
def test_segment_size_limit_enforced(make_service, fixture_keys):
    svc = make_service(max_segment=4)
    sealed = encode_message(fixture_keys, CryptographyBackend(),
                            "m-lim", b"abcdefgh", 8)  # one 8B segment
    with pytest.raises(LimitError) as ei:
        svc.submit(sealed.frames[0])
    assert ei.value.category == "limit"


@pytest.mark.integration
def test_total_size_limit_enforced(make_service, fixture_keys):
    svc = make_service(max_total=4)
    sealed = encode_message(fixture_keys, CryptographyBackend(),
                            "m-tlim", b"abcde", 2)
    with pytest.raises(LimitError):
        svc.submit(sealed.frames[0])


@pytest.mark.integration
def test_begin_stream_duplicate_and_unknown(make_service):
    svc = make_service()
    svc.begin_stream("m-b", total_segments=1, total_len=0)
    with pytest.raises(StateError):
        svc.begin_stream("m-b", total_segments=1, total_len=0)
    with pytest.raises(StateError):
        svc.status("nope")


@pytest.mark.integration
def test_declared_total_len_must_match_stream(make_service, fixture_keys):
    svc = make_service()
    sealed = encode_message(fixture_keys, CryptographyBackend(),
                            "m-decl", b"abcd", 2)   # 2 segs, total_len 4
    svc.submit(sealed.frames[0])

    # 2-segment message but a different declared total length (5 bytes split
    # "abc","de"): same count, lying length -> rejected, no fake acceptance.
    other = encode_message(fixture_keys, CryptographyBackend(),
                           "m-decl", b"abcde", 3)  # "abc","de" -> 2 segs, len 5
    assert other.total_segments == 2
    with pytest.raises(ProtocolCodingError) as ei:
        svc.submit(other.frames[1])
    assert ei.value.category == "protocoding"


@pytest.mark.integration
def test_abort_seals_stream_and_shreds(make_service, fixture_keys):
    svc = make_service()
    sealed = encode_message(fixture_keys, CryptographyBackend(),
                            "m-abort", b"abcd", 2)
    svc.submit(sealed.frames[0])
    svc.abort("m-abort")
    assert svc.status("m-abort")["status"] == "failed"
    with pytest.raises(StateError):
        svc.submit(sealed.frames[1])


@pytest.mark.integration
def test_submit_after_release_is_state_error(make_service, fixture_keys):
    svc = make_service()
    sealed = encode_message(fixture_keys, CryptographyBackend(),
                            "m-rel", b"abcd", 2)
    for f in sealed.frames:
        svc.submit(f)
    svc.finalize("m-rel")
    assert svc.get_result("m-rel") == b"abcd"
    with pytest.raises(StateError):
        svc.submit(sealed.frames[0])
    with pytest.raises(StateError):
        svc.abort("m-rel")


@pytest.mark.integration
def test_finalize_with_outstanding_segments_is_incomplete(
        make_service, fixture_keys):
    svc = make_service()
    svc.begin_stream("m-gap", total_segments=2, total_len=4)
    sealed = encode_message(fixture_keys, CryptographyBackend(),
                            "m-gap", b"abcd", 2)
    svc.submit(sealed.frames[0])
    with pytest.raises(IncompleteStreamError) as ei:
        svc.finalize("m-gap")  # undecidable, not a false success
    assert ei.value.category == "incomplete"
    assert svc.status("m-gap")["released"] is False


@pytest.mark.integration
def test_release_storage_failure_does_not_release(make_service, fixture_keys,
                                                  monkeypatch):
    svc = make_service()
    sealed = encode_message(fixture_keys, CryptographyBackend(),
                            "m-sf", b"abcd", 2)
    for f in sealed.frames:
        svc.submit(f)

    def boom(*_a, **_k):
        raise StorageError("simulated assembly failure")

    monkeypatch.setattr(svc._staging, "release", boom)
    with pytest.raises(StorageError):
        svc.finalize("m-sf")
    # Stream is failed; no fake complete message.
    assert svc.status("m-sf")["status"] == "failed"
