"""Edge cases: malformed inputs, empty draw sets, invalid round configs."""

import pytest

from commit_reveal.crypto.commitment import combine_seed
from commit_reveal.crypto.draw import draw_index
from commit_reveal.errors import ErrorCode, ProtocolError
from commit_reveal.protocol.encoding import encode_fields

from tests.conftest import (
    COMMIT_DEADLINE,
    ROUND_ID,
    commitment_for,
    commit_all,
    make_round,
)


def test_encode_fields_rejects_non_bytes():
    with pytest.raises(TypeError):
        encode_fields("not-bytes")  # type: ignore[arg-type]


def test_combine_seed_rejects_empty():
    with pytest.raises(ValueError):
        combine_seed(ROUND_ID, [])


def test_draw_index_rejects_empty_candidate_set():
    with pytest.raises(ValueError):
        draw_index("ab" * 32, 0)


def test_malformed_commitment_rejected(service, clock):
    make_round(service)
    with pytest.raises(ProtocolError) as excinfo:
        service.commit("req-c1", ROUND_ID, "alice", "not-hex")
    assert excinfo.value.code == ErrorCode.MALFORMED_COMMITMENT

    with pytest.raises(ProtocolError) as excinfo:
        service.commit("req-c2", ROUND_ID, "alice", "ab" * 16)  # too short
    assert excinfo.value.code == ErrorCode.MALFORMED_COMMITMENT


def test_malformed_reveal_rejected(service, clock):
    make_round(service)
    commit_all(service, clock)
    with pytest.raises(ProtocolError) as excinfo:
        service.reveal("req-r1", ROUND_ID, "alice", "zz" * 32, "a1" * 16)
    assert excinfo.value.code == ErrorCode.MALFORMED_REVEAL

    with pytest.raises(ProtocolError) as excinfo:
        service.reveal("req-r2", ROUND_ID, "alice", "aa" * 8, "a1" * 16)
    assert excinfo.value.code == ErrorCode.MALFORMED_REVEAL


def test_invalid_round_configs_rejected(service, clock):
    with pytest.raises(ProtocolError) as excinfo:
        service.create_round("req", "", ["alice", "bob"],
                             COMMIT_DEADLINE, COMMIT_DEADLINE + 100)
    assert excinfo.value.code == ErrorCode.INVALID_ROUND_CONFIG

    with pytest.raises(ProtocolError) as excinfo:
        service.create_round("req", "r2", ["alice", "alice"],
                             COMMIT_DEADLINE, COMMIT_DEADLINE + 100)
    assert excinfo.value.code == ErrorCode.INVALID_ROUND_CONFIG

    with pytest.raises(ProtocolError) as excinfo:
        service.create_round("req", "r3", ["alice", "bob"],
                             COMMIT_DEADLINE + 100, COMMIT_DEADLINE)
    assert excinfo.value.code == ErrorCode.INVALID_ROUND_CONFIG

    with pytest.raises(ProtocolError) as excinfo:
        service.create_round("req", "r4", ["alice"],  # too few participants
                             COMMIT_DEADLINE, COMMIT_DEADLINE + 100)
    assert excinfo.value.code == ErrorCode.INVALID_ROUND_CONFIG


def test_duplicate_round_id_rejected(service, clock):
    make_round(service)
    with pytest.raises(ProtocolError) as excinfo:
        make_round(service)
    assert excinfo.value.code == ErrorCode.INVALID_ROUND_CONFIG


def test_commit_to_finalized_round_rejected(service, clock):
    make_round(service)
    commit_all(service, clock)
    from tests.conftest import PARTICIPANTS, reveal
    for pid in PARTICIPANTS:
        reveal(service, ROUND_ID, pid)
    service.finalize("req-f", ROUND_ID)

    with pytest.raises(ProtocolError) as excinfo:
        service.commit("req-c-late", ROUND_ID, "alice",
                       commitment_for(ROUND_ID, "alice"))
    assert excinfo.value.code == ErrorCode.ROUND_NOT_OPEN

    with pytest.raises(ProtocolError) as excinfo:
        reveal(service, ROUND_ID, "alice")
    assert excinfo.value.code == ErrorCode.ROUND_FINALIZED

    with pytest.raises(ProtocolError) as excinfo:
        service.finalize("req-f2", ROUND_ID)
    assert excinfo.value.code == ErrorCode.ROUND_FINALIZED


def test_unknown_round_rejected(service, clock):
    with pytest.raises(ProtocolError) as excinfo:
        service.commit("req-c1", "ghost-round", "alice", "ab" * 32)
    assert excinfo.value.code == ErrorCode.ROUND_NOT_FOUND
