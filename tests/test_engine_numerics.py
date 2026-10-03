"""Numerical correctness of the partitioned engine against independent
references: impulse identity, random signals, non-integral tails, ultra-long
IRs, block-size invariance, and state-budget accounting."""
from __future__ import annotations

import numpy as np
import pytest

from app.convolution import PartitionedConvolver, state_bytes_estimate
from app.errors import InvalidBlockSizeError, InvalidIRError
from app.fixtures import (
    make_exponential_ir,
    make_impulse,
    make_long_ir,
    make_lowpass_ir,
    make_random_signal,
)
from tests.conftest import run_stream
from tests.reference import direct_convolve_np, direct_convolve_py, fft_convolve_scipy

TOL = 1e-9


def test_impulse_reproduces_ir_exactly_in_length_and_values():
    ir = make_exponential_ir(500, seed=3)
    x = make_impulse(300)
    y = run_stream(x, ir, block_size=128)
    assert y.shape == (len(x) + len(ir) - 1,)
    np.testing.assert_allclose(y[: len(ir)], ir, atol=TOL)
    np.testing.assert_allclose(y[len(ir) :], 0.0, atol=TOL)


def test_random_signal_matches_numpy_direct_convolution():
    ir = make_lowpass_ir(257, cutoff=0.2)
    x = make_random_signal(4096, seed=42)
    y = run_stream(x, ir, block_size=256)
    ref = direct_convolve_np(x, ir)
    assert y.shape == ref.shape
    np.testing.assert_allclose(y, ref, atol=TOL)


def test_pure_python_reference_on_small_case():
    """Smallest possible config checked against a from-scratch Python loop."""
    rng = np.random.Generator(np.random.PCG64(5))
    ir = rng.standard_normal(13)
    x = rng.standard_normal(29)
    y = run_stream(x, ir, block_size=8)
    ref = direct_convolve_py(x, ir)
    assert y.shape == (29 + 13 - 1,)
    np.testing.assert_allclose(y, ref, atol=1e-12)


def test_scipy_fftconvolve_crosscheck():
    ir = make_exponential_ir(1000, seed=17)
    x = make_random_signal(2048, seed=18)
    y = run_stream(x, ir, block_size=512)
    np.testing.assert_allclose(y, fft_convolve_scipy(x, ir), atol=TOL)


@pytest.mark.parametrize("block_size", [64, 128, 256, 1024])
def test_block_size_invariance(block_size):
    """Same linear convolution regardless of the streaming block size."""
    ir = make_exponential_ir(700, seed=23)
    x = make_random_signal(1500, seed=24)
    y = run_stream(x, ir, block_size=block_size)
    ref = direct_convolve_np(x, ir)
    assert y.shape == ref.shape
    np.testing.assert_allclose(y, ref, atol=TOL)


def test_block_size_results_are_pairwise_identical():
    ir = make_exponential_ir(700, seed=23)
    x = make_random_signal(1500, seed=24)
    outs = [run_stream(x, ir, block_size=b) for b in (64, 128, 256, 1024)]
    for other in outs[1:]:
        assert other.shape == outs[0].shape
        np.testing.assert_allclose(other, outs[0], atol=1e-12)


def test_non_integral_tail():
    """Input length not a multiple of the block size: 3*256 + 37 samples."""
    ir = make_exponential_ir(400, seed=31)
    x = make_random_signal(3 * 256 + 37, seed=32)
    y = run_stream(x, ir, block_size=256)
    ref = direct_convolve_np(x, ir)
    assert y.shape == (len(x) + len(ir) - 1,)
    np.testing.assert_allclose(y, ref, atol=TOL)


def test_ultra_long_ir():
    ir = make_long_ir(100_000, seed=99)
    x = make_random_signal(20_000, seed=100)
    y = run_stream(x, ir, block_size=1024)
    ref = direct_convolve_np(x, ir)
    assert y.shape == (len(x) + len(ir) - 1,)
    np.testing.assert_allclose(y, ref, atol=1e-8)


def test_tail_is_fully_flushed_after_input_ends():
    """The last L-1 output samples (the pure tail) must match the reference,
    not be silently truncated to zero."""
    ir = make_exponential_ir(300, seed=41)
    x = make_random_signal(512, seed=43)
    y = run_stream(x, ir, block_size=128)
    ref = direct_convolve_np(x, ir)
    tail = y[len(x) :]
    assert tail.shape == (len(ir) - 1,)
    np.testing.assert_allclose(tail, ref[len(x) :], atol=TOL)
    assert np.max(np.abs(tail)) > 1e-3  # tail carries real energy


def test_state_bytes_include_spectrum_history():
    block_size = 256
    ir = make_exponential_ir(1000, seed=51)
    conv = PartitionedConvolver(ir, block_size)
    report = conv.state_bytes()
    partitions = conv.num_partitions
    bins = block_size + 1
    assert report["ir_spectra"] == partitions * bins * 16
    assert report["input_fdl"] == partitions * bins * 16
    assert report["overlap_tail"] == block_size * 8
    assert report["total"] == sum(
        report[k] for k in ("ir_spectra", "input_fdl", "overlap_tail")
    )
    # Estimate used for budget checks must match the real allocation.
    assert state_bytes_estimate(len(ir), block_size) == report


def test_invalid_block_size_rejected():
    with pytest.raises(InvalidBlockSizeError):
        PartitionedConvolver(np.ones(16), block_size=100)  # not a power of two
    with pytest.raises(InvalidBlockSizeError):
        PartitionedConvolver(np.ones(16), block_size=4)  # below minimum


def test_invalid_ir_rejected():
    with pytest.raises(InvalidIRError):
        PartitionedConvolver(np.array([]), block_size=64)
    with pytest.raises(InvalidIRError):
        PartitionedConvolver(np.array([1.0, np.nan]), block_size=64)
