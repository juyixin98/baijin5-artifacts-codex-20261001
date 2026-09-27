"""递归下降语法分析器。

理论文件语法 (EBNF 风格):

    theory    := statement*
    statement := rule | fact | priority
    rule      := "@" IDENT literal ("::-")?  body "."
                 箭头 ":-" 为严格规则, ":=" 为可撤销规则
    fact      := literal "."
    priority  := "priority" "(" IDENT "," IDENT ")" "."
    body      := "true" | literal ("," literal)*
    literal   := ["not"] ["-"] IDENT ["(" term ("," term)* ")"]
    term      := IDENT | STRING | INT

约定:
* @ 命名的才是规则; 没有 @ 的接地文字是事实。
* 大写或下划线开头的 IDENT 是变量, 小写开头的是常量符号。
"""
from __future__ import annotations

from .ast_nodes import (
    Literal,
    Rule,
    RuleKind,
    Theory,
    const_term,
    var_term,
)
from ..errors import RuleLanguageError
from .lexer import Token, tokenize


class _Parser:
    def __init__(self, text: str) -> None:
        self.tokens = tokenize(text)
        self.pos = 0

    # -- 基础工具 -----------------------------------------------------------

    @property
    def cur(self) -> Token:
        return self.tokens[self.pos]

    def peek_kind(self) -> str:
        return self.cur.kind

    def advance(self) -> Token:
        tok = self.cur
        self.pos += 1
        return tok

    def expect_symbol(self, sym: str) -> Token:
        if not self.cur.is_symbol(sym):
            raise self._error(f"期望符号 {sym!r}, 实际得到 {self._describe(self.cur)}")
        return self.advance()

    def expect_ident(self) -> Token:
        if self.cur.kind != "IDENT":
            raise self._error(
                f"期望标识符, 实际得到 {self._describe(self.cur)}"
            )
        return self.advance()

    def expect_keyword(self, word: str) -> Token:
        if not self.cur.is_keyword(word):
            raise self._error(f"期望关键字 {word!r}, 实际得到 {self._describe(self.cur)}")
        return self.advance()

    def _error(self, msg: str) -> RuleLanguageError:
        t = self.cur
        return RuleLanguageError(
            msg,
            details={"line": t.line, "column": t.column, "token": self._describe(t)},
        )

    @staticmethod
    def _describe(t: Token) -> str:
        if t.kind == "EOF":
            return "<文件结束>"
        return f"{t.kind}:{t.value!r}"

    # -- 顶层 ---------------------------------------------------------------

    def parse_theory(self) -> Theory:
        theory = Theory()
        seen_ids: set[str] = set()
        while self.cur.kind != "EOF":
            if self.cur.is_symbol("@"):
                rule = self._parse_rule()
                if rule.rule_id in seen_ids:
                    raise self._error(f"规则标识重复: @{rule.rule_id}")
                seen_ids.add(rule.rule_id)
                if rule.is_strict:
                    theory.strict_rules.append(rule)
                else:
                    theory.defeasible_rules.append(rule)
            elif self.cur.kind == "IDENT" and self.cur.value == "priority":
                strong, weak = self._parse_priority()
                theory.priorities.append((strong, weak))
            else:
                fact = self._parse_literal()
                self.expect_symbol(".")
                theory.facts.append(fact)
        return theory

    def _parse_rule(self) -> Rule:
        at = self.expect_symbol("@")
        name_tok = self.expect_ident()
        head = self._parse_literal()
        if head.naf:
            raise RuleLanguageError(
                "规则头不允许使用失败即否定 not",
                details={"line": head.line, "column": head.column},
            )
        if self.cur.is_symbol(":-"):
            kind = RuleKind.STRICT
            self.advance()
        elif self.cur.is_symbol(":="):
            kind = RuleKind.DEFEASIBLE
            self.advance()
        else:
            raise self._error(
                f"规则 @{name_tok.value} 头之后期望箭头 :- 或 :=, "
                f"实际得到 {self._describe(self.cur)}"
            )
        body = self._parse_body()
        self.expect_symbol(".")
        return Rule(
            rule_id=str(name_tok.value),
            kind=kind,
            head=head,
            body=tuple(body),
            line=at.line,
        )

    def _parse_body(self) -> list[Literal]:
        if self.cur.is_keyword("true"):
            self.advance()
            return []
        body: list[Literal] = [self._parse_literal()]
        while self.cur.is_symbol(","):
            self.advance()
            body.append(self._parse_literal())
        return body

    def _parse_priority(self) -> tuple[str, str]:
        kw = self.advance()  # priority
        self.expect_symbol("(")
        strong = str(self.expect_ident().value)
        self.expect_symbol(",")
        weak = str(self.expect_ident().value)
        self.expect_symbol(")")
        self.expect_symbol(".")
        if strong == weak:
            raise RuleLanguageError(
                f"优先关系不能自指: priority({strong}, {weak})",
                details={"line": kw.line},
            )
        return strong, weak

    # -- 文字与项 -----------------------------------------------------------

    def _parse_literal(self) -> Literal:
        line, col = self.cur.line, self.cur.column
        naf = False
        if self.cur.is_keyword("not"):
            naf = True
            self.advance()
        negated = False
        if self.cur.is_symbol("-"):
            negated = True
            self.advance()
        pred_tok = self.expect_ident()
        args: list = []
        if self.cur.is_symbol("("):
            self.advance()
            if not self.cur.is_symbol(")"):
                args.append(self._parse_term())
                while self.cur.is_symbol(","):
                    self.advance()
                    args.append(self._parse_term())
            self.expect_symbol(")")
        return Literal(
            predicate=str(pred_tok.value),
            args=tuple(args),
            negated=negated,
            naf=naf,
            line=line,
            column=col,
        )

    def _parse_term(self):
        t = self.cur
        if t.kind == "IDENT":
            self.advance()
            name = str(t.value)
            if name[:1].isupper() or name.startswith("_"):
                return var_term(name)
            return const_term(name)
        if t.kind == "STRING":
            self.advance()
            return const_term(str(t.value))
        if t.kind == "INT":
            self.advance()
            return const_term(int(t.value))
        raise self._error(f"期望变量或常量, 实际得到 {self._describe(t)}")


def parse_theory_text(text: str) -> Theory:
    return _Parser(text).parse_theory()


def parse_literal_text(text: str, *, trailing_dot: bool = False) -> Literal:
    """解析单个文字 (用于事实写入与查询)。可含变量 (查询模板)。

    trailing_dot=True 时允许 (但不要求) 文字后带一个句点。
    """
    parser = _Parser(text)
    literal = parser._parse_literal()
    if trailing_dot and parser.cur.is_symbol("."):
        parser.advance()
    if parser.cur.kind != "EOF":
        raise RuleLanguageError(
            f"单个文字之后存在多余输入: {parser._describe(parser.cur)}",
            details={"line": parser.cur.line, "column": parser.cur.column},
        )
    return literal
