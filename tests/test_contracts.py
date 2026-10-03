"""Contract validation tests: every failure category is triggered by a
concrete bad input, and boundary values of the supported range are pinned."""
import numpy as np
import pytest

from wsola_backend.contracts import (
    MAX_TIME_SCALE,
    MIN_TIME_SCALE,
    ContractViolation,
    FailureCategory,
    validate_request,
)
from wsola_backend.wsola import make_params

SR = 16_000
WINDOW = make_params(SR, 1.0).window_len


def expect(category, **kwargs):
    with pytest.raises(ContractViolation) as err:
        validate_request(**kwargs)
    assert err.value.category == category


def valid_samples(n=4096):
    return np.zeros(n)


def test_valid_request_accepted():
    out = validate_request(SR, 1.5, valid_samples(), WINDOW)
    assert out.sample_rate == SR
    assert out.time_scale == 1.5
    assert out.n_samples == 4096


@pytest.mark.parametrize("ts", [MIN_TIME_SCALE, MAX_TIME_SCALE])
def test_time_scale_boundaries_accepted(ts):
    validate_request(SR, ts, valid_samples(), make_params(SR, ts).window_len)


@pytest.mark.parametrize("ts", [0.49, 2.01, 0.0, -1.0, float("nan"), float("inf")])
def test_unsupported_time_scale_rejected(ts):
    expect(FailureCategory.UNSUPPORTED_TIME_SCALE,
           sample_rate=SR, time_scale=ts, samples=valid_samples(), min_window_len=WINDOW)


@pytest.mark.parametrize("sr", [0, 7999, 48001, 16_000.5, "16000"])
def test_invalid_sample_rate_rejected(sr):
    expect(FailureCategory.INVALID_SAMPLE_RATE,
           sample_rate=sr, time_scale=1.0, samples=valid_samples(), min_window_len=WINDOW)


def test_empty_input_rejected():
    expect(FailureCategory.EMPTY_INPUT,
           sample_rate=SR, time_scale=1.0, samples=np.zeros(0), min_window_len=WINDOW)


def test_non_finite_samples_rejected():
    samples = valid_samples()
    samples[10] = np.nan
    expect(FailureCategory.NON_FINITE_SAMPLES,
           sample_rate=SR, time_scale=1.0, samples=samples, min_window_len=WINDOW)
    samples = valid_samples()
    samples[3] = np.inf
    expect(FailureCategory.NON_FINITE_SAMPLES,
           sample_rate=SR, time_scale=1.0, samples=samples, min_window_len=WINDOW)


def test_input_shorter_than_one_window_rejected():
    expect(FailureCategory.INPUT_TOO_SHORT,
           sample_rate=SR, time_scale=1.0, samples=valid_samples(WINDOW - 1), min_window_len=WINDOW)


def test_category_is_deterministic_for_multi_violation():
    # Non-finite AND too short: checks run in fixed order, non-finite wins
    # because emptiness/finiteness is validated before length.
    samples = np.full(WINDOW - 1, np.nan)
    expect(FailureCategory.NON_FINITE_SAMPLES,
           sample_rate=SR, time_scale=1.0, samples=samples, min_window_len=WINDOW)
