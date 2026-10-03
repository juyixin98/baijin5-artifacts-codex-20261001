"""Configuration environment overrides and validation."""

import pytest

from depthcov.config import Settings
from depthcov.coverage import PER_RECORD, UNION_PER_QUERY


def test_defaults():
    s = Settings()
    assert s.min_mapq == 20
    assert s.dedup_policy == UNION_PER_QUERY
    assert s.external_sort_chunk_size > 0


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("DEPTHCOV_MIN_MAPQ", "37")
    monkeypatch.setenv("DEPTHCOV_DEDUP_POLICY", PER_RECORD)
    monkeypatch.setenv("DEPTHCOV_SORT_CHUNK", "1234")
    s = Settings.from_env()
    assert s.min_mapq == 37
    assert s.dedup_policy == PER_RECORD
    assert s.external_sort_chunk_size == 1234


def test_invalid_mapq_rejected(monkeypatch):
    monkeypatch.setenv("DEPTHCOV_MIN_MAPQ", "9999")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_invalid_policy_rejected(monkeypatch):
    monkeypatch.setenv("DEPTHCOV_DEDUP_POLICY", "nonsense")
    with pytest.raises(ValueError):
        Settings.from_env()


def test_invalid_chunk_rejected(monkeypatch):
    monkeypatch.setenv("DEPTHCOV_SORT_CHUNK", "0")
    with pytest.raises(ValueError):
        Settings.from_env()
