"""Network builder: compiles rules into a shared alpha/beta network.

Sharing:
  * Alpha memories are shared across rules when constant tests are identical.
  * Join nodes (and their output beta memories) are shared when the parent
    memory, alpha memory and full test set are identical — so two rules with
    a common condition prefix share that prefix's matches.

When a new join node is attached below a memory that already holds tokens
(e.g. the root, or a prefix shared with an earlier rule), the existing
tokens are replayed through it exactly once; new WMEs arriving later are
handled by the normal right-activation path.
"""

from __future__ import annotations

from .agenda import Agenda
from .alpha import AlphaNetwork
from .beta import (BEQ, FEQ, GTEST, BetaMemory, JoinNode, ProductionNode,
                   Token)
from .errors import DuplicateRuleError
from .pattern import Condition, Rule, Test, is_var


class ReteNetwork:
    def __init__(self, agenda: Agenda):
        self.alpha = AlphaNetwork()
        self.agenda = agenda
        self.root = BetaMemory()
        # The dummy top token: the seed every match chain grows from.
        self.root_token = Token(self.root, None, None, {}, ())
        self.root.tokens[self.root_token.id] = self.root_token
        self.join_index: dict[tuple, JoinNode] = {}
        self.productions: dict[str, ProductionNode] = {}

    # -- public ------------------------------------------------------------
    def add_rule(self, rule: Rule) -> ProductionNode:
        if rule.name in self.productions:
            raise DuplicateRuleError(rule.name)
        memory = self._build_beta_chain(rule)
        prod = ProductionNode(rule, self.agenda.add, self.agenda.remove)
        memory.children.append(prod)
        # Replay tokens that already exist in a shared final memory.
        for tok in list(memory.tokens.values()):
            prod.left_activate(tok)
        self.productions[rule.name] = prod
        return prod

    # -- construction ------------------------------------------------------
    def _build_beta_chain(self, rule: Rule) -> BetaMemory:
        memory: BetaMemory = self.root
        earlier_vars: set = set()
        pending_tests: list[Test] = list(rule.tests)
        for cond in rule.conditions:
            alpha = self.alpha.get_or_create(
                cond.kind, len(cond.fields), cond.constant_tests)
            tests, var_positions = self._condition_tests(
                cond, earlier_vars, pending_tests)
            key = (memory.id, alpha.key, tests)
            join = self.join_index.get(key)
            if join is None:
                join = JoinNode(memory, alpha, tests)
                join.var_positions = var_positions
                self.join_index[key] = join
                # Replay pre-existing parent tokens through the new node.
                for tok in list(memory.tokens.values()):
                    join.left_activate(tok)
            memory = join.output
            earlier_vars |= set(cond.variables)
        return memory

    @staticmethod
    def _condition_tests(cond: Condition, earlier_vars: set,
                         pending_tests: list) -> tuple[tuple, dict[int, str]]:
        """Build the join tests for one condition.

        Returns (tests, var_positions) and mutates ``pending_tests`` by
        removing the general tests that become fully bound at this point.
        """
        tests: list = []
        var_positions: dict[int, str] = {}
        seen_here: dict[str, int] = {}
        for pos, f in enumerate(cond.fields):
            if not is_var(f):
                continue
            if f in earlier_vars:
                tests.append((BEQ, pos, f))
            elif f in seen_here:
                tests.append((FEQ, seen_here[f], pos))
            else:
                seen_here[f] = pos
                var_positions[pos] = f
        bound_now = earlier_vars | set(cond.variables)
        still_pending = []
        for t in pending_tests:
            if all(v in bound_now for v in t.variables):
                tests.append((GTEST, t))
            else:
                still_pending.append(t)
        pending_tests[:] = still_pending
        return tuple(tests), var_positions
