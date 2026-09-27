"""独立预言机 (test oracle)。

本文件是对"接地 + 论证 + grounded 标签"的**第二份独立实现**:
* 不 import app.kernel 下的任何模块 (引擎/接地/解释均不使用);
* 仅借用规则语言的解析 AST (词法语法不属于被验收的推理语义);
* 数据结构、枚举方式、不动点循环均独立编写。

用途: 对枚举的小理论, 断言被测内核给出的状态与本预言机一致。
这样期望结论既来自人工 YAML, 也来自独立算法, 而不是被测核心自产自销。
"""
from __future__ import annotations

from app.rulelang.ast_nodes import Literal, RuleKind

# 命题化原子: (谓词, 参数元组, 强否定)
Atom = tuple[str, tuple, bool]
# 接地规则: (规则id, kind, 头, 正体集合, NAF体集合)
GRule = tuple[str, str, Atom, tuple[Atom, ...], tuple[Atom, ...]]


def _atom_of(lit: Literal) -> Atom:
    return (
        lit.predicate,
        tuple(value for _, value in lit.args),
        lit.negated,
    )


# --------------------------------------------------------------------------- #
# 独立接地器 (自底向上, 变量仅来自正体连接)
# --------------------------------------------------------------------------- #

def naive_ground(theory) -> tuple[set[Atom], set[Atom], list[GRule]]:
    """返回 (原始事实, 全部可达原子, 接地规则)。

    注意原始事实与可达原子必须分开: 推导出来的原子不是公理,
    不能作为无前提论证播种, 否则一切结论都会被错误地当成事实。
    """
    original_facts: set[Atom] = {_atom_of(f) for f in theory.facts}
    rules = theory.all_rules
    derived: set[Atom] = set(original_facts)
    grounded_rules: dict[tuple, GRule] = {}

    def substitutions_for(rule):
        pos = [b for b in rule.body if not b.naf]
        subs = [{}]
        for lit in pos:
            new_subs = []
            for (pred, row, neg) in derived:
                if pred != lit.predicate or neg != lit.negated or len(row) != lit.arity:
                    continue
                for sub in subs:
                    nxt = dict(sub)
                    ok = True
                    for term, value in zip(lit.args, row):
                        kind, name = term
                        if kind == "c":
                            if name != value:
                                ok = False
                                break
                        elif name in nxt and nxt[name] != value:
                            ok = False
                            break
                        else:
                            nxt[name] = value
                    if ok:
                        new_subs.append(nxt)
            subs = new_subs
            if not subs:
                break
        return subs

    def inst(lit, sub) -> Atom:
        row = tuple(
            value if kind == "c" else sub[value]
            for kind, value in lit.args
        )
        return (lit.predicate, row, lit.negated)

    changed = True
    while changed:
        changed = False
        for rule in rules:
            for sub in substitutions_for(rule):
                head = inst(rule.head, sub)
                pos_body = tuple(inst(b, sub) for b in rule.body if not b.naf)
                naf_body = tuple(inst(b, sub) for b in rule.body if b.naf)
                key = (rule.rule_id, head, pos_body, naf_body)
                if key not in grounded_rules:
                    grounded_rules[key] = (
                        rule.rule_id, rule.kind.value, head, pos_body, naf_body
                    )
                    changed = True
                if head not in derived:
                    derived.add(head)
                    changed = True
    return original_facts, derived, list(grounded_rules.values())


# --------------------------------------------------------------------------- #
# 独立论证枚举 (递归 + 无环守卫, 显式元组树)
# --------------------------------------------------------------------------- #

def build_derivations(original_facts: set[Atom], grules: list[GRule]):
    by_head: dict[Atom, list[GRule]] = {}
    for gr in grules:
        by_head.setdefault(gr[2], []).append(gr)

    # derivation = ("fact", atom) | ("app", rule_id, kind, head, children, assumptions)
    derivations: list[tuple] = []
    index: dict[Atom, list[int]] = {}
    sig_seen: set[tuple] = set()

    for atom in sorted(original_facts):
        d = ("fact", atom)
        if d not in sig_seen:
            sig_seen.add(d)
            derivations.append(d)
            index.setdefault(atom, []).append(len(derivations) - 1)

    def make_app(gr, child_ids, assumptions):
        rule_id, kind, head, _, naf_body = gr
        sig = ("app", rule_id, head, tuple(child_ids), tuple(sorted(assumptions)))
        if sig in sig_seen:
            return None
        sig_seen.add(sig)
        children = tuple(derivations[i] for i in child_ids)
        d = ("app", rule_id, kind, head, children, frozenset(assumptions))
        derivations.append(d)
        index.setdefault(head, []).append(len(derivations) - 1)
        return len(derivations) - 1

    changed = True
    while changed:
        changed = False
        before = len(derivations)
        for gr in grules:
            rule_id, kind, head, pos_body, naf_body = gr
            options = [index.get(a, []) for a in pos_body]
            if any(not o for o in options):
                continue

            def products(lists):
                if not lists:
                    yield ()
                    return
                for first in lists[0]:
                    for rest in products(lists[1:]):
                        yield (first, *rest)

            for combo in products(options):
                # 无环守卫: 同一规则实例不得在子树中重复
                if any(_uses_rule(derivations[i], (rule_id, head, pos_body, naf_body))
                       for i in combo):
                    continue
                assumptions = set(naf_body)
                for i in combo:
                    assumptions |= _assumptions(derivations[i])
                if make_app(gr, combo, assumptions) is not None:
                    changed = True
        if len(derivations) == before:
            break

    return derivations, index


