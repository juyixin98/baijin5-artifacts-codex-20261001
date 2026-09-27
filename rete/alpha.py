"""Alpha network: constant tests and alpha memories.

Indexing (per the design contract, alpha memory is explicitly indexed):
  * ``AlphaNetwork.memories`` — one alpha memory per distinct
    ``(kind, constant-tests)`` key, so conditions with identical constant
    tests share a single memory across rules.
  * ``AlphaNetwork.kind_index`` — kind -> memories, so an incoming WME only
    visits memories of its own kind.
  * ``AlphaMemory.index`` — (position, value) -> set of wme ids, maintained
    incrementally; join nodes use it to fetch candidate WMEs by join value
    instead of scanning the whole memory.
"""

from __future__ import annotations

from .facts import WME


class AlphaMemory:
    _next_id = 0

    def __init__(self, kind: str, arity: int, const_tests: tuple):
        AlphaMemory._next_id += 1
        self.id = AlphaMemory._next_id
        self.kind = kind
        self.arity = arity
        self.const_tests = tuple(const_tests)  # ((pos, value), ...)
        self.items: dict[int, WME] = {}
        self.index: dict[tuple, set] = {}      # (pos, value) -> {wme_id}
        self.successors: list["JoinNode"] = []  # join nodes fed by this memory

    @property
    def key(self) -> tuple:
        return (self.kind, self.arity, self.const_tests)

    def matches(self, wme: WME) -> bool:
        return (len(wme.fields) == self.arity
                and all(wme.fields[pos] == val
                        for pos, val in self.const_tests))

    def add(self, wme: WME) -> None:
        self.items[wme.id] = wme
        wme.alpha_memories.append(self)
        for pos, val in enumerate(wme.fields):
            self.index.setdefault((pos, val), set()).add(wme.id)

    def remove(self, wme: WME) -> None:
        del self.items[wme.id]
        wme.alpha_memories.remove(self)
        for pos, val in enumerate(wme.fields):
            bucket = self.index.get((pos, val))
            if bucket is not None:
                bucket.discard(wme.id)
                if not bucket:
                    del self.index[(pos, val)]

    def candidates(self, pos: int, value: object) -> set[int]:
        """Indexed lookup used by join nodes."""
        return self.index.get((pos, value), set())

    def __len__(self) -> int:
        return len(self.items)


class AlphaNetwork:
    def __init__(self) -> None:
        self.memories: dict[tuple, AlphaMemory] = {}
        self.kind_index: dict[str, list[AlphaMemory]] = {}

    def get_or_create(self, kind: str, arity: int,
                      const_tests: tuple) -> AlphaMemory:
        key = (kind, arity, tuple(const_tests))
        mem = self.memories.get(key)
        if mem is None:
            mem = AlphaMemory(kind, arity, tuple(const_tests))
            self.memories[key] = mem
            self.kind_index.setdefault(kind, []).append(mem)
        return mem

    def propagate_new_wme(self, wme: WME) -> None:
        """Right-activate every join node under each matching memory."""
        for mem in self.kind_index.get(wme.kind, ()):
            if mem.matches(wme):
                mem.add(wme)
                for join in list(mem.successors):
                    join.right_activate(wme)

    def remove_wme(self, wme: WME) -> None:
        for mem in list(wme.alpha_memories):
            mem.remove(wme)
