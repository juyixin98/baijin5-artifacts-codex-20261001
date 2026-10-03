"""Stream registry: owns filter instances, parameter versions, limits.

A *stream* is one stateful cascade (fixed channel count). Coefficient
updates bump ``param_version``; callers may pin a block or an update to an
expected version, and a mismatch is a state conflict, not a silent apply.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field

import numpy as np

from app.config import Settings
from app.dsp.cascade import (
    InitialCondition,
    SosCascadeFilter,
    TransientStrategy,
)
from app.dsp.coefficients import NormalizedSos, normalize_and_validate
from app.errors import (
    ParamVersionConflictError,
    ResourceLimitError,
    StreamNotFoundError,
)


@dataclass
class StreamState:
    stream_id: str
    sample_rate: float
    num_channels: int
    param_version: int
    sos: NormalizedSos
    filt: SosCascadeFilter
    samples_processed: int = 0
    blocks_processed: int = 0


class StreamStore:
    """Thread-safe registry of active streams."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._streams: dict[str, StreamState] = {}
        self._lock = threading.Lock()

    def create_stream(
        self,
        *,
        sample_rate: float,
        num_channels: int,
        sections: list[list[float]],
        initial_condition: InitialCondition,
    ) -> StreamState:
        if num_channels > self._settings.max_channels:
            raise ResourceLimitError(
                f"num_channels {num_channels} > limit {self._settings.max_channels}",
                context={"limit": self._settings.max_channels},
            )
        sos = normalize_and_validate(
            sections,
            max_sections=self._settings.max_sections,
            pole_radius_limit=self._settings.pole_radius_limit,
        )
        with self._lock:
            if len(self._streams) >= self._settings.max_streams:
                raise ResourceLimitError(
                    f"stream count limit {self._settings.max_streams} reached",
                    context={"limit": self._settings.max_streams},
                )
            stream_id = uuid.uuid4().hex
            state = StreamState(
                stream_id=stream_id,
                sample_rate=sample_rate,
                num_channels=num_channels,
                param_version=1,
                sos=sos,
                filt=SosCascadeFilter(sos.sections, num_channels, initial_condition),
            )
            self._streams[stream_id] = state
            return state

    def get(self, stream_id: str) -> StreamState:
        try:
            return self._streams[stream_id]
        except KeyError:
            raise StreamNotFoundError(
                f"unknown stream id {stream_id!r}",
                context={"stream_id": stream_id},
            ) from None

    def delete(self, stream_id: str) -> None:
        with self._lock:
            self.get(stream_id)
            del self._streams[stream_id]

    @staticmethod
    def _check_version(stream: StreamState, expected: int | None) -> None:
        if expected is not None and expected != stream.param_version:
            raise ParamVersionConflictError(
                f"caller pinned param_version {expected} but stream "
                f"{stream.stream_id} is at {stream.param_version}",
                context={
                    "stream_id": stream.stream_id,
                    "expected": expected,
                    "actual": stream.param_version,
                },
            )

    def process_block(
        self,
        stream_id: str,
        block: np.ndarray,
        *,
        expected_version: int | None,
    ) -> tuple[StreamState, np.ndarray]:
        stream = self.get(stream_id)
        with self._lock:
            self._check_version(stream, expected_version)
            if block.shape[0] > self._settings.max_block_samples:
                raise ResourceLimitError(
                    f"block length {block.shape[0]} > limit "
                    f"{self._settings.max_block_samples}",
                    context={"limit": self._settings.max_block_samples},
                )
            out = stream.filt.process_block(block)
            stream.samples_processed += int(block.shape[0])
            stream.blocks_processed += 1
            return stream, out

    def update_coefficients(
        self,
        stream_id: str,
        sections: list[list[float]],
        *,
        expected_version: int,
        transient: TransientStrategy,
    ) -> StreamState:
        stream = self.get(stream_id)
        sos = normalize_and_validate(
            sections,
            max_sections=self._settings.max_sections,
            pole_radius_limit=self._settings.pole_radius_limit,
        )
        with self._lock:
            self._check_version(stream, expected_version)
            stream.filt.replace_coefficients(sos.sections, transient)
            stream.sos = sos
            stream.param_version += 1
            return stream
