"""Registry of live training sessions."""

from __future__ import annotations

import itertools
import threading

from ..errors import ConfigError, StateError
from .trainer import LinearSGDTrainer


class TrainerStore:
    def __init__(self, capacity: int) -> None:
        if not isinstance(capacity, int) or capacity <= 0:
            raise ConfigError(f"capacity must be a positive int, got {capacity!r}")
        self._capacity = capacity
        self._trainers: dict[str, LinearSGDTrainer] = {}
        self._counter = itertools.count(1)
        self._lock = threading.Lock()

    def put(self, trainer: LinearSGDTrainer) -> str:
        if not isinstance(trainer, LinearSGDTrainer):
            raise TypeError("TrainerStore only holds LinearSGDTrainer instances")
        with self._lock:
            if len(self._trainers) >= self._capacity:
                raise StateError(
                    f"trainer store full ({self._capacity} sessions)")
            handle = f"s{next(self._counter)}"
            while handle in self._trainers:
                handle = f"s{next(self._counter)}"
            self._trainers[handle] = trainer
            return handle

    def get(self, handle: str) -> LinearSGDTrainer:
        try:
            return self._trainers[handle]
        except KeyError:
            raise StateError(f"unknown training session {handle!r}",
                             details={"handle": handle,
                                      "not_found": True}) from None

    def has(self, handle: str) -> bool:
        return handle in self._trainers

    def delete(self, handle: str) -> None:
        if handle not in self._trainers:
            raise StateError(f"cannot delete unknown session {handle!r}",
                             details={"handle": handle})
        del self._trainers[handle]

    def list_handles(self) -> list[str]:
        return sorted(self._trainers)

    def __len__(self) -> int:
        return len(self._trainers)
