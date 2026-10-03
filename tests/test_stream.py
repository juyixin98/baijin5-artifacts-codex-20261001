"""Stream session state machine tests."""

import numpy as np
import pytest

from fir_backend.contracts import EstimateParams
from fir_backend.errors import (
    ErrorCategory,
    ResourceExhaustedError,
    StateConflictError,
)
from fir_backend.fixtures import make_fixture
from fir_backend.stream import StreamRegistry, StreamSession, StreamState


def _session(max_samples=10_000):
    return StreamSession(stream_id="test-stream", max_samples=max_samples)


def test_full_open_seal_estimate_flow_recovers_coefficients():
    fixture = make_fixture("clean", n_samples=256)
    session = _session()
    half = fixture.excitation.size // 2
    session.append(fixture.excitation[:half], fixture.response[:half])
    session.append(fixture.excitation[half:], fixture.response[half:])
    assert session.state is StreamState.OPEN
    session.seal()
    assert session.state is StreamState.SEALED
    result = session.estimate(EstimateParams(model_order=8))
    assert session.state is StreamState.ESTIMATED
    np.testing.assert_allclose(
        result.coefficients_array(), fixture.true_coefficients, atol=1e-8
    )


def test_append_after_seal_is_state_conflict():
    session = _session()
    session.append(np.ones(16), np.ones(16))
    session.seal()
    with pytest.raises(StateConflictError) as excinfo:
        session.append(np.ones(4), np.ones(4))
    assert excinfo.value.category is ErrorCategory.STATE


def test_double_seal_is_state_conflict():
    session = _session()
    session.append(np.ones(16), np.ones(16))
    session.seal()
    with pytest.raises(StateConflictError):
        session.seal()


def test_estimate_before_seal_is_state_conflict():
    session = _session()
    session.append(np.ones(16), np.ones(16))
    with pytest.raises(StateConflictError):
        session.estimate(EstimateParams(model_order=2))


def test_seal_empty_stream_is_state_conflict():
    session = _session()
    with pytest.raises(StateConflictError):
        session.seal()


def test_abort_is_terminal():
    session = _session()
    session.append(np.ones(16), np.ones(16))
    session.abort()
    with pytest.raises(StateConflictError):
        session.append(np.ones(4), np.ones(4))
    with pytest.raises(StateConflictError):
        session.abort()


def test_sample_budget_exceeded_is_resource_error():
    session = _session(max_samples=32)
    session.append(np.ones(20), np.ones(20))
    with pytest.raises(ResourceExhaustedError) as excinfo:
        session.append(np.ones(20), np.ones(20))
    assert excinfo.value.category is ErrorCategory.RESOURCE
    # The failed append must not corrupt the accumulated state.
    assert session.n_samples == 20


def test_block_contract_still_enforced_inside_stream():
    session = _session()
    from fir_backend.errors import InputValidationError

    with pytest.raises(InputValidationError):
        session.append(np.ones(8), np.ones(9))


def test_registry_limits_live_sessions():
    registry = StreamRegistry(max_sessions=2)
    registry.create()
    registry.create()
    with pytest.raises(ResourceExhaustedError):
        registry.create()


def test_registry_get_returns_none_for_unknown_id():
    registry = StreamRegistry()
    assert registry.get("no-such-stream") is None
