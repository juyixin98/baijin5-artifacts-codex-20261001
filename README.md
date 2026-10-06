# pmd：代数数据模式 → 共享测试决策树编译器

把带守卫的代数数据模式匹配编译成**共享测试决策树**，并提供对照的
顺序模式解释器与语义差分，验证编译结果在边界输入上也不偏离顺序语义。
纯 Go 标准库，多模块 workspace，无第三方依赖。

## 模块职责

| 模块 | 目录 | 职责 |
| --- | --- | --- |
| 语言前端 | `frontend/` | DSL 词法/语法分析、语义校验（构造器表、模式 arity、变量重复绑定、守卫变量作用域、内建函数签名），产出带位置的分类错误 |
| IR 变换 | `ir/` | 模式矩阵 → 共享测试决策树（switch/guard/leaf/fail 四类节点），统计信息，不可达分支警告 |
| 运行解释 | `runtime/` | 决策树求值器（`EvalTree`）+ 独立的顺序模式解释器（`EvalSequential`，语义参照），守卫表达式求值与副作用追踪，值校验 |
| 语义差分 | `diff/` | 确定性穷举语料（按值高度枚举）+ 种子随机语料，双引擎结果对比，黄金夹具校验，mismatch/uncertain 分列的报告 |
| 服务 | `service/` | HTTP API（`/v1/health|compile|match|diff`），请求 ID 关联、结构化日志、模块版本上报；`cmd/pmd-server` 为服务入口，`cmd/pmd` 为本地 CLI |
| 独立测试 | `tests/` | 黑盒测试：只使用各模块公开 API，断言具体结果与失败类别 |
| 配置 | `config/server.json` | 服务监听地址、请求体上限、差分语料规模 |
| 夹具 | `fixtures/` | 示例程序（`programs/*.pmd`）与**手写**期望结果（`golden/*.json`，非由被测实现生成） |

## DSL 语法

```
ctor Nil 0            # 构造器声明：名字（大写开头）+ 元数
ctor Cons 2

match xs {            # 按源顺序逐分支优先匹配
  | Cons(x, Cons(y, Nil)) if effect("pair-check", eq(x, y)) => "pair-eq"
  | Cons(x, Cons(_, _)) => "long"
  | Cons(x, Nil) if even(x) => "single-even"
  | Cons(_, Nil) => "single"
  | Nil => "empty"
}
```

- 模式：`_` 通配、小写变量、字面量（整数/字符串/布尔）、构造器（可嵌套）。
- 守卫：`and`/`or`/`not`（短路）+ 内建函数 `eq ne lt le gt ge even odd pos neg`，
  以及 `effect("label", cond)`：求值 cond、向副作用轨迹追加一条记录并返回 cond，
  用于观测守卫求值的次数与顺序。
- 值编码：`{"ctor":"Cons","args":[...]}` 或 `{"lit":2}`。

## 三条正确性性质及其落实方式

1. **分支原顺序优先级保留**：矩阵行即源分支、始终保持原顺序；仅当首行
   为全通配/变量时才提交叶节点；switch 时对某列无约束的行被复制进每个
   分支与默认分支，因此高优先级行不会丢失。由 `tests.TestBranchOrderPriority`
   与穷举差分共同验证。
2. **守卫不重复、不提前**：守卫节点只在其所属行的全部结构测试完成之后
   才出现在树中（结构未匹配的路径根本到不了该守卫）；守卫为假时进入
   Else 子树继续后续分支，该行随之移除，任意一次求值路径上同一守卫
   至多出现一次。`tests.TestGuardEvaluatedOnceInOrderNotEarly` 断言副作用
   轨迹的确切序列（含"结构不匹配则零副作用"）。
3. **失败路径与绑定作用域正确**：行集为空即产生 fail 节点 → `no_match`；
   绑定以（名字 → 值路径）记录在叶/守卫节点上，按分支隔离；守卫失败
   后该分支的绑定不泄漏到后续分支（`tests.TestBindingScopeAcrossBranches`）；
   前端在编译前拒绝守卫引用未绑定变量、重复绑定等作用域错误。

共享性：同一路径在同一求值路径上至多测试一次（`ir.TestNoRepeatedPathOnAnyRoute`
对每条根到叶路径断言），重叠分支共享公共前缀测试。

## 错误语义

所有错误都带**类别**，前端/服务错误另带源码位置；运行期失败是正常结果
（`ok:true` + `result.outcome.failure`），协议层错误才是 `ok:false` + HTTP 4xx。