def _assumptions(d) -> frozenset:
    if d[0] == "fact":
        return frozenset()
    return d[5]


def _head(d) -> Atom:
    return d[1] if d[0] == "fact" else d[3]


def _rule_id(d):
    return None if d[0] == "fact" else d[1]


def _kind(d):
    return "fact" if d[0] == "fact" else d[2]


def _sub_derivations(d) -> list[int]:
    # 仅用于攻击传播; 通过结构相等在全表定位
    if d[0] == "fact":
        return []
    return [_find_derivation_id(c) for c in d[4]]


_DERIVATION_TABLE = None  # 由 evaluate 设置, 供结构定位使用


def _find_derivation_id(target) -> int:
    for i, d in enumerate(_DERIVATION_TABLE):
        if d == target:
            return i
    raise KeyError


def _uses_rule(d, rule_instance) -> bool:
    rid, head, pos_body, naf_body = rule_instance
    if d[0] == "app":
        own = (d[1], d[3], tuple(_head(c) for c in d[4]),
               tuple(sorted(d[5] & set(naf_body))))
        if d[1] == rid and d[3] == head:
            # 同一规则同一头即视为同实例 (小理论下足够)
            child_heads = tuple(_head(c) for c in d[4])
            if child_heads == pos_body:
                return True
        for c in d[4]:
            if _uses_rule(c, rule_instance):
                return True
    return False


# --------------------------------------------------------------------------- #
# 独立攻击 + grounded 不动点
# --------------------------------------------------------------------------- #

def evaluate(theory, priority_edges: list[tuple[str, str]]):
    global _DERIVATION_TABLE
    original_facts, reachable, grules = naive_ground(theory)
    derivations, index = build_derivations(original_facts, grules)
    _DERIVATION_TABLE = derivations
    n = len(derivations)

    greater = transitive_closure(priority_edges)

    attacks: set[tuple[int, int]] = set()

    def compare(di, dj):
        ki, kj = _kind(di), _kind(dj)
        ri, rj = _rule_id(di), _rule_id(dj)
        if ri == rj:
            return 0
        if ki == "strict" and kj == "defeasible":
            return 1
        if ki == "defeasible" and kj == "strict":
            return -1
        # 事实按严格对待
        if ri is None and kj == "defeasible":
            return 1
        if rj is None and ki == "defeasible":
            return -1
        if rj in greater.get(ri, set()):
            return 1
        if ri in greater.get(rj, set()):
            return -1
        return 2  # 不可比较

    # 直接攻击
    direct: list[tuple[int, int]] = []
    for i, di in enumerate(derivations):
        # 假设攻击
        for assumed in _assumptions(di):
            for j in index.get(assumed, []):
                direct.append((j, i))
        # 反驳攻击
        pred, row, neg = _head(di)
        opposite = (pred, row, not neg)
        for j in index.get(opposite, []):
            cmp = compare(derivations[j], derivations[i])  # j 相对 i
            # cmp 表示 j 相对 i
            if cmp == 1:
                direct.append((j, i))
            elif cmp == -1:
                direct.append((i, j))
            elif cmp == 2:
                direct.append((j, i))
                direct.append((i, j))

    # 子论证传播
    parents: dict[int, set[int]] = {}
    for i, d in enumerate(derivations):
        if d[0] == "app":
            for child in d[4]:
                parents.setdefault(_find_derivation_id(child), set()).add(i)

    for a, t in list(direct):
        stack = [t]
        seen = set()
        while stack:
            cur = stack.pop()
            for p in parents.get(cur, ()):  # type: ignore[union-attr]
                if p not in seen:
                    seen.add(p)
                    stack.append(p)
        for p in seen:
            attacks.add((a, p))
        attacks.add((a, t))

    attacked_by: dict[int, set[int]] = {i: set() for i in range(n)}
    attacks_out: dict[int, set[int]] = {i: set() for i in range(n)}
    for a, t in attacks:
        attacked_by[t].add(a)
        attacks_out[a].add(t)

    grounded: set[int] = set()
    while True:
        nxt = set()
        for i in range(n):
            # 每个攻击者 X 都必须被 grounded 中的某论证攻击:
            # 查 X 的入边 attacked_by[X] (谁攻击 X), 而非出边。
            if all(
                any(defender in grounded for defender in attacked_by[attacker])
                for attacker in attacked_by[i]
            ):
                nxt.add(i)
        if nxt == grounded:
            break
        grounded = nxt

    rejected: set[int] = set()
    for g in grounded:
        rejected |= attacks_out[g]

    def label(i):
        if i in grounded:
            return "accepted"
        if i in rejected:
            return "rejected"
        return "undecided"

    result: dict[Atom, str] = {}
    for atom, ids in index.items():
        labels = {label(i) for i in ids}
        if "accepted" in labels:
            result[atom] = "accepted"
        elif labels == {"rejected"}:
            result[atom] = "rejected"
        else:
            result[atom] = "undecided"

    return result, reachable


def transitive_closure(edges):
    greater: dict[str, set[str]] = {}
    for a, b in edges:
        greater.setdefault(a, set()).add(b)
    changed = True
    while changed:
        changed = False
        for a in list(greater):
            for m in list(greater[a]):
                add = greater.get(m, set()) - greater[a]
                if add:
                    greater[a] |= add
                    changed = True
    return greater
