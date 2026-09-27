"""Closed / maximal frequent itemset mining kernel.

Algorithm
---------
Vertical Eclat-style depth-first search over a bitset tid-index with an exact
Galois-closure test at every visited frequent itemset.

* A node is a pair ``(prefix, T)`` where ``prefix`` is a tuple of item indices
  and ``T`` is the tidset (integer bitset) of transactions containing every
  item in the prefix. Children extend the prefix with items later in the global
  item order and intersect tidsets with ``&``.
* An itemset ``X`` is **closed** iff it equals its closure
  ``{i : T(X) subseteq t(i)}``. The property is local to the node and the full
  (exact) item catalogue, so a closed itemset found inside a truncated run is
  still provably closed -- partial closed results are sound.
* A frequent itemset is **maximal** iff it has no frequent proper superset.
  Every maximal itemset is closed, and every frequent itemset is contained in a
  closed one, so maximals are derived as closed itemsets without a closed
  proper superset. This global property is only confirmed once the search is
  *complete*; on a truncated run maximals are reported as uncertain.

Budget / resume
---------------
The DFS uses explicit stack frames (``StackFrame``). Each candidate tidset
intersection costs one unit of ``budget``. When the budget is reached the whole
frame stack (including per-frame suffix cursors) can be serialised and later
restored, so enumeration continues exactly where it stopped with no duplicated
or skipped nodes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from .vertical_index import VerticalIndex

Itemset = frozenset[str]


@dataclass
class StackFrame:
    """A visited frequent node plus the cursor over its unprocessed suffix."""

    prefix: tuple[int, ...]
    tidset: int
    suffix: tuple[int, ...]
    next_k: int = 0

    def to_jsonable(self) -> dict:
        return {
            "prefix": list(self.prefix),
            "tidset": format(self.tidset, "x"),
            "suffix": list(self.suffix),
            "next_k": self.next_k,
        }

    @staticmethod
    def from_jsonable(data: dict) -> "StackFrame":
        return StackFrame(
            prefix=tuple(data["prefix"]),
            tidset=int(data["tidset"], 16),
            suffix=tuple(data["suffix"]),
            next_k=data["next_k"],
        )


@dataclass
class MineOutcome:
    """Result of one budget-bounded chunk of search."""

    closed_new: dict[Itemset, int]
    frames: list[StackFrame]
    evaluations_used: int
    complete: bool
    root: Itemset | None = field(default=None)
    root_support: int | None = field(default=None)
    root_closed: bool = False


def _closure_indices(index: VerticalIndex, tidset: int, candidates: Iterable[int]) -> frozenset[int]:
    """All item indices whose own tidset is a superset of ``tidset``."""
    return frozenset(i for i in candidates if index.tidsets[index.items[i]] & tidset == tidset)


def initialise_search(
    index: VerticalIndex, min_support: int
) -> tuple[list[StackFrame], MineOutcome | None]:
    """Create the root frame and evaluate the empty itemset.

    Returns the stack and a one-node outcome describing the root. When
    ``min_support`` exceeds the number of transactions even the empty itemset
    is infrequent: the stack is empty and the search is trivially complete.
    """
    if min_support < 1:
        raise ValueError("min_support must be a positive integer")
    n = index.transaction_count
    root_support = n  # empty itemset is contained in every transaction
    if root_support < min_support:
        outcome = MineOutcome(
            closed_new={},
            frames=[],
            evaluations_used=1,
            complete=True,
            root=frozenset(),
            root_support=root_support,
            root_closed=False,
        )
        return [], outcome

    root_tidset = (1 << n) - 1
    frequent_item_idxs = tuple(
        i for i, item in enumerate(index.items) if index.item_support(item) >= min_support
    )
    root_closure = _closure_indices(index, root_tidset, frequent_item_idxs)
    root_closed = len(root_closure) == 0

    root_closed_map: dict[Itemset, int] = {}
    if root_closed:
        root_closed_map[frozenset()] = root_support

    frame = StackFrame(prefix=(), tidset=root_tidset, suffix=frequent_item_idxs)
    outcome = MineOutcome(
        closed_new=root_closed_map,
        frames=[frame],
        evaluations_used=1,
        complete=False,
        root=frozenset(),
        root_support=root_support,
        root_closed=root_closed,
    )
    return [frame], outcome


def mine_chunk(
    index: VerticalIndex,
    min_support: int,
    budget: int,
    frames: list[StackFrame],
) -> MineOutcome:
    """Run at most ``budget`` candidate intersections starting from ``frames``.

    The frames are mutated in place (suffix cursors advance); pass the returned
    (same) frames to the next chunk to resume. Closed itemsets discovered in
    this chunk are returned in ``closed_new``. Enumeration is deterministic and
    visits every candidate itemset at most once.
    """
    if budget < 0:
        raise ValueError("budget must be a non-negative integer")
    if min_support < 1:
        raise ValueError("min_support must be a positive integer")

    frequent_item_idxs = tuple(
        i for i, item in enumerate(index.items) if index.item_support(item) >= min_support
    )

    closed_new: dict[Itemset, int] = {}
    used = 0
    complete = False

    while frames and used < budget:
        frame = frames[-1]
        if frame.next_k >= len(frame.suffix):
            frames.pop()
            continue

        k = frame.next_k
        j = frame.suffix[k]
        # Consume one budget unit *for* this evaluation; advance the cursor so
        # a checkpoint never re-evaluates this candidate.
        frame.next_k = k + 1
        used += 1

        child_tidset = frame.tidset & index.tidsets[index.items[j]]
        if child_tidset.bit_count() < min_support:
            continue

        child_prefix = frame.prefix + (j,)
        closure = _closure_indices(index, child_tidset, frequent_item_idxs)
        if set(child_prefix) == set(closure):
            itemset = frozenset(index.items[i] for i in child_prefix)
            # Closed itemsets are reached by exactly one combination, but keep
            # the guard so a duplicated emission would overwrite rather than
            # silently double-count.
            closed_new[itemset] = child_tidset.bit_count()

        # Suffix for the child: only items ordered after the one just added.
        frames.append(
            StackFrame(
                prefix=child_prefix,
                tidset=child_tidset,
                suffix=frame.suffix[k + 1 :],
            )
        )

    if not frames:
        complete = True

    return MineOutcome(
        closed_new=closed_new,
        frames=frames,
        evaluations_used=used,
        complete=complete,
    )


def derive_maximal(closed: dict[Itemset, int]) -> list[Itemset]:
    """Maximal frequent itemsets = closed itemsets without a closed superset."""
    itemsets = list(closed.keys())
    maximals: list[Itemset] = []
    for x in itemsets:
        has_superset = any(x < other for other in itemsets)
        if not has_superset:
            maximals.append(x)
    return maximals


def sort_itemsets(itemsets: Iterable[Itemset]) -> list[tuple[str, ...]]:
    """Deterministic human-facing order: size desc then lexicographic."""
    return sorted((tuple(sorted(x)) for x in itemsets), key=lambda x: (-len(x), x))
