"""Protocol-encoding tests: TLV injectivity, round-trip, strict parsing."""

from __future__ import annotations

import pytest

from keytree import encoding
from keytree.errors import InputValidationError


def test_round_trip_preserves_fields():
    fields = [(b"scope", b"tenant"), (b"tenant", b"acme"), (b"empty", b"")]
    assert encoding.decode_info(encoding.encode_info(fields)) == fields


def test_label_concatenation_collision_is_impossible():
    """Counterexample to naive string concatenation: ("ab","c") vs ("a","bc")
    collide under plain concat but must differ under TLV."""
    naive_a = b"ab" + b"c"
    naive_b = b"a" + b"bc"
    assert naive_a == naive_b  # the collision we are guarding against

    tlv_a = encoding.encode_info([(b"x", b"ab"), (b"y", b"c")])
    tlv_b = encoding.encode_info([(b"x", b"a"), (b"y", b"bc")])
    assert tlv_a != tlv_b


def test_field_boundary_collision_is_impossible():
    """Values containing the next tag's bytes must not shift boundaries."""
    a = encoding.encode_info([(b"t", b"u\x00\x01v")])
    b = encoding.encode_info([(b"t", b"u"), (b"v", b"")])
    assert a != b


def test_empty_value_vs_missing_field_differ():
    a = encoding.encode_info([(b"k", b"")])
    b = encoding.encode_info([])
    assert a != b
    assert encoding.decode_info(a) == [(b"k", b"")]
    assert encoding.decode_info(b) == []


def test_decode_rejects_truncated_block():
    blob = encoding.encode_info([(b"tag", b"value")])
    with pytest.raises(InputValidationError):
        encoding.decode_info(blob[:-1])


def test_decode_rejects_trailing_garbage():
    blob = encoding.encode_info([(b"tag", b"value")]) + b"\x00"
    with pytest.raises(InputValidationError):
        encoding.decode_info(blob)


def test_decode_rejects_bad_magic():
    with pytest.raises(InputValidationError):
        encoding.decode_info(b"XXXX" + b"\x00\x00")


def test_tag_length_limit_enforced():
    with pytest.raises(InputValidationError):
        encoding.encode_field(b"t" * (encoding.MAX_TAG_LEN + 1), b"v")


def test_value_length_limit_enforced():
    with pytest.raises(InputValidationError):
        encoding.encode_field(b"t", b"v" * (encoding.MAX_VALUE_LEN + 1))


def test_field_count_limit_enforced():
    with pytest.raises(InputValidationError):
        encoding.encode_info([(b"t", b"v")] * (encoding.MAX_FIELDS + 1))


def test_error_category_is_input_error():
    try:
        encoding.decode_info(b"garbage")
    except InputValidationError as exc:
        assert exc.category == "input_error"
    else:
        raise AssertionError("expected InputValidationError")
