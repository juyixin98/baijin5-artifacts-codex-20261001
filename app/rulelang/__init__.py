"""受限规则语言 (Restricted Defeasible Rule Language, RDRL).

子模块:
* ast_nodes  —— 语法树数据结构
* lexer      —— 词法分析
* parser     —— 递归下降语法分析
* validator  —— 静态语义校验 (安全性/元数/优先级引用)
* dsl        —— 对外门面: 解析理论文本、事实、查询
"""
from __future__ import annotations

from .ast_nodes import Literal, Rule, RuleKind, Theory
from .dsl import parse_fact, parse_literal, parse_query, parse_theory

__all__ = [
    "Literal",
    "Rule",
    "RuleKind",
    "Theory",
    "parse_theory",
    "parse_fact",
    "parse_literal",
    "parse_query",
]
