"""Multi-channel state isolation and store-level error categories."""

from __future__ import annotations

import numpy as np
import pytest

from app.config import Settings
from app.errors import ResourceExhaustedError, StateConflictError
from app.state.channels import ChannelStore


def test_channels_are_fully_isolated() -> None:
    store = ChannelStore()
    a = store.create(algorithm="nlms", filter_len=4, mu=0.5)
    b = store.create(algorithm="nlms", filter_len=4, mu=0.5)

    rng = np.random.default_rng(0)
    a.filter.process_block(rng.standard_normal(256), rng.standard_normal(256))

    assert np.linalg.norm(a.filter.weights) > 0.0
    # Channel b must be untouched: zero weights, zero buffer, zero count.
    np.testing.assert_array_equal(b.filter.weights, np.zeros(4))
    np.testing.assert_array_equal(b.filter.buffer, np.zeros(4))
    assert b.samples_processed == 0


def test_same_input_same_result_across_channels() -> None:
    store = ChannelStore()
    a = store.create(algorithm="nlms", filter_len=4, mu=0.5)
    b = store.create(algorithm="nlms", filter_len=4, mu=0.5)
    rng = np.random.default_rng(4)
    ref, pri = rng.standard_normal(128), rng.standard_normal(128)

    ra = a.filter.process_block(ref, pri)
    rb = b.filter.process_block(ref, pri)
    np.testing.assert_array_equal(ra.error, rb.error)
    np.testing.assert_array_equal(a.filter.weights, b.filter.weights)


def test_duplicate_channel_id_is_state_conflict() -> None:
    store = ChannelStore()
    store.create(algorithm="lms", filter_len=2, mu=0.1, channel_id="ch-1")
    with pytest.raises(StateConflictError):
        store.create(algorithm="lms", filter_len=2, mu=0.1, channel_id="ch-1")


def test_unknown_channel_lookup_and_delete_are_state_conflict() -> None:
    store = ChannelStore()
    with pytest.raises(StateConflictError):
        store.get("missing")
    with pytest.raises(StateConflictError):
        store.delete("missing")
    channel = store.create(algorithm="lms", filter_len=2, mu=0.1)
    store.delete(channel.channel_id)
    with pytest.raises(StateConflictError):
        store.get(channel.channel_id)  # deleted channels stay conflicting


def test_channel_capacity_is_resource_exhaustion() -> None:
    store = ChannelStore(Settings(max_channels=2))
    store.create(algorithm="lms", filter_len=2, mu=0.1)
    store.create(algorithm="lms", filter_len=2, mu=0.1)
    with pytest.raises(ResourceExhaustedError):
        store.create(algorithm="lms", filter_len=2, mu=0.1)
    # Error categories stay distinct: capacity is NOT a state conflict.
    assert len(store) == 2
