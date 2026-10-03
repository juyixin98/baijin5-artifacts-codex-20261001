"""Validation module tests: property checks pass on genuine results and fail
with the right check name on tampered ones."""
import numpy as np
import pytest

from fixtures import ALL_FIXTURES
from geodesic_recon.kernel import reconstruct_queue
from geodesic_recon.validation import (
    check_idempotent,
    check_monotone,
    verify_result,
)


@pytest.mark.parametrize("factory", ALL_FIXTURES, ids=lambda f: f().name)
def test_genuine_result_passes_all_checks(factory):
    fx = factory()
    result, _ = reconstruct_queue(fx.marker, fx.mask, 4)
    report = verify_result(fx.marker, fx.mask, result, 4)
    assert report.ok, report.to_dict()


def test_tampered_above_mask_fails_within_mask():
    fx = ALL_FIXTURES[2]()  # flat_zone
    result, _ = reconstruct_queue(fx.marker, fx.mask, 4)
    tampered = result.copy()
    tampered[0, 0] = fx.mask[0, 0] + 1.0
    report = verify_result(fx.marker, fx.mask, tampered, 4)
    by_name = {c.name: c for c in report.checks}
    assert not report.ok
    assert not by_name["within_mask"].passed
    assert by_name["within_mask"].violations == 1


def test_tampered_below_fixed_point_fails_fixed_point():
    fx = ALL_FIXTURES[2]()  # flat_zone: expected all 60
    result, _ = reconstruct_queue(fx.marker, fx.mask, 4)
    tampered = result.copy()
    tampered[0, 0] = 10.0  # still >= marker[0,0]=0 and <= mask, but not a fixed point
    report = verify_result(fx.marker, fx.mask, tampered, 4)
    by_name = {c.name: c for c in report.checks}
    assert not report.ok
    assert by_name["above_marker"].passed
    assert by_name["within_mask"].passed
    assert not by_name["fixed_point"].passed


def test_result_below_marker_fails_above_marker():
    fx = ALL_FIXTURES[2]()
    result, _ = reconstruct_queue(fx.marker, fx.mask, 4)
    tampered = result.copy()
    tampered[1, 1] = 0.0  # marker[1,1] = 60
    report = verify_result(fx.marker, fx.mask, tampered, 4)
    by_name = {c.name: c for c in report.checks}
    assert not by_name["above_marker"].passed


def test_shape_mismatch_reported():
    report = verify_result(np.zeros((2, 2)), np.zeros((2, 2)), np.zeros((3, 3)), 4)
    assert not report.ok
    assert report.checks[0].name == "shape_consistent"


@pytest.mark.parametrize("factory", ALL_FIXTURES, ids=lambda f: f().name)
def test_idempotent_on_fixtures(factory):
    fx = factory()
    check = check_idempotent(fx.marker, fx.mask, algorithm="queue", connectivity=4)
    assert check.passed, check


@pytest.mark.parametrize("factory", ALL_FIXTURES, ids=lambda f: f().name)
def test_monotone_on_scaled_markers(factory):
    fx = factory()
    low = fx.marker * 0.5
    high = fx.marker  # low <= high <= mask
    check = check_monotone(low, high, fx.mask, algorithm="queue", connectivity=4)
    assert check.passed, check


def test_monotone_random_pairs():
    rng = np.random.default_rng(123)
    for _ in range(5):
        mask = rng.uniform(0, 100, size=(9, 11))
        high = mask * rng.uniform(0, 1, size=(9, 11))
        low = high * rng.uniform(0, 1, size=(9, 11))
        check = check_monotone(low, high, mask, algorithm="sync", connectivity=4)
        assert check.passed, check


def test_result_never_exceeds_mask_random():
    rng = np.random.default_rng(9)
    mask = rng.uniform(0, 50, size=(13, 8))
    marker = mask * rng.uniform(0, 1, size=(13, 8))
    result, _ = reconstruct_queue(marker, mask, 8)
    assert (result <= mask).all()
    assert (result >= marker).all()
