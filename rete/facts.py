"""Working memory: facts (WMEs) with explicit duplicate semantics.

Duplicate semantics (documented, tested):
  * A fact is identified by its content key ``(kind, fields)``.
  * Inserting an identical fact does NOT create a second WME; it increments
    the reference count of the existing one and returns the same ``wme_id``.
  * ``retract`` decrements the count; the WME (and every match depending on
    it) is removed only when the count reaches zero.
  * ``wme_id`` values are monotonically increasing and never reused, so an
    activation's identity ``(rule, wme_id tuple)`` is stable and unambiguous.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field

from .errors import FactNotFoundError
from .pattern import ALLOWED_SCALARS


@dataclass(eq=False)
class WME:
    """A working-memory element. Identity is ``id``; content is the key."""

    id: int
    kind: str
    fields: tuple
    count: int = 1
    # Back-pointers maintained by the network (used on retraction):
    alpha_memories: list = field(default_factory=list)
    tokens: list = field(default_factory=list)

    @property
    def key(self) -> tuple:
        return (self.kind, self.fields)


class FactStore:
    """Content-addressed fact table with reference counting."""

    def __init__(self):
        self._ids = itertools.count(1)
        self._by_key: dict[tuple, WME] = {}
        self._by_id: dict[int, WME] = {}

    def _validate(self, kind: str, fields: tuple) -> tuple:
        if not isinstance(kind, str) or not kind:
            raise ValueError("fact kind must be a non-empty string")
        fields = tuple(fields)
        for f in fields:
            if not isinstance(f, ALLOWED_SCALARS):
                raise ValueError(f"unsupported fact field value {f!r}")
        return fields

    def insert(self, kind: str, fields: tuple) -> tuple[WME, bool]:
        """Insert one occurrence. Returns (wme, created)."""
        fields = self._validate(kind, fields)
        key = (kind, fields)
        wme = self._by_key.get(key)
        if wme is not None:
            wme.count += 1
            return wme, False
        wme = WME(id=next(self._ids), kind=kind, fields=fields)
        self._by_key[key] = wme
        self._by_id[wme.id] = wme
        return wme, True

    def retract(self, kind: str, fields: tuple) -> tuple[WME, bool]:
        """Retract one occurrence. Returns (wme, removed_from_memory)."""
        fields = tuple(fields)
        key = (kind, fields)
        wme = self._by_key.get(key)
        if wme is None:
            raise FactNotFoundError(kind, fields)
        wme.count -= 1
        if wme.count > 0:
            return wme, False
        del self._by_key[key]
        del self._by_id[wme.id]
        return wme, True

    def get(self, wme_id: int) -> WME | None:
        return self._by_id.get(wme_id)

    def lookup(self, kind: str, fields: tuple) -> WME | None:
        return self._by_key.get((kind, tuple(fields)))

    def distinct_facts(self) -> list[WME]:
        return list(self._by_id.values())

    def __len__(self) -> int:
        return len(self._by_id)
