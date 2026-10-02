# 标量条件循环语言（前端）

一种最小的整数语言，用于表达“带标量条件的计数循环”。所有值为 `int64`，
条件按非零为真处理。

## 语法（EBNF 风格）

```
program     := decl* loop* reduceloop*
decl        := ("input" | "output") name ("[" constExpr "]")? "int64"
              ("," name ("[" constExpr "]")? "int64")* ";"
loop        := "for" ident ":=" expr ".." expr block
reduceloop  := "for" ident ":=" expr ".." expr
              "{" "reduce" ident reduceOp ident "[" ident "]" ";"? "}"
reduceOp    := "+=" | "*=" | "concat="
block       := "{" stmt* "}"
stmt        := assign | if
assign      := name ("[" expr "]")? "=" expr ";"?
if          := "if" "(" expr ")" block ("else" (block | if))?
expr        := 见运算符优先级
constExpr   := 整数字面量与 + - * 及一元负号
```

分号可省略：遇到 `}`、关键字或 EOF 自动结束语句。支持 `//` 行注释。

## 运算符（从低到高）

`||` < `&&` < `|` < `^` < `&` < `== !=` < `< > <= >=` < `+ -` < `* / %`，
一元 `- ! ~` 与括号、`len(a)`、`true/false`。

`&&` 与 `||` 在标量参考中是短路的；lowering 通过“按左值结果收窄右值谓词”
在掩码 IR 中复现相同语义，因此被左操作数屏蔽的通道不会执行右侧的除零或越界。

## 边界与陷阱

- 只有一个顶层计数循环，循环变量从常量 `0` 开始，上界为 `len(数组)`，
  或请求中提供的标量（此时诊断为需要请求数据才能判定）。
- 只读输入；只允许写 `output`。数组输出越界写、输入数组越界读、
  除以/模 `0` 都是**活动通道陷阱**。
- 不支持嵌套循环、循环间携带变量、数组的数组；违反者为 `REJECT`。
