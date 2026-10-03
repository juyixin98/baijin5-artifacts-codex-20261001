"""Profile validation and registry tests."""

from __future__ import annotations

import pytest

from iccconv.contract import ColorSpace
from iccconv.errors import (
    InvalidProfileError,
    MissingProfileError,
    ProfileRoleMismatchError,
)
from iccconv.profiles import ensure_role, validate_profile


def test_valid_srgb_profile(profile_bytes):
    info = validate_profile(profile_bytes("sRGB.icc"), source="test")
    assert info.color_space is ColorSpace.RGB
    assert info.device_class == "mntr"
    assert info.is_matrix_shaper
    assert info.pcs == "XYZ"
    redacted = info.redacted()
    assert redacted["sha256_16"] == info.sha256[:16]
    assert "sha256" not in redacted  # redacted view carries the truncated digest only


def test_valid_cmyk_profile(profile_bytes):
    info = validate_profile(profile_bytes("SWOP_TR003_coated_3.icc"))
    assert info.color_space is ColorSpace.CMYK
    assert info.device_class == "prtr"


def test_truncated_profile_rejected(bad_profile_bytes):
    with pytest.raises(InvalidProfileError):
        validate_profile(bad_profile_bytes("truncated.icc"))


def test_garbage_profile_rejected(bad_profile_bytes):
    with pytest.raises(InvalidProfileError):
        validate_profile(bad_profile_bytes("garbage.icc"))


def test_empty_profile_rejected():
    with pytest.raises(InvalidProfileError):
        validate_profile(b"")


def test_named_color_class_rejected(bad_profile_bytes):
    # A syntactically valid ICC profile, but class 'nmcl' cannot be a
    # conversion endpoint.
    with pytest.raises(InvalidProfileError) as excinfo:
        validate_profile(bad_profile_bytes("named_color.icc"))
    assert excinfo.value.detail["device_class"] == "nmcl"


def test_role_mismatch_detected(profile_bytes):
    cmyk_info = validate_profile(profile_bytes("SWOP_TR003_coated_3.icc"))
    with pytest.raises(ProfileRoleMismatchError):
        ensure_role(cmyk_info, ColorSpace.RGB, role="source")


def test_registry_lists_profiles(registry):
    names = registry.names()
    assert "sRGB.icc" in names
    assert "SWOP_TR003_coated_3.icc" in names


def test_registry_rejects_traversal(registry):
    with pytest.raises(MissingProfileError):
        registry.load_bytes("../profiles/sRGB.icc")
    with pytest.raises(MissingProfileError):
        registry.load_bytes("/etc/passwd")


def test_registry_missing_name(registry):
    with pytest.raises(MissingProfileError) as excinfo:
        registry.load_bytes("no-such-profile.icc")
    assert "sRGB.icc" in excinfo.value.detail["available"]
