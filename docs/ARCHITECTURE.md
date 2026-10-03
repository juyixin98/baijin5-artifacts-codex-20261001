# 架构与失效传播模型

## 模块职责（真实分层，非单文件脚本）

| 包 | 目录 | 职责 |
| --- | --- | --- |
| 语言前端 | `internal/frontend` | 词法分析（`lexer.go`）、AST（`ast.go`）、递归下降解析（`parser.go`），带行列号的词法/语法错误。 |
| IR 变换 | `internal/ir` | 名称解析、跨模块常量折叠、类型检查、泛型单态化、生成栈式指令 IR（`compile.go`、`lower*.go`）。 |
| 运行解释 | `internal/runtime` | 栈式虚拟机执行链接后的 `*ir.Program`，带分类运行期错误。 |
| 公开接口指纹 | `internal/fingerprint` | 对类型/常量/函数/泛型实例计算确定性 SHA-256 指纹，区分“接口哈希”和“函数体哈希”。 |
| 语义差分 | `internal/semdiff` | 对比两次构建，分类变化并在依赖图上计算最小必要失效集；实施语义版本门控。 |
| 诊断 | `internal/diag` | JSON 行结构化日志，带 request/record 标识、判定 accept/reject/inconclusive、关键状态，敏感值脱敏。 |
| 编排 | `internal/pipeline` | 从目录加载、编译、指纹、差分、执行、渲染文本清单与诊断；`verify` 聚合。 |
| 配置 | `internal/config` | 加载 `config/rlverify.json`（仅标准库）。 |
| CLI | `cmd/rlverify` | `compile / run / diff / verify` 子命令。 |
| 独立测试 | `internal/*/*_test.go`、`testdata/golden` | 逐模块单测与独立金样端到端测试。 |

IR 是栈式指令机：`PushInt/PushStr/LoadLocal/StoreLocal/Neg/Bin/Jump/
JumpIfFalse/Call/Return/Pop`。`Call` 指令记录被调模块、函数名、单态化键与参数数。

## 指纹契约（显式记录的接口依赖）

每个函数符号都有两部分指纹：

- `InterfaceHash`：调用者编译时真正依赖的内容——
  - 参数/结果签名；
  - **内联常量依赖** `const_deps`（常量值在调用者代码里被物理内联）；
  - **泛型实例依赖** `generic_deps`（单态化函数体被复制进调用者），泛型实例的
    接口哈希额外包含其实例化体；
  - 普通按名链接的调用 `call_deps` **不**进入接口哈希（私有实现改变不应无谓失效调用者）。
- `BodyHash`：函数自身可执行体。

模块 `PublicHash` 只聚合导出符号的接口面，既不含私有符号，也不含任何函数体。
`FullHash` 额外包含私有符号与全部函数体，用于完整性对照。

这精确实现了题目三条核心要求：

1. **私有实现改变不无谓失效调用者**：私有函数体变化只移动其 `BodyHash`，
   `PublicHash` 不变，按名链接的调用者不进入失效集。
2. **常量内联作为接口依赖显式记录**：常量折叠值进入常量指纹，并通过 `const_deps`
   进入每个内联函数的接口哈希。
3. **泛型体作为接口依赖显式记录**：每个 `Name[实参...]` 实例有独立指纹且包含其
   实例化体；调用者记录 `generic_deps=模块.Name[实参]`。

## 变化分类

`semdiff` 对每个符号给出六类之一：

- `added` / `removed`：结构面增删；
- `public_signature`：导出签名或类型表面变化；
- `inline_const`：折叠常量值变化；
- `generic_body`：某泛型实例（含其实例化体）变化；
- `public_body`：导出函数体变化但接口不变（重链属主，调用者接口不变）；
- `private_body_only`：私有函数体变化，调用者不受影响。

## 最小必要失效集

依赖图的边方向是“被依赖者 → 依赖者”，来自降级阶段记录的依赖账本，而不是再次解析
源码：

- `const 依赖`：内联常量 → 内联它的函数；
- `generic 依赖`：泛型实例 → 使用该实例的函数；
- `call 依赖`：被调函数 → 调用函数（用于完整图与属主重链分析）；
- 类型表面：被导入模块的具名类型 → 签名中提到它的函数。

传播规则：接口级变化（签名/常量/泛型体/增删）作为根，沿消费者边做可达性 BFS，
得到传递闭包即“最小必要失效集”；`private_body_only` 不向外传播（符号自身仍重编）。
可达性集合与“已变化集合”分开记录，因此一个同时内联了变化常量的函数，既被标记失效，
也会作为中间节点继续把失效传播给它的调用者。

## 语义版本门控

- 指纹带 `schema`（指纹/ABI 模式，当前 `fp-v1`）与编译语义版本 `semver`。
- 当 schema 不同，或 semver **主版本号**不同，判定 `inconclusive`：
  旧指纹一律不得复用，所有单元失效（`reused 0`）。
- 相同主版本内（含次版本/补丁升级）按上述指纹与依赖图精确判定。

## 诊断与脱敏

`diff --diag` 输出 JSON 行，每行包含 `request_id`、`record_id`、`decision`
（accept/reject/inconclusive）、`subject`、`reason` 与关键 `state`（变化类别、
前后哈希前缀、是否在失效集中、版本信息）。`sensitive const` 的字面量永远不会出现在
指纹清单或诊断中，仅以 `<redacted...>` 形式呈现类型与长度。
