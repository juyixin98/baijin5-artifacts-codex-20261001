"""词法分析器。

支持的词法:
    标识符/变量  [A-Za-z_][A-Za-z0-9_]*   (大写或下划线开头 => 变量; 小写开头 => 常量符号)
    字符串常量   "..."
    整数常量     [0-9]+
    关键字       not, true
    符号         :-  :=  @  .  ,  (  )  -
    行注释       % ... 或 # ... 直到行尾
"""
from __future__ import annotations

from dataclasses import dataclass

from ..errors import RuleLanguageError

_KEYWORDS = {"not", "true"}
_SYMBOLS_TWO = (":-", ":=")
_SYMBOLS_ONE = ("@", ".", ",", "(", ")", "-")


@dataclass(frozen=True)
class Token:
    kind: str          # IDENT | STRING | INT | KEYWORD | SYMBOL | EOF
    value: object
    line: int
    column: int

    def is_symbol(self, sym: str) -> bool:
        return self.kind == "SYMBOL" and self.value == sym

    def is_keyword(self, word: str) -> bool:
        return self.kind == "KEYWORD" and self.value == word


_SIMPLE_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "n": "\n",
    "t": "\t",
    "r": "\r",
}


def tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(text)
    line, col = 1, 1

    def take() -> str:
        nonlocal i, line, col
        ch = text[i]
        i += 1
        if ch == "\n":
            line += 1
            col = 1
        else:
            col += 1
        return ch

    while i < n:
        ch = text[i]
        if ch in " \t\r\n":
            take()
            continue
        if ch in "%#":
            while i < n and text[i] != "\n":
                take()
            continue

        start_line, start_col = line, col

        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            word = text[i:j]
            for _ in range(j - i):
                take()
            kind = "KEYWORD" if word in _KEYWORDS else "IDENT"
            tokens.append(Token(kind, word, start_line, start_col))
            continue

        if ch.isdigit():
            j = i
            while j < n and text[j].isdigit():
                j += 1
            digits = text[i:j]
            for _ in range(j - i):
                take()
            tokens.append(Token("INT", int(digits), start_line, start_col))
            continue

        if ch == '"':
            take()  # opening quote
            chars: list[str] = []
            closed = False
            while i < n:
                c = text[i]
                if c == '"':
                    take()
                    closed = True
                    break
                if c == "\n":
                    raise RuleLanguageError(
                        "字符串常量中不允许换行",
                        details={"line": line, "column": col},
                    )
                if c == "\\":
                    take()
                    if i >= n:
                        break
                    esc = text[i]
                    if esc not in _SIMPLE_ESCAPES:
                        raise RuleLanguageError(
                            f"非法转义序列 \\{esc}",
                            details={"line": line, "column": col},
                        )
                    chars.append(_SIMPLE_ESCAPES[esc])
                    take()
                else:
                    chars.append(c)
                    take()
            if not closed:
                raise RuleLanguageError(
                    "字符串常量缺少结束引号",
                    details={"line": start_line, "column": start_col},
                )
            tokens.append(Token("STRING", "".join(chars), start_line, start_col))
            continue

        two = text[i : i + 2]
        if two in _SYMBOLS_TWO:
            take()
            take()
            tokens.append(Token("SYMBOL", two, start_line, start_col))
            continue
        if ch in _SYMBOLS_ONE:
            take()
            tokens.append(Token("SYMBOL", ch, start_line, start_col))
            continue

        raise RuleLanguageError(
            f"无法识别的字符 {ch!r}",
            details={"line": start_line, "column": start_col},
        )

    tokens.append(Token("EOF", None, line, col))
    return tokens
