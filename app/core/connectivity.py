"""Fixed neighbourhood definitions for the flood.

Connectivity is part of the algorithm contract: only 4- (von Neumann) and
8-connectivity (Moore) are supported, and the offset tables below are the
single source of truth used by both the kernel and the tests.
"""

from __future__ import annotations

# (dr, dc) offsets. Row axis first, column axis second.
CONNECTIVITY_OFFSETS: dict[int, tuple[tuple[int, int], ...]] = {
    4: ((-1, 0), (1, 0), (0, -1), (0, 1)),
    8: (
        (-1, -1), (-1, 0), (-1, 1),
        (0, -1),           (0, 1),
        (1, -1),  (1, 0),  (1, 1),
    ),
}

SUPPORTED_CONNECTIVITIES: tuple[int, ...] = tuple(sorted(CONNECTIVITY_OFFSETS))


def neighbor_offsets(connectivity: int) -> tuple[tuple[int, int], ...]:
    """Return the fixed offset table for ``connectivity``.

    Raises:
        ValueError: if the connectivity is not one of the supported values.
    """
    try:
        return CONNECTIVITY_OFFSETS[int(connectivity)]
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(
            f"unsupported connectivity {connectivity!r}; "
            f"supported: {SUPPORTED_CONNECTIVITIES}"
        ) from exc


def iter_neighbors(
    row: int, col: int, n_rows: int, n_cols: int, connectivity: int
):
    """Yield in-bounds (row, col) neighbours of a pixel, in fixed table order."""
    for dr, dc in neighbor_offsets(connectivity):
        r, c = row + dr, col + dc
        if 0 <= r < n_rows and 0 <= c < n_cols:
            yield r, c
