"""Cross-check the streaming core against an independent reference.

The reference (tests/reference_impl.py) is written from the spec with
plain Python loops and shares no code with the package. Fixtures and
seeded random signals are compared bit-tightly.
"""

from __future__ import annotations

import numpy as np
import pytest

from limiter import limit_offline
from reference_impl import reference_limiter

FIXTURE_NAMES = [
    "short_impulse",
    "stereo_imbalance",
    "sustained_peaks",
    "block_boundary_burst",
    "gain_recovery",
]


def _reference_args(cfg):
    return (
        cfg.threshold_linear,
        cfg.attack_coeff,
        cfg.release_coeff,
        cfg.lookahead_samples,
    )


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_core_matches_independent_reference(default_config, name, request):
    pcm, _ = request.getfixturevalue(name)
    result = limit_offline(default_config, pcm)
    ref_out, ref_gain = reference_limiter(pcm, *_reference_args(default_config))
    np.testing.assert_allclose(result.output, ref_out, rtol=1e-12, atol=1e-15)
    np.testing.assert_allclose(result.gain, ref_gain, rtol=1e-12, atol=1e-15)


@pytest.mark.parametrize("block_size", [1, 63, 1000])
def test_reference_matches_across_block_sizes(default_config, block_size):
    rng = np.random.default_rng(42)
    pcm = rng.uniform(-0.98, 0.98, size=(1500, 2))
    result = limit_offline(default_config, pcm, block_size=block_size)
    ref_out, ref_gain = reference_limiter(pcm, *_reference_args(default_config))
    np.testing.assert_allclose(result.output, ref_out, rtol=1e-12, atol=1e-15)
    np.testing.assert_allclose(result.gain, ref_gain, rtol=1e-12, atol=1e-15)
