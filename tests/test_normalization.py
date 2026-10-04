"""Normalization: same value in different surface representations must
canonicalize identically; malformed inputs must fail with a typed error."""
import pytest

from sensitive_layer.errors import ValidationError
from sensitive_layer.protocol.normalize import normalize


@pytest.mark.parametrize("surface", [
    "alice@example.com",
    "Alice@Example.COM",
    "  alice@example.com  ",
    "ALICE@EXAMPLE.COM",
    "ａｌｉｃｅ＠ｅｘａｍｐｌｅ．ｃｏｍ",   # full-width ASCII folds under NFKC
])
def test_email_representations_canonicalize_equal(surface):
    assert normalize(surface, "email") == "alice@example.com"


@pytest.mark.parametrize("surface,expected", [
    ("1 (555) 010-1234", "15550101234"),
    ("15550101234", "15550101234"),
    ("+1 555 010 1234", "+15550101234"),
    ("１５５５０１０１２３４", "15550101234"),  # full-width digits
])
def test_phone_representations(surface, expected):
    assert normalize(surface, "phone") == expected


def test_name_whitespace_and_case():
    assert normalize("  Alice   Smith ", "name") == "alice smith"


def test_plus_significant_in_phone():
    assert normalize("+15550101234", "phone") != normalize("15550101234", "phone")


@pytest.mark.parametrize("value,field_type", [
    ("   ", "email"),          # empty after strip
    ("a b@example.com", "email"),  # interior whitespace
    ("()", "phone"),           # no digits
    ("", "name"),
])
def test_empty_or_malformed_rejected(value, field_type):
    with pytest.raises(ValidationError) as exc:
        normalize(value, field_type)
    assert exc.value.category == "validation_error"


def test_unknown_field_type_rejected():
    with pytest.raises(ValidationError):
        normalize("x", "no-such-type")
