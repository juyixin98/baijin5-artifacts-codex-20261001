"""Tests for bandwidth selection."""
from __future__ import annotations

import numpy as np
import pytest

from app.bandwidths import select_bandwidth
from app.contract import BandwidthMethod, KernelName
from app.errors import BandwidthFailedError, InsufficientDataError


@pytest.mark.unit
def test_manual_bandwidth_respects_value_and_multiplier() -> None:
    rng = np.random.default_rng(0)
    x = rng.uniform(-1, 1, 500)
    y = x + rng.normal(0, 1, x.size)
    sel = select_bandwidth(
        x, y, 0.0, BandwidthMethod.MANUAL, 0.3, 1.0, 10, KernelName.TRIANGULAR
    )
    assert sel.left == sel.right == pytest.approx(0.3)
    sel2 = select_bandwidth(
        x, y, 0.0, BandwidthMethod.MANUAL, 0.3, 1.5, 10, KernelName.TRIANGULAR
    )
    assert sel2.left == pytest.approx(0.45)


@pytest.mark.unit
def test_manual_requires_positive_bandwidth() -> None:
    rng = np.random.default_rng(0)
    x = rng.uniform(-1, 1, 100)
    y = rng.normal(0, 1, 100)
    with pytest.raises(BandwidthFailedError):
        select_bandwidth(
            x, y, 0.0, BandwidthMethod.MANUAL, None, 1.0, 10, KernelName.TRIANGULAR
        )


@pytest.mark.unit
def test_ik_rot_finite_positive_and_within_support() -> None:
    rng = np.random.default_rng(1)
    x = rng.uniform(-1, 1, 2000)
    y = np.where(x >= 0, 3.0, 0.0) + 0.8 * x + rng.normal(0, 1, x.size)
    sel = select_bandwidth(
        x, y, 0.0, BandwidthMethod.IK_ROT, None, 1.0, 10, KernelName.TRIANGULAR
    )
    assert 0 < sel.left == sel.right < 1.0
    assert np.isfinite(sel.left)
    assert "curvature_gap" in sel.notes


@pytest.mark.unit
def test_ik_rot_stable_on_linear_null_no_nan() -> None:
    # The regularisation term must keep ROT finite when curvature gap ~ 0.
    rng = np.random.default_rng(2)
    x = rng.uniform(-1, 1, 2000)
    y = 1.0 * x + rng.normal(0, 1, x.size)
    sel = select_bandwidth(
        x, y, 0.0, BandwidthMethod.IK_ROT, None, 1.0, 10, KernelName.TRIANGULAR
    )
    assert np.isfinite(sel.left) and sel.left > 0


@pytest.mark.unit
def test_ik_rot_insufficient_side_is_unidentified() -> None:
    rng = np.random.default_rng(3)
    x = rng.uniform(-1, 1, 20)
    x[:19] = -np.abs(x[:19])  # only one point above cutoff
    y = rng.normal(0, 1, 20)
    with pytest.raises(InsufficientDataError):
        select_bandwidth(
            x, y, 0.0, BandwidthMethod.IK_ROT, None, 1.0, 3, KernelName.TRIANGULAR
        )


@pytest.mark.unit
def test_bandwidth_deterministic_given_same_data() -> None:
    rng = np.random.default_rng(4)
    x = rng.uniform(-1, 1, 1000)
    y = (x >= 0) * 2.0 + rng.normal(0, 1, x.size)
    a = select_bandwidth(
        x, y, 0.0, BandwidthMethod.IK_ROT, None, 1.0, 10, KernelName.TRIANGULAR
    )
    b = select_bandwidth(
        x, y, 0.0, BandwidthMethod.IK_ROT, None, 1.0, 10, KernelName.TRIANGULAR
    )
    assert a.left == b.left
