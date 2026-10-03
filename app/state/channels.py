"""Multi-channel streaming state.

Each channel owns an independent ``AdaptiveFilter`` instance; the store
never shares buffers or weights between channels, so processing one
channel cannot leak state into another. Capacity limits raise
``ResourceExhaustedError``; lookups of unknown or deleted channels raise
``StateConflictError`` — the two are deliberately distinct categories.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from app.config import DEFAULT_SETTINGS, Settings
from app.dsp.lms import AdaptiveFilter
from app.errors import ResourceExhaustedError, StateConflictError


@dataclass
class Channel:
    channel_id: str
    filter: AdaptiveFilter
    created_at: float = field(default_factory=time.time)

    @property
    def samples_processed(self) -> int:
        return self.filter.samples_processed


class ChannelStore:
    def __init__(self, settings: Settings = DEFAULT_SETTINGS) -> None:
        self._settings = settings
        self._channels: dict[str, Channel] = {}

    def create(
        self,
        *,
        algorithm: str,
        filter_len: int,
        mu: float,
        eps: float = DEFAULT_SETTINGS.eps_default,
        channel_id: str | None = None,
    ) -> Channel:
        if len(self._channels) >= self._settings.max_channels:
            raise ResourceExhaustedError(
                "channel capacity reached",
                detail={"channels": len(self._channels), "max": self._settings.max_channels},
            )
        channel_id = channel_id or uuid.uuid4().hex[:16]
        if channel_id in self._channels:
            raise StateConflictError(
                "channel id already exists",
                detail={"channel_id": channel_id},
            )
        adaptive_filter = AdaptiveFilter(
            filter_len=filter_len,
            algorithm=algorithm,
            mu=mu,
            eps=eps,
            settings=self._settings,
        )
        channel = Channel(channel_id=channel_id, filter=adaptive_filter)
        self._channels[channel_id] = channel
        return channel

    def get(self, channel_id: str) -> Channel:
        try:
            return self._channels[channel_id]
        except KeyError:
            raise StateConflictError(
                "channel does not exist (never created or already deleted)",
                detail={"channel_id": channel_id},
            ) from None

    def delete(self, channel_id: str) -> None:
        channel = self.get(channel_id)  # raises StateConflictError if absent
        del self._channels[channel.channel_id]

    def __len__(self) -> int:
        return len(self._channels)
