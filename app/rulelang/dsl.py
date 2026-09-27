"""对外门面: 解析理论 / 事实 / 查询, 并做静态校验。"""
from __future__ import annotations

from .ast_nodes import Literal, Theory
from ..errors import RuleLanguageError
from .parser import parse_literal_text, parse_theory_text
from .validator import validate_theory


def parse_theory(text: str, *, validate: bool = True) -> Theory:
    """解析一整段理论文本并校验。"""
    theory = parse_theory_text(text)
    if validate:
        validate_theory(theory)
    return theory


def parse_fact(text: str) -> Literal:
    """解析一条待写入的事实, 必须接地。"""
    literal = parse_literal_text(text.strip(), trailing_dot=True)
    if literal.naf:
        raise RuleLanguageError(
            "事实不允许使用 not",
            details={"literal": literal.render()},
        )
    if not literal.is_ground():
        raise RuleLanguageError(
            f"事实必须接地, 但 {literal.render()} 含变量",
            details={"literal": literal.render()},
        )
    return literal


def parse_literal(text: str) -> Literal:
    """解析任意单个文字 (可能含变量)。"""
    return parse_literal_text(text.strip())


def parse_query(text: str) -> Literal:
    """解析查询目标。

    查询可以是接地文字 (是/否/悬置) 或含自由变量的模板 (回带置换)。
    查询不允许 NAF —— 若要查询"不能证明", 直接查询正文字后看状态即可。
    """
    literal = parse_literal_text(text.strip())
    if literal.naf:
        raise RuleLanguageError(
            "查询目标不允许使用 not; 请直接查询正文字并阅读其状态",
            details={"literal": literal.render()},
        )
    return literal
