"""Unit tests for configuration loading and validation."""

import pytest
from conftest import judgement

from msa_backend.config import DEFAULT_CONFIG_PATH, load_config


def test_default_config_loads_and_matches_contract():
    config = load_config()
    judgement(
        "config/settings.yaml", "config-load",
        "fixed policy values: uniform_split, threshold 0.6, coverage floor 2.0",
    )
    assert config.algorithm.ambiguity_policy == "uniform_split"
    assert config.algorithm.conservation_threshold == pytest.approx(0.6)
    assert config.algorithm.min_effective_coverage == pytest.approx(2.0)
    assert config.algorithm.alphabet == ("A", "C", "G", "T")


def test_invalid_ambiguity_policy_rejected(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text(
        DEFAULT_CONFIG_PATH.read_text(encoding="utf-8").replace(
            "uniform_split", "random_guess"
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="ambiguity_policy"):
        load_config(bad)


def test_invalid_thresholds_rejected(tmp_path):
    for old, new in [
        ("conservation_threshold: 0.6", "conservation_threshold: 1.5"),
        ("min_effective_coverage: 2.0", "min_effective_coverage: 0"),
        ("identity_threshold: 0.95", "identity_threshold: 2"),
    ]:
        bad = tmp_path / "bad.yaml"
        bad.write_text(
            DEFAULT_CONFIG_PATH.read_text(encoding="utf-8").replace(old, new),
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            load_config(bad)
