"""Config validation: each violated constraint must raise ConfigError."""

import pytest

from mfcc_backend import ConfigError, MFCCConfig


def test_default_config_is_valid(tlog):
    cfg = MFCCConfig().validate()
    tlog.step("validate_default", basis="defaults satisfy all constraints",
              frame_length=cfg.frame_length, hop_length=cfg.hop_length,
              fmax=cfg.resolved_fmax)
    assert cfg.frame_length == 400
    assert cfg.hop_length == 160
    assert cfg.resolved_fmax == 8000.0


@pytest.mark.parametrize(
    "override,fragment",
    [
        ({"sample_rate": 0}, "sample_rate"),
        ({"preemphasis": 1.0}, "preemphasis"),
        ({"window_ms": 0.0}, "window_ms"),
        ({"hop_ms": -1.0}, "hop_ms"),
        ({"n_fft": 128}, "n_fft"),          # < frame_length=400
        ({"n_mels": 0}, "n_mels"),
        ({"n_mfcc": 27}, "n_mfcc"),          # > n_mels=26
        ({"fmin": -5.0}, "fmin"),
        ({"fmax": 9000.0}, "fmax"),          # > Nyquist at 16 kHz
        ({"fmin": 4000.0, "fmax": 3000.0}, "fmin"),
        ({"log_floor": 0.0}, "log_floor"),
        ({"delta_width": 0}, "delta_width"),
        ({"lifter": -1}, "lifter"),
    ],
)
def test_invalid_configs_raise_config_error(override, fragment, tlog):
    tlog.step("validate_invalid", basis=f"ConfigError mentions {fragment!r}",
              override=override)
    with pytest.raises(ConfigError) as excinfo:
        MFCCConfig(**override).validate()
    assert fragment in str(excinfo.value)
    assert excinfo.value.category == "config"


def test_frame_geometry_scales_with_sample_rate(tlog):
    cfg = MFCCConfig(sample_rate=8000).validate()
    tlog.step("geometry_8k", basis="25ms/10ms at 8kHz -> 200/80 samples",
              frame_length=cfg.frame_length, hop_length=cfg.hop_length)
    assert cfg.frame_length == 200
    assert cfg.hop_length == 80
