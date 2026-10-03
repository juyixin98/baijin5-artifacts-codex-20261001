"""s-t graph construction (Kolmogorov & Zabih style).

Node layout: pixels are nodes ``0 .. N-1`` (row-major), node ``N`` is the
source, node ``N+1`` is the sink.  A pixel on the *source* side of the cut
takes label 1 (foreground), on the *sink* side label 0 (background).

Each pairwise term V with (v00, v01, v10, v11), submodular with
``k = v01 + v10 - v00 - v11 >= 0``, decomposes exactly as

    V(x_p, x_q) = v00
                  + (v10 - v00 - k/2) * x_p
                  + (v01 - v00 - k/2) * x_q
                  + (k/2) * [x_p != x_q]

so the builder adds two directed arcs p->q and q->p of capacity k/2 each,
folds the linear parts into per-pixel unaries, and accumulates v00 into a
global constant.  Per-pixel unaries (u0, u1) become t-links:

    t = u1 - u0
    t >= 0: arc p -> sink with capacity t,   constant += u0
    t <  0: arc source -> p with capacity -t, constant += u1

All arc capacities are non-negative by construction.

Hard seeds use a *computed* big-M, never a magic infinity:
``M = 1 + sum(all non-seed arc capacities)`` is strictly larger than any
cut that respects the seeds, so the minimum cut can never sever a seed arc.
The builder verifies ``M <= 2**headroom`` so float64 arithmetic stays exact
enough for the certificate; otherwise it raises RESOURCE_EXHAUSTED /
CAPACITY_OVERFLOW instead of silently overflowing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterator

import numpy as np

from .config import Settings
from .contracts import BACKGROUND, FOREGROUND, SegmentationSpec, neighbor_pairs
from .errors import ResourceExhaustedError


@dataclass(frozen=True)
class STGraph:
    num_pixels: int
    source: int
    sink: int
    edges_from: np.ndarray  # int64, shape (E,)
    edges_to: np.ndarray    # int64, shape (E,)
    edges_cap: np.ndarray   # float64, shape (E,), all >= 0
    constant: float         # additive constant folded into the graph
    big_m: float            # capacity used for hard-seed arcs (0 if no seeds)
    num_seed_arcs: int

    @property
    def num_nodes(self) -> int:
        return self.num_pixels + 2

    @property
    def num_edges(self) -> int:
        return int(self.edges_from.shape[0])


def _pairwise_rows(spec: SegmentationSpec,
                   row_range: range) -> Iterator[tuple[int, int, float, float, float]]:
    """Yield (p, q, unary1_delta_p, unary1_delta_q, arc_capacity) per edge."""
    pw = spec.pairwise
    k = pw.v01 + pw.v10 - pw.v00 - pw.v11  # >= 0, checked at validation
    half_k = 0.5 * k
    width = spec.width
    for p, q in neighbor_pairs(spec.height, spec.width):
        # neighbor_pairs emits each undirected edge once with p as the
        # left/upper pixel, so keying on p's row yields it exactly once.
        if p // width not in row_range:
            continue
        yield (p, q,
               pw.v10 - pw.v00 - half_k,
               pw.v01 - pw.v00 - half_k,
               half_k)


def build_st_graph(
    spec: SegmentationSpec,
    *,
    settings: Settings | None = None,
    chunk_rows: int | None = None,
    on_chunk: Callable[[int, int], None] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> STGraph:
    """Build the s-t graph, processing pixel rows in cooperative chunks.

    ``on_chunk(chunks_done, chunks_total)`` is invoked after each chunk;
    ``should_cancel()`` is polled between chunks and aborts the build by
    raising :class:`BuildCancelled`.
    """
    settings = settings or Settings()
    rows_per_chunk = chunk_rows or settings.chunk_rows
    height, width = spec.height, spec.width
    n = spec.num_pixels

    unary1 = spec.unary1.copy()
    frm: list[int] = []
    to: list[int] = []
    cap: list[float] = []
    constant = 0.0

    chunks_total = max(1, (height + rows_per_chunk - 1) // rows_per_chunk)
    chunks_done = 0
    for start in range(0, height, rows_per_chunk):
        if should_cancel is not None and should_cancel():
            raise BuildCancelled(f"graph build cancelled after {chunks_done} chunks")
        row_range = range(start, min(start + rows_per_chunk, height))
        for p, q, du1_p, du1_q, arc_cap in _pairwise_rows(spec, row_range):
            constant += spec.pairwise.v00
            unary1.ravel()[p] += du1_p
            unary1.ravel()[q] += du1_q
            if arc_cap > 0.0:
                frm.append(p); to.append(q); cap.append(arc_cap)
                frm.append(q); to.append(p); cap.append(arc_cap)
        chunks_done += 1
        if on_chunk is not None:
            on_chunk(chunks_done, chunks_total)

    # t-links from combined unaries.
    u0 = spec.unary0.ravel()
    u1 = unary1.ravel()
    for p in range(n):
        t = float(u1[p] - u0[p])
        if t >= 0.0:
            if t > 0.0:
                frm.append(p); to.append(n + 1); cap.append(t)
            constant += float(u0[p])
        else:
            frm.append(n); to.append(p); cap.append(-t)
            constant += float(u1[p])

    # Big-M for hard seeds: strictly larger than any seed-respecting cut.
    big_m = float(sum(cap)) + 1.0
    headroom_limit = float(2 ** settings.big_m_headroom)
    if not np.isfinite(big_m) or big_m > headroom_limit:
        raise ResourceExhaustedError(
            f"hard-seed capacity M={big_m!r} exceeds safe limit {headroom_limit!r}; "
            "refusing to build a graph whose seed constraints could overflow",
            code="CAPACITY_OVERFLOW",
            details={"big_m": big_m, "limit": headroom_limit},
        )

    num_seed_arcs = 0
    for seed in spec.seeds:
        p = seed.row * width + seed.col
        if seed.label == FOREGROUND:
            frm.append(n); to.append(p); cap.append(big_m)
        else:
            frm.append(p); to.append(n + 1); cap.append(big_m)
        num_seed_arcs += 1

    return STGraph(
        num_pixels=n,
        source=n,
        sink=n + 1,
        edges_from=np.asarray(frm, dtype=np.int64),
        edges_to=np.asarray(to, dtype=np.int64),
        edges_cap=np.asarray(cap, dtype=np.float64),
        constant=constant,
        big_m=big_m if num_seed_arcs else 0.0,
        num_seed_arcs=num_seed_arcs,
    )


class BuildCancelled(Exception):
    """Internal control-flow signal: the owning job was cancelled."""
