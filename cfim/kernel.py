"""Closed frequent itemset mining kernel.

Algorithm
---------
A prefix DFS over vertical transaction-id (tid) sets with an explicit closure
test at every node:

* The database is represented vertically: for every item, a sorted tuple of
  tids of transactions containing it. The tidset ``T(I)`` of an itemset is
  the intersection of its members' tidsets, computed by merge scans.
* The DFS enumerates *every frequent itemset exactly once*. A node is a
  prefix ``I = (i1 < ... < ik)``; children extend it with ``j > ik`` such
  that ``|T(I) ∩ T(j)| >= min_support``. Prefix ordering makes duplicate
  enumeration structurally impossible (there is exactly one sorted path to
  each itemset).
* **Closure test (rule 3, closed vs maximal):** at node ``I`` we compute
  ``cl(I) = { j : T(j) ⊇ T(I) }`` by sorted-tidset containment. ``I`` is
  *closed* iff ``cl(I) == I``, i.e. no absent item appears in every
  transaction that contains ``I``. It is *maximal* iff no closed frequent
  strict superset exists -- a strictly stronger property, derived after
  completion and never conflated with closedness.
  Itemsets with the same support as a containing set are exactly the
  non-closed ones (``I ⊊ cl(I)`` and ``|T(I)| == |T(cl(I))|``); the
  exhaustive tests pin this behavior down.
* **Enumeration budget (rule 4):** entering one frequent-itemset node costs
  one budget unit (one closure test + child list build). When the budget is
  spent the DFS suspends: the parent frame keeps ``position`` pointing at
  the not-yet-entered child, so resuming redoes nothing but an intersection
  and never reports a node twice. Partial results are valid closed itemsets.

Scope notes:
* The empty itemset is not mined (standard convention). Empty transactions
  still count toward the transaction total and therefore the threshold
  boundary; they simply contain no itemset.
* The kernel is pure and persistence-free; the store serializes its state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

from cfim.config import BUDGET_UNIT, HARD_ENUMERATION_CEILING, KERNEL_VERSION
from cfim.errors import DomainError, ErrorCode

# State dump format version emitted by this module.
STATE_VERSION = 1


@dataclass(frozen=True)
class ItemsetResult:
    """One closed frequent itemset.

    Attributes:
        itemset: items in canonical (sorted) order.
        support: number of distinct transactions containing the itemset.
        maximal: True iff no strict frequent superset exists. Only finalized
            once the whole search has completed; always False on partial
            results of a still-running job.
    """

    itemset: tuple[str, ...]
    support: int
    maximal: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "itemset": list(self.itemset),
            "support": self.support,
            "maximal": self.maximal,
        }


@dataclass(frozen=True)
class VerticalDatabase:
    """Vertical representation of one corpus.

    Attributes:
        items: item labels in fixed canonical (sorted) order.
        tidsets: ``tidsets[i]`` is the sorted tuple of tids containing
            ``items[i]``.
        transaction_count: total number of transactions, empty ones included.
    """

    items: tuple[str, ...]
    tidsets: tuple[tuple[int, ...], ...]
    transaction_count: int

    @property
    def item_count(self) -> int:
        return len(self.items)


def build_vertical_database(
    items: Iterable[str],
    transactions: Iterable[Iterable[str]],
) -> VerticalDatabase:
    """Build tidsets from normalized transactions.

    Tids are 1-based positions (``tid = transaction index + 1``). Transactions
    are expected to be de-duplicated within themselves by the corpus layer;
    a ``set`` guard here keeps the kernel independently correct.
    """
    ordered_items = tuple(sorted(items))
    index = {item: i for i, item in enumerate(ordered_items)}
    buckets: list[set[int]] = [set() for _ in ordered_items]
    count = 0
    for position, tx in enumerate(transactions):
        count += 1
        tid = position + 1
        for item in set(tx):
            buckets[index[item]].add(tid)
    return VerticalDatabase(
        items=ordered_items,
        tidsets=tuple(tuple(sorted(b)) for b in buckets),
        transaction_count=count,
    )


def intersect_sorted(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[int, ...]:
    """Intersection of two ascending tid tuples via a merge scan."""
    i = j = 0
    out: list[int] = []
    la, lb = len(a), len(b)
    while i < la and j < lb:
        x, y = a[i], b[j]
        if x == y:
            out.append(x)
            i += 1
            j += 1
        elif x < y:
            i += 1
        else:
            j += 1
    return tuple(out)


def _covers(container: tuple[int, ...], contained: tuple[int, ...]) -> bool:
    """Return True iff every tid in ``contained`` occurs in ``container``.

    Both arguments are ascending; a single merge scan decides containment.
    """
    if len(container) < len(contained):
        return False
    i = j = 0
    n = len(contained)
    while i < n:
        target = contained[i]
        while j < len(container) and container[j] < target:
            j += 1
        if j >= len(container) or container[j] != target:
            return False
        i += 1
    return True


def validate_budget(budget: object) -> int:
    """Validate an enumeration budget: a positive integer of node visits."""
    if isinstance(budget, bool) or not isinstance(budget, int):
        raise DomainError(
            ErrorCode.BUDGET_INVALID,
            "budget must be a positive integer number of DFS node visits",
            {"received_type": type(budget).__name__},
        )
    if budget < 1:
        raise DomainError(
            ErrorCode.BUDGET_INVALID,
            "budget must be at least 1",
            {"budget": budget},
        )
    if budget > HARD_ENUMERATION_CEILING:
        raise DomainError(
            ErrorCode.BUDGET_INVALID,
            f"budget {budget} exceeds hard ceiling {HARD_ENUMERATION_CEILING}",
            {"budget": budget, "ceiling": HARD_ENUMERATION_CEILING},
        )
    return budget


@dataclass
class _Frame:
    """Continuation frame for one prefix-DFS node.

    Attributes:
        prefix: item indices fixed on the path to this node; ``()`` is the
            free virtual root whose entry costs no budget.
        tidset: tidset of the prefix itemset (unused for the root).
        children: candidate extension item indices, all pre-filtered to lead
            to frequent children.
        position: index into ``children`` of the next child to enter.
    """

    prefix: tuple[int, ...]
    tidset: tuple[int, ...]
    children: tuple[int, ...]
    position: int


@dataclass
class MiningState:
    """Resumable state of one mining job.

    ``results`` holds every closed itemset found so far in deterministic DFS
    pre-order. While ``completed`` is False the ``maximal`` flags are False:
    maximality can only be judged against the complete closed result.
    """

    db_items: tuple[str, ...]
    tidsets: tuple[tuple[int, ...], ...]
    min_support: int
    transaction_count: int
    results: list[ItemsetResult] = field(default_factory=list)
    nodes_visited: int = 0
    total_budget_used: int = 0
    completed: bool = False
    _stack: list[_Frame] = field(default_factory=list)

    # -- (de)serialization -------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialize to plain JSON-compatible data."""
        return {
            "state_version": STATE_VERSION,
            "kernel_version": KERNEL_VERSION,
            "db_items": list(self.db_items),
            "tidsets": [list(t) for t in self.tidsets],
            "min_support": self.min_support,
            "transaction_count": self.transaction_count,
            "results": [
                {
                    "itemset": list(r.itemset),
                    "support": r.support,
                    "maximal": r.maximal,
                }
                for r in self.results
            ],
            "nodes_visited": self.nodes_visited,
            "total_budget_used": self.total_budget_used,
            "completed": self.completed,
            "stack": [
                {
                    "prefix": list(fr.prefix),
                    "tidset": list(fr.tidset),
                    "children": list(fr.children),
                    "position": fr.position,
                }
                for fr in self._stack
            ],
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> "MiningState":
        """Deserialize state, rejecting incompatible dump versions."""
        if data.get("state_version") != STATE_VERSION:
            raise DomainError(
                ErrorCode.STATE_VERSION_MISMATCH,
                f"state format {data.get('state_version')!r} is not supported; "
                f"this kernel reads version {STATE_VERSION}",
                {"received": data.get("state_version"), "expected": STATE_VERSION},
            )
        state = cls(
            db_items=tuple(data["db_items"]),
            tidsets=tuple(tuple(t) for t in data["tidsets"]),
            min_support=int(data["min_support"]),
            transaction_count=int(data["transaction_count"]),
            results=[
                ItemsetResult(
                    itemset=tuple(r["itemset"]),
                    support=int(r["support"]),
                    maximal=bool(r.get("maximal", False)),
                )
                for r in data["results"]
            ],
            nodes_visited=int(data["nodes_visited"]),
            total_budget_used=int(data["total_budget_used"]),
            completed=bool(data["completed"]),
        )
        state._stack = [
            _Frame(
                prefix=tuple(f["prefix"]),
                tidset=tuple(f["tidset"]),
                children=tuple(f["children"]),
                position=int(f["position"]),
            )
            for f in data.get("stack", [])
        ]
        return state


def initial_state(db: VerticalDatabase, min_support: int) -> MiningState:
    """Create the initial state: a free virtual root seeded with frequent 1-items.

    Infrequent singleton items cannot occur in any frequent itemset (tidsets
    only shrink under intersection), so they are excluded from the root's
    child list up front.
    """
    state = MiningState(
        db_items=db.items,
        tidsets=db.tidsets,
        min_support=min_support,
        transaction_count=db.transaction_count,
    )
    seeds = tuple(i for i, tids in enumerate(db.tidsets) if len(tids) >= min_support)
    state._stack.append(
        _Frame(prefix=(), tidset=(), children=seeds, position=0)
    )
    return state


def _frequent_children(
    state: MiningState, prefix: tuple[int, ...], tidset: tuple[int, ...]
) -> tuple[int, ...]:
    """All j > prefix[-1] whose intersection with ``tidset`` stays frequent."""
    tidsets = state.tidsets
    min_support = state.min_support
    start = prefix[-1] + 1 if prefix else 0
    children: list[int] = []
    for j in range(start, len(tidsets)):
        column = tidsets[j]
        if prefix:
            child_tid = intersect_sorted(tidset, column)
        else:
            child_tid = column
        if len(child_tid) >= min_support:
            children.append(j)
    return tuple(children)


def _enter_node(state: MiningState, prefix: tuple[int, ...], tidset: tuple[int, ...]) -> None:
    """Bookkeeping for entering one frequent-itemset node.

    Runs the closure test, appends the result exactly when the node is
    closed, and pushes its continuation frame.
    """
    tidsets = state.tidsets
    closure: set[int] = set()
    for j, column in enumerate(tidsets):
        if _covers(column, tidset):
            closure.add(j)
    if closure == set(prefix):
        state.results.append(
            ItemsetResult(
                itemset=tuple(state.db_items[i] for i in prefix),
                support=len(tidset),
            )
        )
    children = _frequent_children(state, prefix, tidset)
    state._stack.append(
        _Frame(prefix=prefix, tidset=tidset, children=children, position=0)
    )


def advance(state: MiningState, budget: int) -> MiningState:
    """Run at most ``budget`` further node entries.

    Newly found closed itemsets are appended to ``state.results`` and the
    counters advance. On full exhaustion the state is marked ``completed``
    and maximal flags are finalized; calling ``advance`` again afterwards is
    a no-op that spends no budget. Suspension guarantees: when the budget
    runs out exactly as a child is about to be entered, the parent frame's
    ``position`` still points at that child, so resuming neither skips nor
    repeats a node.
    """
    validate_budget(budget)
    if state.completed:
        return state

    used = 0
    while state._stack and used < budget:
        frame = state._stack[-1]
        if frame.position >= len(frame.children):
            state._stack.pop()
            continue

        j = frame.children[frame.position]
        # Compute the child tidset before consuming the slot: if the budget
        # ends here the untouched position makes the resume idempotent.
        if frame.prefix:
            child_tid = intersect_sorted(frame.tidset, state.tidsets[j])
        else:
            child_tid = state.tidsets[j]
        if len(child_tid) < state.min_support:
            # Defensive: children are pre-filtered; skip rather than enter.
            frame.position += 1
            continue

        frame.position += 1
        used += 1
        state.nodes_visited += 1
        state.total_budget_used += 1
        _enter_node(state, frame.prefix + (j,), child_tid)

    if not state._stack:
        state.completed = True
        _mark_maximal(state)
    return state


def _mark_maximal(state: MiningState) -> None:
    """Finalize maximal flags over the complete closed result (rule 3).

    Among closed frequent itemsets, ``I`` is maximal iff no other closed
    frequent itemset strictly contains it. Any frequent strict superset
    would contain a closed frequent strict superset (its own closure), so
    checking only closed sets is exact.
    """
    as_sets = [frozenset(r.itemset) for r in state.results]
    flagged: list[ItemsetResult] = []
    for i, result in enumerate(state.results):
        own = as_sets[i]
        is_maximal = not any(i != j and own < other for j, other in enumerate(as_sets))
        flagged.append(
            ItemsetResult(itemset=result.itemset, support=result.support, maximal=is_maximal)
        )
    state.results = flagged


def run_to_completion(db: VerticalDatabase, min_support: int) -> list[ItemsetResult]:
    """Convenience wrapper: mine synchronously up to the hard ceiling.

    Intended for tests and the local demo, not for the HTTP API.
    """
    state = initial_state(db, min_support)
    advance(state, HARD_ENUMERATION_CEILING)
    return state.results


def budget_unit() -> str:
    """Human description of one budget unit, for logs and responses."""
    return BUDGET_UNIT
