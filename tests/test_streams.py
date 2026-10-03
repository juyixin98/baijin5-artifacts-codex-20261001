"""Stream state and parameter-versioning tests (registry level)."""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import butter

from app.dsp.coefficients import normalize_and_validate
from app.errors import ResourceLimitError, StreamNotFoundError, VersionConflictError
from app.streams import StreamRegistry, TransientPolicy


def _sos(cutoff: float = 0.2, order: int = 2) -> np.ndarray:
    return butter(order, cutoff, output="sos")


def _make(registry: StreamRegistry, sos=None, transient=TransientPolicy.CARRY):
    sos = _sos() if sos is None else sos
    return registry.create(normalize_and_validate(sos), 2, transient)


def test_version_increments_on_update(run_log):
    reg = StreamRegistry()
    stream = _make(reg)
    assert stream.param_version == 1
    reg.update_coefficients(stream.stream_id, normalize_and_validate(_sos(0.3)),
                            None, expected_version=1)
    assert stream.param_version == 2
    run_log.log("version_bump", version=stream.param_version, verdict="pass")


def test_stale_expected_version_conflicts(run_log):
    reg = StreamRegistry()
    stream = _make(reg)
    reg.update_coefficients(stream.stream_id, normalize_and_validate(_sos(0.3)),
                            None, expected_version=1)
    with pytest.raises(VersionConflictError) as exc:
        reg.update_coefficients(stream.stream_id, normalize_and_validate(_sos(0.4)),
                                None, expected_version=1)
    assert exc.value.category.value == "state_conflict"
    run_log.log("conflict", current=exc.value.detail["current_version"],
                expected=exc.value.detail["expected_version"], verdict="pass")


def test_chunk_version_check_conflicts():
    reg = StreamRegistry()
    stream = _make(reg)
    with pytest.raises(VersionConflictError):
        reg.check_version(stream, 99)
    reg.check_version(stream, None)  # absent expectation never conflicts
    reg.check_version(stream, 1)


def test_carry_policy_preserves_state(run_log):
    reg = StreamRegistry()
    sos_a, sos_b = _sos(0.2), _sos(0.3)
    stream = _make(reg, sos_a, TransientPolicy.CARRY)
    stream.filter.process(np.ones((2, 64)))
    state_before = stream.filter.state.copy()
    reg.update_coefficients(stream.stream_id, normalize_and_validate(sos_b),
                            None, None)
    assert np.array_equal(stream.filter.state, state_before)
    run_log.log("carry", state_norm=float(np.abs(state_before).sum()), verdict="pass")


def test_reset_policy_zeroes_state(run_log):
    reg = StreamRegistry()
    sos_a, sos_b = _sos(0.2), _sos(0.3)
    stream = _make(reg, sos_a, TransientPolicy.RESET)
    stream.filter.process(np.ones((2, 64)))
    assert np.abs(stream.filter.state).sum() > 0
    reg.update_coefficients(stream.stream_id, normalize_and_validate(sos_b),
                            None, None)
    assert np.abs(stream.filter.state).sum() == 0.0
    # After reset the stream behaves exactly like a fresh one with sos_b.
    fresh = StreamRegistry().create(normalize_and_validate(sos_b), 2,
                                    TransientPolicy.RESET)
    x = np.random.default_rng(7).standard_normal((2, 100))
    assert np.array_equal(stream.filter.process(x), fresh.filter.process(x))
    run_log.log("reset", verdict="pass")


def test_section_count_change_forces_state_zeroing():
    reg = StreamRegistry()
    stream = _make(reg, _sos(0.2, order=2), TransientPolicy.CARRY)  # 1 section
    stream.filter.process(np.ones((2, 64)))
    reg.update_coefficients(stream.stream_id,
                            normalize_and_validate(_sos(0.3, order=4)),  # 2 sections
                            None, None)
    assert np.abs(stream.filter.state).sum() == 0.0
    assert stream.filter.n_sections == 2


def test_unknown_stream_raises_not_found():
    reg = StreamRegistry()
    with pytest.raises(StreamNotFoundError):
        reg.get("does-not-exist")
    with pytest.raises(StreamNotFoundError):
        reg.delete("does-not-exist")


def test_stream_limit_enforced():
    reg = StreamRegistry(max_streams=2)
    _make(reg)
    _make(reg)
    with pytest.raises(ResourceLimitError) as exc:
        _make(reg)
    assert exc.value.category.value == "resource_exhausted"


def test_delete_removes_stream():
    reg = StreamRegistry()
    stream = _make(reg)
    reg.delete(stream.stream_id)
    with pytest.raises(StreamNotFoundError):
        reg.get(stream.stream_id)
