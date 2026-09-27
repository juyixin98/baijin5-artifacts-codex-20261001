"""Beta network: tokens, beta memories, join nodes, production nodes.

Indexing (per the design contract, beta memory is explicitly indexed):
  * ``BetaMemory.tokens`` — token id -> Token.
  * ``BetaMemory.indexes`` — for each child join node, an index
    ``join_vars -> {key_tuple -> {token_id}}`` where the key is the tuple of
    the token's bindings for exactly the variables the child joins on.
    A right activation therefore touches only tokens whose bindings match
    the new WME's join values, never a full scan.
  * Retraction is back-pointer driven: each WME lists the tokens it takes
    part in; deleting a token recursively deletes its descendants and any
    agenda activation produced from them.
"""

from __future__ import annotations

import itertools
from typing import Callable

from .facts import WME
from .pattern import Rule, Test

# Join-test encodings (kept as plain tuples so nodes stay inspectable):
#   ("beq", pos, var)   wme.fields[pos] == token.bindings[var]   (indexed)
#   ("feq", pos_a, pos_b)  wme.fields[pos_a] == wme.fields[pos_b]
#   ("gtest", Test)     general comparison over merged bindings
BEQ, FEQ, GTEST = "beq", "feq", "gtest"

_token_ids = itertools.count(1)
_node_ids = itertools.count(1)


class Token:
    """A partial (or complete) match: a chain of WMEs plus merged bindings."""

    __slots__ = ("id", "node", "parent", "wme", "bindings", "wme_ids",
                 "children")

    def __init__(self, node, parent: "Token | None", wme: WME | None,
                 bindings: dict, wme_ids: tuple):
        self.id = next(_token_ids)
        self.node = node            # BetaMemory or ProductionNode holding it
        self.parent = parent
        self.wme = wme
        self.bindings = bindings
        self.wme_ids = wme_ids      # fact ids in condition order (provenance)
        self.children: list[Token] = []

    def __repr__(self):
        return f"<Token #{self.id} wmes={self.wme_ids}>"


class BetaMemory:
    def __init__(self):
        self.id = next(_node_ids)
        self.tokens: dict[int, Token] = {}
        self.indexes: dict[tuple, dict] = {}   # join_vars -> {key: {token_id}}
        self.children: list = []               # JoinNode | ProductionNode
        # The join whose output this memory is (None for the root); used to
        # clean the join's pair-dedup set on token deletion.
        self.parent_join = None

    # -- index maintenance -------------------------------------------------
    def ensure_index(self, join_vars: tuple) -> None:
        if join_vars in self.indexes:
            return
        idx: dict = {}
        for tok in self.tokens.values():
            key = tuple(tok.bindings[v] for v in join_vars)
            idx.setdefault(key, set()).add(tok.id)
        self.indexes[join_vars] = idx

    def lookup(self, join_vars: tuple, key: tuple) -> list[Token]:
        ids = self.indexes[join_vars].get(key, ())
        return [self.tokens[i] for i in ids if i in self.tokens]

    # -- token lifecycle ---------------------------------------------------
    def add_token(self, parent: Token | None, wme: WME | None,
                  bindings: dict, wme_ids: tuple) -> Token:
        tok = Token(self, parent, wme, bindings, wme_ids)
        self.tokens[tok.id] = tok
        for join_vars, idx in self.indexes.items():
            key = tuple(bindings[v] for v in join_vars)
            idx.setdefault(key, set()).add(tok.id)
        if parent is not None:
            parent.children.append(tok)
        if wme is not None:
            wme.tokens.append(tok)
        for child in list(self.children):
            child.left_activate(tok)
        return tok

    def remove_token(self, tok: Token) -> None:
        del self.tokens[tok.id]
        for join_vars, idx in self.indexes.items():
            key = tuple(tok.bindings[v] for v in join_vars)
            bucket = idx.get(key)
            if bucket is not None:
                bucket.discard(tok.id)
                if not bucket:
                    del idx[key]


class ProductionNode:
    """Terminal node: complete matches for one rule -> agenda activations."""

    def __init__(self, rule: Rule,
                 on_add_activation: Callable[[Rule, "Token"], None],
                 on_remove_activation: Callable[[Rule, "Token"], None]):
        self.id = next(_node_ids)
        self.rule = rule
        self.tokens: dict[int, Token] = {}
        self._on_add = on_add_activation
        self._on_remove = on_remove_activation

    def left_activate(self, parent: Token) -> Token:
        """A token reached the final memory: record a complete match and
        push an activation onto the agenda."""
        tok = Token(self, parent, None, parent.bindings, parent.wme_ids)
        self.tokens[tok.id] = tok
        parent.children.append(tok)
        self._on_add(self.rule, tok)
        return tok

    def remove_token(self, tok: Token) -> None:
        del self.tokens[tok.id]
        self._on_remove(self.rule, tok)


