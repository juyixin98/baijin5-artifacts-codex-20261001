"""AST 与可存储/可传输字典之间的编解码。

接地原子的规范键 (canonical key):
    (predicate, sign, args_json)
其中 sign 为 "+" / "-", args_json 为常量元组的 JSON 文本。
"""
from __future__ import annotations

import json

from .ast_nodes import Literal, Rule, RuleKind


def term_to_dict(term) -> dict:
    kind, value = term
    if kind == "v":
        return {"t": "v", "name": value}
    return {"t": "c", "value": value}


def term_from_dict(data: dict):
    if data["t"] == "v":
        return ("v", data["name"])
    return ("c", data["value"])


def literal_to_dict(lit: Literal) -> dict:
    return {
        "predicate": lit.predicate,
        "args": [term_to_dict(a) for a in lit.args],
        "negated": lit.negated,
        "naf": lit.naf,
        "line": lit.line,
        "column": lit.column,
    }


def literal_from_dict(data: dict) -> Literal:
    return Literal(
        predicate=data["predicate"],
        args=tuple(term_from_dict(a) for a in data["args"]),
        negated=data.get("negated", False),
        naf=data.get("naf", False),
        line=data.get("line", 0),
        column=data.get("column", 0),
    )


def rule_to_dict(rule: Rule) -> dict:
    return {
        "rule_id": rule.rule_id,
        "kind": rule.kind.value,
        "head": literal_to_dict(rule.head),
        "body": [literal_to_dict(b) for b in rule.body],
        "line": rule.line,
        "text": rule.render(),
    }


def rule_from_dict(data: dict) -> Rule:
    return Rule(
        rule_id=data["rule_id"],
        kind=RuleKind(data["kind"]),
        head=literal_from_dict(data["head"]),
        body=tuple(literal_from_dict(b) for b in data["body"]),
        line=data.get("line", 0),
    )


def ground_key(lit: Literal) -> tuple[str, str, str]:
    """接地文字的规范键。要求文字接地且不带 NAF。"""
    if lit.naf:
        raise ValueError("NAF 文字没有规范接地键")
    if not lit.is_ground():
        raise ValueError(f"文字未接地, 无法生成规范键: {lit.render()}")
    sign = "-" if lit.negated else "+"
    args = [value for _, value in lit.args]
    return (lit.predicate, sign, json.dumps(args, ensure_ascii=False, sort_keys=True))
