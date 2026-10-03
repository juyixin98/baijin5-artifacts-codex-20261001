"""Parameter compatibility: invalid combinations fail with a category."""

import pytest

from app.config import MinimizerConfig
from app.errors import InvalidParameterError


def test_defaults_are_valid():
    cfg = MinimizerConfig()
    assert cfg.k == 7 and cfg.window == 4
    assert cfg.min_sequence_length == 10
    assert cfg.tie_break == "rightmost"


@pytest.mark.parametrize("bad_k", [0, 1, -3, 32, 100])
def test_k_out_of_range_rejected(bad_k):
    with pytest.raises(InvalidParameterError) as excinfo:
        MinimizerConfig(k=bad_k)
    assert excinfo.value.category == "INVALID_PARAMETER"
    assert excinfo.value.detail["parameter"] == "k"


@pytest.mark.parametrize("bad_w", [0, -1, 257])
def test_window_out_of_range_rejected(bad_w):
    with pytest.raises(InvalidParameterError) as excinfo:
        MinimizerConfig(window=bad_w)
    assert excinfo.value.category == "INVALID_PARAMETER"
    assert excinfo.value.detail["parameter"] == "window"


def test_unknown_hash_rejected():
    with pytest.raises(InvalidParameterError) as excinfo:
        MinimizerConfig(hash_name="md5")
    assert excinfo.value.category == "INVALID_PARAMETER"


def test_occurrence_cap_must_be_positive():
    with pytest.raises(InvalidParameterError) as excinfo:
        MinimizerConfig(max_hash_occurrences=0)
    assert excinfo.value.category == "INVALID_PARAMETER"


def test_tie_break_is_fixed():
    with pytest.raises(InvalidParameterError) as excinfo:
        MinimizerConfig(tie_break="leftmost")
    assert excinfo.value.category == "INVALID_PARAMETER"


def test_from_dict_rejects_unknown_keys():
    with pytest.raises(InvalidParameterError) as excinfo:
        MinimizerConfig.from_dict({"k": 7, "bogus": 1})
    assert excinfo.value.category == "INVALID_PARAMETER"
    assert excinfo.value.detail["unknown_keys"] == ["bogus"]


def test_from_dict_roundtrip():
    cfg = MinimizerConfig(k=5, window=3, hash_seed=42)
    assert MinimizerConfig.from_dict(cfg.to_dict()) == cfg
