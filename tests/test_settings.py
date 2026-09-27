"""配置模块测试：默认值、环境变量覆盖与参数校验。"""
import pytest

from config.settings import SolverSettings, load_settings


def test_defaults_are_valid():
    s = SolverSettings()
    assert s.tolerance == 1e-10
    assert s.mp_dps == 60


@pytest.mark.parametrize(
    "field,value",
    [
        ("tolerance", 0.0),
        ("tolerance", 1.5),
        ("stall_ratio", 0.0),
        ("stall_ratio", 1.0),
        ("fp32_max_iter", 0),
        ("mp_dps", 10),
        ("cond_threshold", 0.5),
    ])
def test_invalid_values_rejected(field, value):
    with pytest.raises(ValueError):
        SolverSettings(**{field: value})


def test_env_override(monkeypatch):
    monkeypatch.setenv("IRSOLVER_TOLERANCE", "1e-12")
    monkeypatch.setenv("IRSOLVER_MP_DPS", "80")
    monkeypatch.setenv("IRSOLVER_FP32_MAX_ITER", "20")
    s = load_settings()
    assert s.tolerance == 1e-12
    assert s.mp_dps == 80
    assert s.fp32_max_iter == 20
    assert s.fp64_max_iter == 8  # 未设置的项保持默认


def test_env_absent_uses_defaults(monkeypatch):
    for key in list(__import__("os").environ):
        if key.startswith("IRSOLVER_"):
            monkeypatch.delenv(key)
    assert load_settings() == SolverSettings()
