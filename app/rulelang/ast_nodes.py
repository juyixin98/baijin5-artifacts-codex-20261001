"""规则语言的语法树节点。

文字 (Literal):
    强否定文字以 negated=True 表示, 例如 -Flies(tweety)。
    NAF (失败即否定) 以 naf=True 表示, 例如 not Flies(X)。
    规则头不允许 NAF; 规则头允许强否定 (用于推出相反结论)。

参数项 (Term) 只有两种:
    ("v", "X")    变量
    ("c", 123)    常量 (str/int/bool), 接地后所有项均为常量
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# 项: ("v", 变量名) | ("c", 常量值)
Term = tuple[str, object]


def var_term(name: str) -> Term:
    return ("v", name)


def const_term(value: object) -> Term:
    return ("c", value)


class RuleKind(str, Enum):
    STRICT = "strict"
    DEFEASIBLE = "defeasible"


@dataclass(frozen=True)
class Literal:
    """一个一阶文字, 如 ``not -Flies(X)``。

    相等与哈希只基于逻辑内容 (谓词/参数/强否定/NAF), 与源码位置无关,
    否则接地实例中的文字将无法与事实文字匹配。
    """

    predicate: str
    args: tuple[Term, ...]
    negated: bool = False  # 强否定 (经典否定)
    naf: bool = False      # 失败即否定 (弱否定), 仅允许出现在规则体
    line: int = 0
    column: int = 0

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Literal):
            return NotImplemented
        return (
            self.predicate == other.predicate
            and self.args == other.args
            and self.negated == other.negated
            and self.naf == other.naf
        )

    def __hash__(self) -> int:
        return hash((self.predicate, self.args, self.negated, self.naf))

    @property
    def arity(self) -> int:
        return len(self.args)

    def key(self) -> tuple[str, int]:
        """谓词符号键 (名字, 元数)。"""
        return (self.predicate, self.arity)

    def variables(self) -> set[str]:
        return {name for kind, name in self.args if kind == "v"}

    def is_ground(self) -> bool:
        return all(kind == "c" for kind, _ in self.args)

    def negate_strong(self) -> "Literal":
        """翻转强否定符号。"""
        return Literal(
            self.predicate,
            self.args,
            negated=not self.negated,
            naf=self.naf,
            line=self.line,
            column=self.column,
        )

    def with_naf(self, naf: bool) -> "Literal":
        return Literal(
            self.predicate,
            self.args,
            negated=self.negated,
            naf=naf,
            line=self.line,
            column=self.column,
        )

    def render(self) -> str:
        parts = []
        if self.naf:
            parts.append("not ")
        if self.negated:
            parts.append("-")
        parts.append(self.predicate)
        if self.args:
            args = ", ".join(_render_term(a) for a in self.args)
            parts.append(f"({args})")
        return "".join(parts)

    def __str__(self) -> str:
        return self.render()


def _render_term(term: Term) -> str:
    kind, value = term
    if kind == "v":
        return str(value)
    if isinstance(value, str):
        if value and (value[0].isalpha() or value[0] == "_") and all(
            ch.isalnum() or ch == "_" for ch in value
        ):
            return value
        return '"' + value.replace('"', '\\"') + '"'
    return str(value)


@dataclass(frozen=True)
class Rule:
    """一条带名字的规则。"""

    rule_id: str
    kind: RuleKind
    head: Literal
    body: tuple[Literal, ...]
    line: int = 0

    @property
    def is_strict(self) -> bool:
        return self.kind is RuleKind.STRICT

    def render(self) -> str:
        arrow = ":-" if self.is_strict else ":="
        body = ", ".join(b.render() for b in self.body)
        prefix = f"@{self.rule_id} "
        if body:
            return f"{prefix}{self.head.render()} {arrow} {body}."
        return f"{prefix}{self.head.render()} {arrow} true."

    def __str__(self) -> str:
        return self.render()


@dataclass
class Theory:
    """一段理论文本解析的完整结果: 事实 + 严格/可撤销规则 + 优先关系。

    严格规则与可撤销规则物理分开存放 (验收规则 1)。
    priorities 中每个元素为 (更强规则id, 更弱规则id)。
    """

    facts: list[Literal] = field(default_factory=list)
    strict_rules: list[Rule] = field(default_factory=list)
    defeasible_rules: list[Rule] = field(default_factory=list)
    priorities: list[tuple[str, str]] = field(default_factory=list)

    @property
    def all_rules(self) -> list[Rule]:
        return [*self.strict_rules, *self.defeasible_rules]

    def rule_by_id(self) -> dict[str, Rule]:
        return {r.rule_id: r for r in self.all_rules}
