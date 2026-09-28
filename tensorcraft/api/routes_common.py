"""Shared helpers for API route modules."""

from __future__ import annotations

from typing import Any

from ..errors import InvalidIndexError


def decode_index(raw: list[Any]):
    """Decode the wire index form into a Python indexer tuple."""
    entries: list[Any] = []
    for entry in raw:
        if isinstance(entry, str):
            if entry == "newaxis":
                entries.append(None)
            elif entry == "ellipsis":
                entries.append(Ellipsis)
            else:
                raise InvalidIndexError(
                    f"unsupported index token {entry!r}; use a "
                    "[start, stop, step] list, int, 'newaxis' or 'ellipsis'")
        elif isinstance(entry, bool):
            raise InvalidIndexError("boolean scalar index is not supported")
        elif isinstance(entry, int):
            entries.append(entry)
        elif isinstance(entry, list):
            if len(entry) != 3:
                raise InvalidIndexError(
                    f"slice entry must be [start, stop, step], got {entry!r}")
            entries.append(tuple_slice(entry))
        else:
            raise InvalidIndexError(f"unsupported index entry {entry!r}")
    return tuple(entries)


def tuple_slice(entry: list[Any]) -> slice:
    return slice(entry[0], entry[1], entry[2])
