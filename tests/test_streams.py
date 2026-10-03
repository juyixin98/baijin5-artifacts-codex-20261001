"""Stream registry: multi-channel isolation, ordering, freeze intervals."""

from __future__ import annotations

import numpy as np
import pytest

from app.algorithms.lms import FilterSpec
from app.config import Settings
from app.errors import ComputationError, ResourceExhaustedError, StateConflictError
from app.streams.session import StreamRegistry

SPEC = FilterSpec(algorithm="nlms", filter_length=8, mu=0.5, epsilon=1e-8)


def registry(**overrides) -> StreamRegistry:
    return StreamRegistry(Settings(**overrides))


class TestLifecycle:
    def test_duplicate_stream_rejected(self):
        reg = registry()
        reg.create_stream("s1", SPEC, ["a"])
        with pytest.raises(StateConflictError) as exc:
            reg.create_stream("s1", SPEC, ["a"])
        assert exc.value.reason == "stream_exists"

    def test_stream_capacity(self):
        reg = registry(max_streams=2)
        reg.create_stream("s1", SPEC, ["a"])
        reg.create_stream("s2", SPEC, ["a"])
        with pytest.raises(ResourceExhaustedError) as exc:
            reg.create_stream("s3", SPEC, ["a"])
        assert exc.value.reason == "too_many_streams"

    def test_channel_capacity(self):
        reg = registry(max_channels_per_stream=2)
        with pytest.raises(ResourceExhaustedError) as exc:
            reg.create_stream("s1", SPEC, ["a", "b", "c"])
        assert exc.value.reason == "too_many_channels"

    def test_unknown_stream_and_channel(self):
        reg = registry()
        reg.create_stream("s1", SPEC, ["a"])
        with pytest.raises(StateConflictError) as exc:
            reg.process_block("nope", "a", 0, np.zeros(4), np.zeros(4))
        assert exc.value.reason == "unknown_stream"
        assert exc.value.http_status == 404
        with pytest.raises(StateConflictError) as exc:
            reg.process_block("s1", "nope", 0, np.zeros(4), np.zeros(4))
        assert exc.value.reason == "unknown_channel"


class TestOrdering:
    def test_index_gap_is_state_conflict_and_preserves_state(self):
        reg = registry()
        reg.create_stream("s1", SPEC, ["a"])
        reg.process_block("s1", "a", 0, np.zeros(10), np.zeros(10))
        with pytest.raises(StateConflictError) as exc:
            reg.process_block("s1", "a", 12, np.zeros(4), np.zeros(4))
        assert exc.value.reason == "index_mismatch"
        assert exc.value.detail["expected"] == 10
        assert exc.value.detail["got"] == 12
        # State untouched: the correct next block is still accepted.
        reg.process_block("s1", "a", 10, np.zeros(4), np.zeros(4))
        assert reg.channel_snapshot("s1", "a")["next_index"] == 14

    def test_block_length_limit(self):
        reg = registry(max_block_length=8)
        reg.create_stream("s1", SPEC, ["a"])
        with pytest.raises(ResourceExhaustedError) as exc:
            reg.process_block("s1", "a", 0, np.zeros(9), np.zeros(9))
        assert exc.value.reason == "block_too_long"


class TestChannelIsolation:
    def test_channels_do_not_share_state(self):
        reg = registry()
        reg.create_stream("s1", SPEC, ["a", "b"])
        rng = np.random.default_rng(1)
        xs_a, ds_a = rng.standard_normal(64), rng.standard_normal(64)
        xs_b, ds_b = rng.standard_normal(64), rng.standard_normal(64)
        reg.process_block("s1", "a", 0, xs_a, ds_a)
        reg.process_block("s1", "b", 0, xs_b, ds_b)
        snap_a = reg.channel_snapshot("s1", "a")
        snap_b = reg.channel_snapshot("s1", "b")
        assert snap_a["weights"] != snap_b["weights"]

        # Each channel must match a standalone filter fed the same data.
        solo = registry()
        solo.create_stream("solo", SPEC, ["a"])
        solo.process_block("solo", "a", 0, xs_a, ds_a)
        np.testing.assert_allclose(
            snap_a["weights"], solo.channel_snapshot("solo", "a")["weights"],
            atol=1e-15,
        )

    def test_streams_do_not_share_state(self):
        reg = registry()
        reg.create_stream("s1", SPEC, ["a"])
        reg.create_stream("s2", SPEC, ["a"])
        reg.process_block("s1", "a", 0, np.ones(16), np.ones(16))
        assert reg.channel_snapshot("s2", "a")["next_index"] == 0
        np.testing.assert_array_equal(
            reg.channel_snapshot("s2", "a")["weights"], np.zeros(8)
        )


class TestFreezeIntervals:
    def test_frozen_until_index_blocks_adaptation(self):
        reg = registry()
        reg.create_stream("s1", SPEC, ["a"], frozen_until_index=10)
        rng = np.random.default_rng(2)
        xs, ds = rng.standard_normal(20), rng.standard_normal(20)
        result = reg.process_block("s1", "a", 0, xs, ds)
        assert result.frozen_samples == 10
        # Frozen prefix: weights unchanged for the first 10 samples, then
        # adaptation starts — final weights must equal a run where the same
        # filter only adapts from sample 10 on.
        solo = registry()
        solo.create_stream("solo", SPEC, ["a"])
        solo.process_block("solo", "a", 0, xs[:10], ds[:10], freeze_adaptation=True)
        solo.process_block("solo", "a", 10, xs[10:], ds[10:])
        np.testing.assert_allclose(
            reg.channel_snapshot("s1", "a")["weights"],
            solo.channel_snapshot("solo", "a")["weights"],
            atol=1e-15,
        )

    def test_freeze_adaptation_flag_freezes_whole_block(self):
        reg = registry()
        reg.create_stream("s1", SPEC, ["a"])
        rng = np.random.default_rng(3)
        result = reg.process_block(
            "s1", "a", 0, rng.standard_normal(16), rng.standard_normal(16),
            freeze_adaptation=True,
        )
        assert result.frozen_samples == 16
        np.testing.assert_array_equal(
            reg.channel_snapshot("s1", "a")["weights"], np.zeros(8)
        )
        # But the buffer still advanced.
        assert reg.channel_snapshot("s1", "a")["next_index"] == 16


class TestFailureRecovery:
    def test_computation_failure_allows_retry_at_same_index(self):
        reg = registry()
        spec = FilterSpec(algorithm="lms", filter_length=4, mu=1.0, epsilon=1e-8)
        reg.create_stream("s1", spec, ["a"])
        with np.errstate(over="ignore", invalid="ignore"):
            with pytest.raises(ComputationError):
                reg.process_block("s1", "a", 0, np.full(32, 1e200), np.ones(32))
        # Rolled back: index 0 is still expected and a sane block succeeds.
        reg.process_block("s1", "a", 0, np.ones(8), np.ones(8))
        assert reg.channel_snapshot("s1", "a")["next_index"] == 8