def delete_token(tok: Token) -> None:
    """Remove a token, its descendants, and any dependent activations.

    Idempotent: a WME can occupy several positions in one match chain (a
    fact satisfying more than one condition), so retraction can reach a
    token more than once; detached tokens are skipped."""
    if tok.id not in tok.node.tokens:
        return
    node = tok.node
    if isinstance(node, BetaMemory):
        node.remove_token(tok)
        if node.parent_join is not None and tok.parent is not None \
                and tok.wme is not None:
            node.parent_join.pairs.discard((tok.parent.id, tok.wme.id))
    else:  # ProductionNode
        node.remove_token(tok)
    for child in list(tok.children):
        delete_token(child)
    if tok.parent is not None:
        tok.parent.children.remove(tok)
    if tok.wme is not None:
        try:
            tok.wme.tokens.remove(tok)
        except ValueError:
            pass


class JoinNode:
    """Joins a parent beta memory with an alpha memory under join tests."""

    def __init__(self, parent: BetaMemory, alpha_memory, tests: tuple):
        self.id = next(_node_ids)
        self.parent: BetaMemory = parent
        self.alpha = alpha_memory
        self.tests: tuple = tuple(tests)
        self.output = BetaMemory()
        self.output.parent_join = self
        # A (parent-token, wme) pair can be discovered twice while a single
        # new fact propagates (once via left activation, once via right),
        # notably when the same WME satisfies several conditions of one
        # rule. The pair set makes each output token exactly-once.
        self.pairs: set[tuple[int, int]] = set()
        # Indexed join variables, ordered by wme position for a stable key.
        beq = sorted((t[1], t[2]) for t in self.tests if t[0] == BEQ)
        self.beq_positions = tuple(p for p, _ in beq)
        self.join_vars = tuple(v for _, v in beq)
        # position -> variable for this join's condition (variables only;
        # constants never bind). Set by the network builder.
        self.var_positions: dict[int, str] = {}
        parent.ensure_index(self.join_vars)
        parent.children.append(self)
        alpha_memory.successors.append(self)

    @property
    def key(self) -> tuple:
        return (self.parent.id, self.alpha.id, self.tests)

    # -- test evaluation ---------------------------------------------------
    def _wme_self_tests_ok(self, wme: WME) -> bool:
        return all(wme.fields[a] == wme.fields[b]
                   for t in self.tests if t[0] == FEQ
                   for a, b in [(t[1], t[2])])

    def _general_tests_ok(self, bindings: dict) -> bool:
        try:
            return all(t[1].evaluate(bindings)
                       for t in self.tests if t[0] == GTEST)
        except TypeError:
            # Unorderable operand types: the comparison cannot hold.
            return False

    # -- activations -------------------------------------------------------
    def left_activate(self, token: Token) -> None:
        """New token in the parent memory: find matching WMEs via the
        alpha memory's (position, value) index — no full memory scan."""
        key = tuple(token.bindings[v] for v in self.join_vars)
        if self.beq_positions:
            candidate_ids = None
            for pos, val in zip(self.beq_positions, key):
                bucket = self.alpha.candidates(pos, val)
                candidate_ids = (set(bucket) if candidate_ids is None
                                 else candidate_ids & bucket)
                if not candidate_ids:
                    return
        else:
            candidate_ids = set(self.alpha.items)
        for wme_id in candidate_ids:
            wme = self.alpha.items.get(wme_id)
            if wme is not None:
                self._try_join(token, wme)

    def right_activate(self, wme: WME) -> None:
        """New WME in the alpha memory: find matching tokens via the parent
        beta memory's join-variable index — no full token scan."""
        if not self._wme_self_tests_ok(wme):
            return
        key = tuple(wme.fields[p] for p in self.beq_positions)
        for token in self.parent.lookup(self.join_vars, key):
            self._try_join(token, wme)

    def _try_join(self, token: Token, wme: WME) -> None:
        pair = (token.id, wme.id)
        if pair in self.pairs:
            return
        merged = self._merge_bindings(token, wme)
        if merged is None:
            return
        if not self._general_tests_ok(merged):
            return
        self.pairs.add(pair)
        self.output.add_token(token, wme, merged,
                              token.wme_ids + (wme.id,))

    def _merge_bindings(self, token: Token, wme: WME) -> dict | None:
        """Merge the WME's fields into the token bindings; None on conflict
        (defensive — beq/feq tests already guarantee consistency)."""
        merged = dict(token.bindings)
        for pos, val in enumerate(wme.fields):
            var = self._var_at(pos)
            if var is None:
                continue
            if var in merged:
                if merged[var] != val:
                    return None
            else:
                merged[var] = val
        return merged

    def _var_at(self, pos: int):
        return self.var_positions.get(pos)
