"""Stream registry: per-session, per-channel filter state.

Isolation contract: each (stream_id, channel_id) pair owns an independent
``AdaptiveFilter``; no buffers or weights are ever shared across channels.

Ordering contract: blocks must arrive with ``start_index`` equal to the
channel's next expected sample index. A gap or overlap is a
``state_conflict`` (reason ``index_mismatch``); the state is not modified.

Freeze contract: samples with absolute index < ``frozen_until_index``
(configured per stream) are filtered but not adapted; a block may
additionally request ``freeze_adaptation`` for its whole span.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.algorithms.lms import AdaptiveFilter, BlockResult, FilterSpec
from app.config import Settings
from app.errors import ResourceExhaustedError, StateConflictError


@dataclass
class ChannelState:
    channel_id: str
    filter: AdaptiveFilter


@dataclass
class Stream:
    stream_id: str
    spec: FilterSpec
    frozen_until_index: int
    channels: dict[str, ChannelState]


class StreamRegistry:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._streams: dict[str, Stream] = {}

    # -- lifecycle ---------------------------------------------------------

    def create_stream(
        self,
        stream_id: str,
        spec: FilterSpec,
        channel_ids: list[str],
        frozen_until_index: int = 0,
    ) -> Stream:
        if stream_id in self._streams:
            raise StateConflictError(
                f"stream '{stream_id}' already exists",
                reason="stream_exists",
                detail={"stream_id": stream_id},
            )
        if len(self._streams) >= self._settings.max_streams:
            raise ResourceExhaustedError(
                "stream capacity reached",
                reason="too_many_streams",
                detail={"max_streams": self._settings.max_streams},
            )
        if not channel_ids or len(channel_ids) > self._settings.max_channels_per_stream:
            raise ResourceExhaustedError(
                "channel count out of range",
                reason="too_many_channels",
                detail={"channels": len(channel_ids),
                        "max": self._settings.max_channels_per_stream},
            )
        if len(set(channel_ids)) != len(channel_ids):
            raise StateConflictError(
                "duplicate channel ids in stream",
                reason="duplicate_channel",
                detail={"channels": channel_ids},
            )
        if frozen_until_index < 0:
            raise StateConflictError(
                "frozen_until_index must be >= 0",
                reason="invalid_freeze_interval",
                detail={"frozen_until_index": frozen_until_index},
            )
        stream = Stream(
            stream_id=stream_id,
            spec=spec,
            frozen_until_index=frozen_until_index,
            channels={
                ch: ChannelState(ch, AdaptiveFilter(spec, self._settings))
                for ch in channel_ids
            },
        )
        self._streams[stream_id] = stream
        return stream

    def drop_stream(self, stream_id: str) -> None:
        self._get_stream(stream_id)
        del self._streams[stream_id]

    # -- processing ----------------------------------------------------------

    def process_block(
        self,
        stream_id: str,
        channel_id: str,
        start_index: int,
        reference: np.ndarray,
        desired: np.ndarray,
        freeze_adaptation: bool = False,
    ) -> BlockResult:
        channel = self._get_channel(stream_id, channel_id)
        if len(reference) > self._settings.max_block_length:
            raise ResourceExhaustedError(
                "block exceeds max_block_length",
                reason="block_too_long",
                detail={"block_length": len(reference),
                        "max": self._settings.max_block_length},
            )
        expected = channel.filter.next_index
        if start_index != expected:
            raise StateConflictError(
                "block start_index does not match the channel's next index",
                reason="index_mismatch",
                detail={"expected": expected, "got": start_index,
                        "stream_id": stream_id, "channel_id": channel_id},
            )
        freeze_mask = self._build_freeze_mask(
            stream_id, start_index, len(reference), freeze_adaptation
        )
        return channel.filter.process_block(reference, desired, freeze_mask)

    def _build_freeze_mask(
        self, stream_id: str, start_index: int, length: int, freeze_all: bool
    ) -> np.ndarray:
        if freeze_all:
            return np.ones(length, dtype=bool)
        frozen_until = self._streams[stream_id].frozen_until_index
        mask = np.zeros(length, dtype=bool)
        frozen_count = min(length, max(0, frozen_until - start_index))
        mask[:frozen_count] = True
        return mask

    # -- introspection -------------------------------------------------------

    def channel_snapshot(self, stream_id: str, channel_id: str) -> dict:
        channel = self._get_channel(stream_id, channel_id)
        stream = self._streams[stream_id]
        return {
            "stream_id": stream_id,
            "channel_id": channel_id,
            "algorithm": stream.spec.algorithm,
            "filter_length": stream.spec.filter_length,
            "mu": stream.spec.mu,
            "epsilon": stream.spec.epsilon,
            "frozen_until_index": stream.frozen_until_index,
            "next_index": channel.filter.next_index,
            "weights": channel.filter.weights().tolist(),
            "buffer": channel.filter.buffer().tolist(),
            "weight_norm": float(np.linalg.norm(channel.filter.weights())),
        }

    # -- lookup helpers --------------------------------------------------------

    def _get_stream(self, stream_id: str) -> Stream:
        try:
            return self._streams[stream_id]
        except KeyError:
            raise StateConflictError(
                f"unknown stream '{stream_id}'",
                reason="unknown_stream",
                detail={"stream_id": stream_id},
                http_status=404,
            ) from None

    def _get_channel(self, stream_id: str, channel_id: str) -> ChannelState:
        stream = self._get_stream(stream_id)
        try:
            return stream.channels[channel_id]
        except KeyError:
            raise StateConflictError(
                f"unknown channel '{channel_id}' in stream '{stream_id}'",
                reason="unknown_channel",
                detail={"stream_id": stream_id, "channel_id": channel_id},
                http_status=404,
            ) from None
