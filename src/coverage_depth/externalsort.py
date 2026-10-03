"""External merge sort spilling to SQLite.

Inputs larger than `chunk_size` records are sorted in bounded memory:
each in-memory chunk is sorted, written as one sorted run into a temporary
SQLite database, and the runs are k-way merged with a heap. SQLite gives us
durable spill files and indexed ORDER BY scans; temp databases are removed
when the merged iterator is exhausted or closed.

Row layout per run table: (k1..k4, payload). The projected sort key (up to
4 components, all str/int) is stored in typed columns so SQLite's ORDER BY
matches Python's tuple ordering; payload is the JSON-serialized record.
"""

from __future__ import annotations

import heapq
import itertools
import json
import os
import sqlite3
import tempfile
from collections.abc import Callable, Iterable, Iterator
from typing import Any, TypeVar

T = TypeVar("T")

_KEY_COLUMNS = 4


def _chunked(items: Iterable[T], size: int) -> Iterator[list[T]]:
    chunk: list[T] = []
    for item in items:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def _pad(key: tuple) -> tuple:
    if not 1 <= len(key) <= _KEY_COLUMNS:
        raise ValueError(f"sort keys must have 1..{_KEY_COLUMNS} components")
    # Pad with a value that sorts before any real str/int component.
    return key + ("",) * (_KEY_COLUMNS - len(key))


def external_sort(
    items: Iterable[T],
    key: Callable[[T], tuple],
    chunk_size: int,
    spill_dir: str | None = None,
    to_jsonable: Callable[[T], Any] = lambda x: x,
    from_jsonable: Callable[[Any], T] = lambda x: x,
) -> Iterator[T]:
    """Sort `items` by `key`, spilling sorted runs to SQLite when needed.

    Inputs of at most one chunk are sorted purely in memory (no I/O).
    """
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")

    chunks = iter(_chunked(items, chunk_size))
    first = next(chunks, None)
    if first is None:
        return iter(())

    second = next(chunks, None)
    if second is None:
        return iter(sorted(first, key=key))  # fast path: single chunk

    return _spilled_sort(
        itertools.chain([first, second], chunks), key, spill_dir,
        to_jsonable, from_jsonable,
    )


def _spilled_sort(
    chunks: Iterable[list[T]],
    key: Callable[[T], tuple],
    spill_dir: str | None,
    to_jsonable: Callable[[T], Any],
    from_jsonable: Callable[[Any], T],
) -> Iterator[T]:
    spill_dir = spill_dir or tempfile.mkdtemp(prefix="coverage_sort_")
    os.makedirs(spill_dir, exist_ok=True)
    db_path = os.path.join(spill_dir, f"runs_{os.getpid()}_{id(chunks):x}.db")
    conn = sqlite3.connect(db_path)
    n_runs = 0
    try:
        for chunk in chunks:
            table = f"run_{n_runs}"
            conn.execute(
                f'CREATE TABLE "{table}" (k1, k2, k3, k4, payload TEXT NOT NULL)'
            )
            rows = [
                (*_pad(key(item)), json.dumps(to_jsonable(item)))
                for item in sorted(chunk, key=key)
            ]
            conn.executemany(f'INSERT INTO "{table}" VALUES (?,?,?,?,?)', rows)
            conn.execute(
                f'CREATE INDEX "idx_{table}" ON "{table}" (k1, k2, k3, k4)'
            )
            n_runs += 1
        conn.commit()

        def run_iterator(table: str) -> Iterator[tuple[tuple, Any]]:
            cursor = conn.cursor()
            cursor.execute(
                f'SELECT k1, k2, k3, k4, payload FROM "{table}" '
                "ORDER BY k1, k2, k3, k4"
            )
            for k1, k2, k3, k4, payload in cursor:
                yield (k1, k2, k3, k4), payload
            cursor.close()

        def merged() -> Iterator[T]:
            try:
                streams = [run_iterator(f"run_{i}") for i in range(n_runs)]
                for _, payload in heapq.merge(*streams, key=lambda kp: kp[0]):
                    yield from_jsonable(json.loads(payload))
            finally:
                conn.close()
                for suffix in ("", "-journal", "-wal", "-shm"):
                    try:
                        os.remove(db_path + suffix)
                    except FileNotFoundError:
                        pass

        return merged()
    except BaseException:
        conn.close()
        raise
