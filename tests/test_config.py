"""Configuration validation: bad configs must fail loudly and specifically."""

import pytest

from mfcc_backend.config import MFCCConfig
from mfcc_backend.errors import ConfigError


def test_default_config_is_valid_and_derived_values_fixed(run_log):
    cfg = MFCCConfig().validate()
    assert cfg.frame_length == 400  # 25 ms @ 16 kHz
    assert cfg.hop_length == 160  # 10 ms @ 16 kHz
    assert cfg.nfft == 400  # fixed spec: nfft == frame_length
    assert cfg.n_freq_bins == 201
    assert cfg.fmax == 8000.0
    run_log("default_config", derived=cfg.as_dict(), verdict="pass",
            rationale="25ms/10ms at 16kHz must give exactly 400/160 samples")


@pytest.mark.parametrize(
    "overrides,fragment",
    [
        ({"n_mfcc": 30}, "n_mfcc"),
        ({"hop_length_ms": 30.0}, "hop_length"),
        ({"preemphasis_coef": 1.0}, "preemphasis"),
        ({"preemphasis_coef": -0.1}, "preemphasis"),
        ({"fmin_hz": -5.0}, "fmin"),
        ({"fmax_hz": 9000.0}, "Nyquist"),
        ({"log_floor": 0.0}, "log_floor"),
        ({"delta_width": 0}, "delta_width"),
        ({"n_mels": 0}, "n_mels"),
        ({"frame_length_ms": 0.05}, "frame too short"),  # < 2 samples @16kHz
    ],
)
def test_invalid_configs_raise_config_error(overrides, fragment, run_log):
    with pytest.raises(ConfigError) as excinfo:
        MFCCConfig(**overrides).validate()
    assert fragment in str(excinfo.value)
    assert excinfo.value.http_status == 422
    run_log("invalid_config_rejected", overrides=overrides,
            error_code=excinfo.value.code, verdict="pass",
            rationale="invalid config must raise ConfigError, not be clamped silently")


def test_sample_rate_changes_frame_geometry():
    cfg = MFCCConfig(sample_rate=8000).validate()
    assert cfg.frame_length == 200
    assert cfg.hop_length == 80
    assert cfg.fmax == 4000.0
