"""Protocol encoding tests: round-trip, injectivity, collision counterexamples."""

from __future__ import annotations

import pytest

from kds.encoding import decode_fields, encode_fields
from kds.errors import InputError


def test_round_trip_mixed_types():
    fields = ("kds1", "level", "version", 7, "ctx")
    assert decode_fields(encode_fields(*fields)) == fields


def test_length_prefix_collision_counterexample():
    """("ab","c") and ("a","bc") collide under naive concatenation; our
    encoding must keep them distinct — this is the canonical counterexample
    for plain string joining."""
    naive_a = "ab" + "c"
    naive_b = "a" + "bc"
    assert naive_a == naive_b  # the failure mode we are guarding against

    enc_a = encode_fields("ab", "c")
    enc_b = encode_fields("a", "bc")
    assert enc_a != enc_b
    assert decode_fields(enc_a) == ("ab", "c")
    assert decode_fields(enc_b) == ("a", "bc")


def test_type_tag_distinguishes_int_from_str():
    assert encode_fields("1") != encode_fields(1)
    assert decode_fields(encode_fields(1)) == (1,)


def test_embedded_separator_bytes_are_safe():
    tricky = "a\x00\x01KDS1|b"
    assert decode_fields(encode_fields(tricky)) == (tricky,)


def test_empty_field_rejected():
    with pytest.raises(InputError):
        encode_fields("")


def test_no_fields_rejected():
    with pytest.raises(InputError):
        encode_fields()


def test_unsupported_type_rejected():
    with pytest.raises(InputError):
        encode_fields(b"raw-bytes")  # type: ignore[arg-type]


def test_bool_rejected_even_though_int_subclass():
    with pytest.raises(InputError):
        encode_fields(True)  # type: ignore[arg-type]


def test_oversized_field_rejected():
    with pytest.raises(InputError):
        encode_fields("x" * 300)


def test_decode_rejects_truncated_blob():
    blob = encode_fields("alpha", 3)
    with pytest.raises(InputError):
        decode_fields(blob[:-1])


def test_decode_rejects_trailing_bytes():
    blob = encode_fields("alpha") + b"\x00"
    with pytest.raises(InputError):
        decode_fields(blob)


def test_decode_rejects_bad_magic():
    with pytest.raises(InputError):
        decode_fields(b"XXXX" + encode_fields("a")[4:])
