"""Stream registry: lifecycle, parameter versioning, transient policies.

Each stream owns one ``SOSCascadeFilter`` plus a monotonically increasing
``param_version`` (starts at 1, incremented on every accepted coefficient
update). Callers may pass ``expected_param_version`` on chunk and update
requests; a mismatch raises ``VersionConflictError`` (STATE_CONFLICT), which
makes lost-update races between a controller retuning a filter and a worker
streaming audio detectable instead of silent.

Transient policy on coefficient switch (set at creation, overridable per
update):
- ``carry``: keep DF2T delay states. No output discontinuity from state
  loss, but the old state is interpreted under new coefficients, so a short
  transient is expected. If the section count changes, states cannot be
  carried and are zeroed (documented, deterministic fallback).
- ``reset``: zero all delay states at the switch. Deterministic restart;
  the output may jump at the boundary.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum

from app.config import MAX_STREAMS
from app.dsp.coefficients import NormalizedSOS
from app.dsp.filter import SOSCascadeFilter
from app.errors import ResourceLimitError, StreamNotFoundError, VersionConflictError


class TransientPolicy(str, Enum):
    CARRY = "carry"
    RESET = "reset"


@dataclass
class Stream:
    stream_id: str
    n_channels: int
    sos: NormalizedSOS
    transient: TransientPolicy
    filter: SOSCascadeFilter
    param_version: int = 1
    samples_processed: int = 0
    chunks_processed: int = 0
    sample_rate: float | None = None


@dataclass
class StreamRegistry:
    """Thread-safe in-memory registry of active streams."""

    max_streams: int = MAX_STREAMS
    _streams: dict[str, Stream] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def create(
        self,
        sos: NormalizedSOS,
        n_channels: int,
        transient: TransientPolicy,
        sample_rate: float | None = None,
    ) -> Stream:
        with self._lock:
            if len(self._streams) >= self.max_streams:
                raise ResourceLimitError(
                    f"stream limit reached: {self.max_streams}",
                    detail={"limit": self.max_streams},
                )
            stream = Stream(
                stream_id=uuid.uuid4().hex,
                n_channels=n_channels,
                sos=sos,
                transient=transient,
                filter=SOSCascadeFilter(sos, n_channels),
                sample_rate=sample_rate,
            )
            self._streams[stream.stream_id] = stream
            return stream

    def get(self, stream_id: str) -> Stream:
        try:
            return self._streams[stream_id]
        except KeyError:
            raise StreamNotFoundError(
                f"unknown stream id: {stream_id}",
                detail={"stream_id": stream_id},
            ) from None

    def check_version(self, stream: Stream, expected: int | None) -> None:
        if expected is not None and expected != stream.param_version:
            raise VersionConflictError(
                f"param version conflict: stream is at {stream.param_version}, "
                f"caller expected {expected}",
                detail={
                    "stream_id": stream.stream_id,
                    "current_version": stream.param_version,
                    "expected_version": expected,
                },
            )

    def update_coefficients(
        self,
        stream_id: str,
        sos: NormalizedSOS,
        transient: TransientPolicy | None,
        expected_version: int | None,
    ) -> Stream:
        with self._lock:
            stream = self.get(stream_id)
            self.check_version(stream, expected_version)
            policy = transient if transient is not None else stream.transient
            stream.filter.replace_coefficients(
                sos, keep_state=policy is TransientPolicy.CARRY
            )
            stream.sos = sos
            stream.transient = policy
            stream.param_version += 1
            return stream

    def reset(self, stream_id: str) -> Stream:
        with self._lock:
            stream = self.get(stream_id)
            stream.filter.reset()
            return stream

    def delete(self, stream_id: str) -> None:
        with self._lock:
            self.get(stream_id)
            del self._streams[stream_id]