| 类别 | 产生位置 | 含义 | HTTP |
| --- | --- | --- | --- |
| `parse_error` | frontend | 词法/语法错误（位置随行号列号） | 400 |
| `semantic_error` | frontend | 未知构造器、arity 不符、变量重复绑定、守卫引用未绑定变量、未知守卫函数 | 400 |
| `invalid_value` | runtime | 输入值未通过构造器表校验（未知构造器/元数不符，含值路径） | 200（结果内） |
| `no_match` | runtime | 值合法但无分支匹配（决策树 fail 节点） | 200（结果内） |
| `guard_error` | runtime | 守卫求值错误（类型不符、非常量参数等） | 200（结果内） |
| `bad_request` | service | 请求体非法 JSON、缺字段、超过大小上限 | 400 |
| `internal_error` | service/runtime | 不应发生的内部不一致（防御性） | 500/200 |

不确定结论单列：`/v1/diff` 报告中无法判定一致性的值（如校验失败的
输入）进入 `uncertain[]`，与 `mismatches[]` 分开；编译警告（不可达分支）
进入响应顶层 `warnings[]`。

可解释性：每个响应带 `request_id`（回显 `X-Request-ID` 或自动生成）与
全部模块版本；`outcome.steps` 给出逐节点求值轨迹（节点 ID、路径、分支
决策）；日志为 JSON 行，含 request_id、模块名与版本、关键步骤计数。

## HTTP API

- `GET /v1/health` → 状态与各模块版本。
- `POST /v1/compile` `{"source": "..."}` → 决策树 JSON、统计、警告。
- `POST /v1/match` `{"source": "...", "value": {...}, "verify": true}` →
  决策树求值结果（分支、绑定、副作用轨迹、步骤轨迹、失败类别），
  并默认用顺序解释器交叉验证（`verification.agree`）。
- `POST /v1/diff` `{"source": "...", "cases": 300, "seed": 1, "max_depth": 4}` →
  穷举 + 随机语料上双引擎差分报告。

## 运行与验证

```bash
bash scripts/verify.sh   # 全模块 go vet + go test + 构建（实际执行并汇报）
bash scripts/demo.sh     # 构建并启动本地服务，逐端点演示（PORT 可覆盖，默认 18080）
```

CLI（`service/cmd/pmd`）：

```bash
cd service && go build -o /tmp/pmd ./cmd/pmd
/tmp/pmd compile -p ../fixtures/programs/list.pmd          # 打印决策树
/tmp/pmd match   -p ../fixtures/programs/list.pmd -v '{"ctor":"Nil"}'
/tmp/pmd diff    -p ../fixtures/programs/tree.pmd -n 500   # 不一致时退出码为 1
```

服务（`service/cmd/pmd-server`）：

```bash
cd service && go build -o /tmp/pmd-server ./cmd/pmd-server
/tmp/pmd-server -config ../config/server.json -addr 127.0.0.1:8080
```

## 测试策略

- 单元测试位于 `frontend/`、`ir/`、`runtime/`（解析错误类别、树形结构、
  求值结果）。
- 独立黑盒测试位于 `tests/`：分支优先级、守卫副作用次数/顺序/不提前、
  嵌套字段绑定、失败类别（no_match/invalid_value/guard_error/parse_error/
  semantic_error）、绑定作用域、字面量模式、共享测试结构、HTTP 端到端。
- 语义差分：对三个夹具程序做深度 3 穷举（去重、上限 2000）+ 种子随机
  语料，双引擎逐值对比分支/绑定/副作用/失败类别，要求零 mismatch。
- 黄金夹具：`fixtures/golden/*.json` 的期望结果为人工计算手写，
  不由被测实现生成。

## 复现步骤

1. `bash scripts/verify.sh` —— 应输出 `ALL CHECKS PASSED`。
2. `bash scripts/demo.sh` —— 应输出 `DEMO OK`，可见健康检查、编译树、
   守卫失败回退的 match、非法程序的分类错误、2200/2200 一致的差分报告。
3. 单模块测试：`cd <module> && GOFLAGS=-mod=readonly go test ./...`
   （本机全局 `GOFLAGS=-mod=mod` 与 workspace 模式冲突，脚本内已处理）。

## 限制（如实说明）

- 不支持 or-pattern 与字面量浮点；JSON 数字按 int64 解析。
- 决策树不做节点级 DAG 合并（共享的是"测试不重复"性质，树可能因
  通配行复制而增大）；守卫节点可在静态树中出现多次，但单次求值路径
  上至多执行一次。
- 守卫内建函数集合固定于 `frontend.Builtins`，扩展需同步两端实现。
