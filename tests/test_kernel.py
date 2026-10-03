"""Kernel tests: exact results on hand-computed fixtures, equivalence with
the independent naive reference, and convergence-trace recording."""
import numpy as np
import pytest

from fixtures import ALL_FIXTURES
from geodesic_recon.kernel import (
    QueueTrace,
    SyncTrace,
    dilate_once,
    reconstruct_queue,
    reconstruct_sync,
)
from naive_reference import naive_reconstruct


@pytest.mark.parametrize("factory", ALL_FIXTURES, ids=lambda f: f().name)
@pytest.mark.parametrize("connectivity", [4, 8])
def test_sync_matches_hand_computed_expected(factory, connectivity):
    fx = factory()
    result, trace = reconstruct_sync(fx.marker, fx.mask, connectivity)
    np.testing.assert_array_equal(result, fx.expected)
    assert isinstance(trace, SyncTrace)
    assert trace.iterations >= 1
    assert trace.changed_per_iteration[-1] == 0  # final pass is stable


@pytest.mark.parametrize("factory", ALL_FIXTURES, ids=lambda f: f().name)
@pytest.mark.parametrize("connectivity", [4, 8])
def test_queue_matches_hand_computed_expected(factory, connectivity):
    fx = factory()
    result, trace = reconstruct_queue(fx.marker, fx.mask, connectivity)
    np.testing.assert_array_equal(result, fx.expected)
    assert isinstance(trace, QueueTrace)
    assert trace.pops <= trace.pushes


@pytest.mark.parametrize("factory", ALL_FIXTURES, ids=lambda f: f().name)
def test_queue_equivalent_to_sync_iteration(factory):
    fx = factory()
    sync_result, _ = reconstruct_sync(fx.marker, fx.mask, 4)
    queue_result, _ = reconstruct_queue(fx.marker, fx.mask, 4)
    np.testing.assert_array_equal(sync_result, queue_result)


@pytest.mark.parametrize("factory", ALL_FIXTURES, ids=lambda f: f().name)
def test_sync_convergence_steps_match_naive_reference(factory):
    fx = factory()
    result, trace = reconstruct_sync(fx.marker, fx.mask, 4)
    naive_result, naive_passes = naive_reconstruct(fx.marker, fx.mask, 4)
    np.testing.assert_array_equal(result, naive_result)
    assert trace.iterations == naive_passes
    assert sum(trace.changed_per_iteration[:-1]) > 0  # real work happened


def test_dilate_boundary_rule_ignores_out_of_bounds():
    img = np.zeros((3, 3))
    img[0, 0] = 5.0
    dil = dilate_once(img, 4)
    # 4-neighborhood spread from the corner, no wrap-around to row/col 2.
    expected = np.array([[5.0, 5.0, 0.0], [5.0, 0.0, 0.0], [0.0, 0.0, 0.0]])
    np.testing.assert_array_equal(dil, expected)


@pytest.mark.parametrize("connectivity", [4, 8])
@pytest.mark.parametrize("shape", [(1, 1), (1, 7), (7, 1), (5, 9), (16, 16)])
def test_random_inputs_all_kernels_equal_naive(connectivity, shape):
    rng = np.random.default_rng(seed=hash((connectivity, shape)) % (2**32))
    mask = rng.uniform(0, 255, size=shape)
    marker = mask * rng.uniform(0, 1, size=shape)  # guarantees marker <= mask

    naive_result, _ = naive_reconstruct(marker, mask, connectivity)
    sync_result, sync_trace = reconstruct_sync(marker, mask, connectivity)
    queue_result, queue_trace = reconstruct_queue(marker, mask, connectivity)

    np.testing.assert_array_equal(sync_result, naive_result)
    np.testing.assert_array_equal(queue_result, naive_result)
    assert queue_trace.pops <= queue_trace.pushes
    assert sync_trace.changed_per_iteration[-1] == 0


def test_single_pixel_image():
    result, trace = reconstruct_queue([[3.0]], [[5.0]], 4)
    np.testing.assert_array_equal(result, [[3.0]])
    assert trace.pushes == 0  # nothing can grow


def test_marker_equal_mask_is_already_fixed_point():
    rng = np.random.default_rng(7)
    mask = rng.uniform(0, 1, size=(6, 6))
    result, trace = reconstruct_sync(mask, mask, 4)
    np.testing.assert_array_equal(result, mask)
    assert trace.iterations == 1  # one pass, zero changes
