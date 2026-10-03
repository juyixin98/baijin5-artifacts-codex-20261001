"""Profile registry tests: each bad-profile class maps to a category."""
from __future__ import annotations

import json
import shutil

import pytest

from colorconvert.contract import ColorMode
from colorconvert.errors import FailureCategory, ProfileError
from colorconvert.profiles import ProfileRegistry


def _write_registry(tmp_path, entries):
    spec = {"version": 1, "profiles": entries}
    path = tmp_path / "REGISTRY.json"
    path.write_text(json.dumps(spec))
    return path


def _entry(profile_id, file, sha256, color_space="RGB", roles=None):
    return {
        "id": profile_id,
        "file": file,
        "sha256": sha256,
        "color_space": color_space,
        "device_class": "mntr",
        "roles": roles or ["source", "target"],
        "cmyk_restricted": False,
        "description": "test",
    }


def test_registry_loads_and_pins_hashes(registry):
    handle = registry.get("srgb", role="source")
    assert handle.color_space == "RGB"
    assert len(handle.sha256) == 64
    assert "fogra39-cmyk" in registry


def test_unknown_profile_id_rejected(registry):
    with pytest.raises(ProfileError) as ei:
        registry.get("does-not-exist", role="source")
    assert ei.value.category is FailureCategory.PROFILE_MISSING


def test_missing_file_rejected(tmp_path, settings):
    reg = _write_registry(
        tmp_path, [_entry("ghost", "nope.icc", "0" * 64)]
    )
    with pytest.raises(ProfileError) as ei:
        ProfileRegistry.load(reg)
    assert ei.value.category is FailureCategory.PROFILE_MISSING


def test_hash_mismatch_rejected(tmp_path, settings):
    src = settings.profile_dir / "sRGB.icc"
    tampered = tmp_path / "tampered.icc"
    data = bytearray(src.read_bytes())
    data[100] ^= 0xFF
    tampered.write_bytes(bytes(data))
    # registry pins the ORIGINAL hash -> tampered file must be caught
    from colorconvert.profiles import sha256_bytes

    reg = _write_registry(
        tmp_path,
        [_entry("srgb", "tampered.icc", sha256_bytes(src.read_bytes()))],
    )
    with pytest.raises(ProfileError) as ei:
        ProfileRegistry.load(reg)
    assert ei.value.category is FailureCategory.PROFILE_HASH_MISMATCH


def test_corrupt_profile_rejected(tmp_path):
    bad = tmp_path / "bad.icc"
    bad.write_bytes(b"this is not an ICC profile at all" * 8)
    from colorconvert.profiles import sha256_bytes

    reg = _write_registry(
        tmp_path, [_entry("bad", "bad.icc", sha256_bytes(bad.read_bytes()))]
    )
    with pytest.raises(ProfileError) as ei:
        ProfileRegistry.load(reg)
    assert ei.value.category is FailureCategory.PROFILE_CORRUPT


def test_colorspace_drift_between_registry_and_file_rejected(
    tmp_path, settings
):
    from colorconvert.profiles import sha256_bytes

    shutil.copy(settings.profile_dir / "sRGB.icc", tmp_path / "sRGB.icc")
    reg = _write_registry(
        tmp_path,
        [
            _entry(
                "srgb",
                "sRGB.icc",
                sha256_bytes((settings.profile_dir / "sRGB.icc").read_bytes()),
                color_space="CMYK",  # registry lies about the file
            )
        ],
    )
    with pytest.raises(ProfileError) as ei:
        ProfileRegistry.load(reg)
    assert ei.value.category is FailureCategory.PROFILE_COLORSPACE_MISMATCH


def test_role_not_allowed(tmp_path, settings):
    from colorconvert.profiles import sha256_bytes

    shutil.copy(settings.profile_dir / "sRGB.icc", tmp_path / "sRGB.icc")
    reg = _write_registry(
        tmp_path,
        [
            _entry(
                "srgb",
                "sRGB.icc",
                sha256_bytes((settings.profile_dir / "sRGB.icc").read_bytes()),
                roles=["source"],
            )
        ],
    )
    loaded = ProfileRegistry.load(reg)
    with pytest.raises(ProfileError) as ei:
        loaded.get("srgb", role="target")
    assert ei.value.category is FailureCategory.PROFILE_ROLE_NOT_ALLOWED


def test_embedded_profile_validated(registry, srgb_icc_bytes):
    handle = registry.from_embedded(srgb_icc_bytes, ColorMode.RGB)
    assert handle.origin == "embedded"
    assert handle.color_mode is ColorMode.RGB


def test_embedded_profile_mode_mismatch(registry, srgb_icc_bytes):
    with pytest.raises(ProfileError) as ei:
        registry.from_embedded(srgb_icc_bytes, ColorMode.CMYK)
    assert ei.value.category is FailureCategory.PROFILE_COLORSPACE_MISMATCH


def test_embedded_profile_corrupt(registry):
    with pytest.raises(ProfileError) as ei:
        registry.from_embedded(b"garbage-bytes", ColorMode.RGB)
    assert ei.value.category is FailureCategory.PROFILE_CORRUPT
