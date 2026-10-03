import numpy as np
import pytest

from geodesic_recon.config import Settings, load_settings
from geodesic_recon.errors import ContractViolation


def test_default_settings_are_valid():
    s = Settings().validate()
    assert s.connectivity == 4
    assert s.boundary_rule == "edge-ignore"
    assert s.on_violation == "reject"


def test_load_settings_from_repo_config():
    s = load_settings()
    assert s.connectivity in (4, 8)
    assert s.algorithm in ("sync", "queue", "tiled")


def test_env_override(monkeypatch):
    monkeypatch.setenv("GEOREC_CONNECTIVITY", "8")
    monkeypatch.setenv("GEOREC_ON_VIOLATION", "clip")
    s = load_settings()
    assert s.connectivity == 8
    assert s.on_violation == "clip"


@pytest.mark.parametrize(
    "kwargs, category",
    [
        ({"connectivity": 6}, "BAD_CONNECTIVITY"),
        ({"on_violation": "ignore"}, "BAD_POLICY"),
        ({"algorithm": "fast"}, "BAD_ALGORITHM"),
        ({"tile_height": 0}, "BAD_TILE_SHAPE"),
        ({"max_pixels": 0}, "BAD_SIZE_LIMIT"),
        ({"boundary_rule": "wrap"}, "BAD_BOUNDARY_RULE"),
    ],
)
def test_invalid_settings_rejected(kwargs, category):
    with pytest.raises(ContractViolation) as exc:
        Settings(**kwargs).validate()
    assert exc.value.category == category
