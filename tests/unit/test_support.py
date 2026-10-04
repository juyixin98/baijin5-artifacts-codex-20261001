"""Keyring, staging and audit-redaction unit tests."""

from __future__ import annotations

import base64
import json
import os

import pytest

from app.core.audit import AuditLog, redact, safe_state
from app.core.errors import KeyMaterialError, StorageError
from app.core.keyring import generate_key_bundle, load_keys, save_key_file
from app.core.staging import StagingArea

pytestmark = pytest.mark.unit


# ---- keyring ---------------------------------------------------------------

def test_key_file_roundtrip_and_permissions(tmp_path):
    p = tmp_path / "keys" / "k.json"
    bundle = generate_key_bundle("kid-x")
    save_key_file(p, bundle)
    assert p.stat().st_mode & 0o777 == 0o600
    loaded = load_keys(p)
    assert loaded.key_id == "kid-x"
    assert loaded.aead_key == bundle.aead_key
    assert loaded.nonce_key == bundle.nonce_key


def test_key_file_rejected_if_group_or_world_readable(tmp_path):
    p = tmp_path / "k.json"
    save_key_file(p, generate_key_bundle())
    os.chmod(p, 0o644)
    with pytest.raises(KeyMaterialError) as ei:
        load_keys(p)
    assert ei.value.category == "key_material"


def test_keys_from_env_take_precedence(tmp_path):
    bundle = generate_key_bundle()
    env = {
        "SSEA_AEAD_KEY_B64": base64.b64encode(bundle.aead_key).decode(),
        "SSEA_NONCE_KEY_B64": base64.b64encode(bundle.nonce_key).decode(),
        "SSEA_KEY_ID": "env-kid",
    }
    loaded = load_keys(tmp_path / "missing.json", env=env)
    assert loaded.key_id == "env-kid"
    assert loaded.aead_key == bundle.aead_key


def test_bad_base64_key_rejected(tmp_path):
    p = tmp_path / "k.json"
    p.write_text(json.dumps({"key_id": "x", "aead_key": "!!!",
                             "nonce_key": "!!!"}))
    os.chmod(p, 0o600)
    with pytest.raises(KeyMaterialError):
        load_keys(p)


# ---- staging ---------------------------------------------------------------

def test_staging_dirs_are_owner_only(tmp_path):
    area = StagingArea(tmp_path / "s", tmp_path / "r")
    assert area.staging_root.stat().st_mode & 0o777 == 0o700
    assert area.release_root.stat().st_mode & 0o777 == 0o700


def test_stage_and_release_assembles_in_order(tmp_path):
    area = StagingArea(tmp_path / "s", tmp_path / "r")
    for i, chunk in enumerate([b"aaa", b"bbb", b"ccc"]):
        path = area.stage("m1", i, chunk)
        assert os.stat(path).st_mode & 0o777 == 0o600
    out = area.release("m1", 3, 9)
    assert out == b"aaabbbccc"
    assert area.read_released("m1") == b"aaabbbccc"
    # Fragments removed after release.
    assert not list(area.staging_root.rglob("*.part"))


def test_release_detects_length_mismatch(tmp_path):
    area = StagingArea(tmp_path / "s", tmp_path / "r")
    area.stage("m2", 0, b"ab")
    with pytest.raises(StorageError) as ei:
        area.release("m2", 1, 99)
    assert ei.value.category == "storage"
    assert area.read_released("m2") is None


def test_release_with_missing_fragment_fails(tmp_path):
    area = StagingArea(tmp_path / "s", tmp_path / "r")
    area.stage("m3", 0, b"ab")
    with pytest.raises(StorageError):
        area.release("m3", 2, 4)


def test_shred_removes_fragments(tmp_path):
    area = StagingArea(tmp_path / "s", tmp_path / "r")
    area.stage("m4", 0, b"ab")
    area.shred("m4")
    assert area.stage_exists("m4", 0) is False


# ---- audit / redaction -----------------------------------------------------

def test_redaction_hashes_sensitive_bytes_and_strings():
    out = redact("aead_key", b"secret-bytes")
    assert "secret" not in out and out.startswith("<redacted:")
    out2 = redact("token", "supersecretstring")
    assert "supersecret" not in out2
    assert redact("seqno", 5) == 5


def test_safe_state_redacts_mapping():
    state = safe_state({"seqno": 1, "plaintext": b"abc", "ciphertext": b"x"})
    assert state["seqno"] == 1
    assert state["plaintext"] != b"abc"


def test_audit_event_structure(caplog):
    import logging
    caplog.set_level(logging.INFO, logger="ssea.audit")
    AuditLog().event("reject", "auth_failed", "req-1",
                     "tag mismatch", message_id="m", seqno=2,
                     plaintext=b"secret")
    assert "req-1" in caplog.text
    assert "secret" not in caplog.text
    assert "auth_failed" in caplog.text
