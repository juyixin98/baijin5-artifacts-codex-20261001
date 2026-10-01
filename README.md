# fologic — 有限枚举域一阶公式的量词消去与模型验证

纯 Rust（原生进程，无容器）实现。对有限、多排序（multi-sorted）枚举域上的
一阶闭公式：

1. **捕获避免替换**（alpha 重命名后替换自由变量）；
2. **有界量词消去**（把 `∀` 展成合取、`∃` 展成析取），预算不足时**保留未处理
   量词**并把结论标记为 `unknown`，不猜测真值；
3. **独立模型验证**：检查器用自己的递归求值器重算参考真值、重放展开记账、校验
   证明记录一致性；
4. **空域策略显式**：`allow_empty_domain` 为真时用标准约定（空集上 `∀ ≡ true`、
   `∃ ≡ false`），为假时返回可区分的计算失败 `empty_domain`。

## 工程边界（模块职责与数据/错误契约）

| 模块 | 文件 | 职责 |
| --- | --- | --- |
| 语法 | `src/syntax.rs` | 项/公式 AST（serde 标签 JSON）、自由变量分析、节点计数、测试构造器 |
| 模型 | `src/model.rs` | 枚举排序、常量、（部分）函数表、闭世界谓词；声明校验与类型解析 |
| 替换 | `src/subst.rs` | 捕获避免替换、alpha 重命名、新鲜命名 |
| 推理核心 | `src/expand.rs` | 确定性有界展开、预算预留/回滚、连接词拼接、节点上限保护 |
| 递归求值 | `src/evaluator.rs` | 直接在模型上递归量词的独立参考语义；展开式求值（残余量词→unknown） |
| 证明记录 | `src/proof.rs` | 运行编号、有序步骤事件、`RunLogger`、`Verdict`、`ProofRecord` |
| 独立检查 | `src/checker.rs` | 独立重算真值 + 重放记账 + 记录一致性，分歧报状态冲突 |
| 服务边界 | `src/service.rs` | 请求/响应 JSON 契约、内联或本地夹具路径、整段管线编排 |
| 错误契约 | `src/error.rs` | 四类互不相交错误：`input` / `state_conflict` / `resource_exhausted` / `computation_failed` |
| CLI | `src/main.rs` | `check` / `check-file` / `verify` / `serve` 子命令 |

模块之间只通过 `syntax`/`model` 的数据类型和 `QeError` 错误类型通信。

### 错误分类（退出码）

| 类别 | code 示例 | CLI 退出码 |
| --- | --- | --- |
| `input` | `unknown_symbol`, `arity_mismatch`, `unbound_variable`, `sort_mismatch`, `unknown_element`, `ambiguous_element`, `duplicate_symbol`, `fixture_unreadable`, `fixture_invalid_json`, `free_variable` | 2 |
| `state_conflict` | `proof_check_failed`（含失败检查项名） | 3 |
| `resource_exhausted` | `node_cap`（展开式 AST 超上限） | 4 |
| `computation_failed` | `empty_domain`、`partial_function`（函数表缺元组） | 5 |

注意：**预算不足不是错误**。它是一个成功响应中的 `verdict = "unknown"`，
同时保留量词节点并给出理由；只有 `node_cap` 这类硬性资源保护才返回
`resource_exhausted`。

## 数据契约

### 项（`"t"` 为判别字段）

```json
{ "t": "var", "name": "x" }
{ "t": "elem", "value": "x1" }
{ "t": "elem", "value": "x1", "sort": "X" }
{ "t": "const", "name": "a" }
{ "t": "app", "name": "next", "args": [ { "t": "var", "name": "x" } ] }
```

未限定 `elem` 的元素名在全模型唯一时自动解析到所属排序；跨排序重名报
`ambiguous_element`；不存在报 `unknown_element`。

### 公式（`"op"` 为判别字段）

`bool`（`{"op":"bool","value":true}`）、`pred`、`eq`、`not`、`and`、`or`、
`impl`、`iff`、`forall`、`exists`。量词形如：

```json
{ "op": "forall", "var": "x", "sort": "X", "inner": { "...": "..." } }
```

### 请求（CheckRequest，可内联 model/formula，也可给本地路径）

```json
{
  "model_path": "fixtures/model/finite_model.json",
  "formula_path": "fixtures/formulas/f03_alt_true.json",
  "run_id": "my-run",
  "budget": 64,
  "node_cap": 1000000,
  "allow_empty_domain": false,
  "verify": true
}
```

`budget` 缺省为无限制；`run_id` 缺省自动生成（`run-<unix毫秒>-<计数>`）。
成功响应含 `verdict`、`instantiations_used`、`residual_quantifiers`、
`expanded_formula`、完整 `proof`（带逐步事件）和 `check_report`。失败响应为
`{"run_id": ..., "error": {"kind", "code", "message"}}`。

## 预算语义（可重放、确定性）

- 顺序：最左最外量词优先，元素按模型中声明顺序。
- 每次“量词变量绑定一个域元素”计 1；嵌套量词在每个外层分支重复计。因此
  域大小为 m、n 的 `∀x∃y` 完整展开成本为 `m + m·n`。
- 一个量词节点若**无法完整支付**，整个节点原样保留，节点内已计实例全部
  **回滚**（证明步骤里是 `rollback_instances`），结论变 `unknown`。
- 连接词是透明拼接点：已展开兄弟保留，中止兄弟原样保留，之后兄弟不再访问。
- 完全展开成功后若 AST 节点数超过 `node_cap`，报 `resource_exhausted/node_cap`。

证明步骤事件：`expand_start`、`instantiate`、`expand_done`、
`budget_exhausted`、`rollback_instances`、`eval_term`、`eval_atom`、
`eval_formula`、`note`。每个事件带单调 `seq`，检查器据此重放。

## 运行与复现（Linux 原生，仅需 Rust 1.98 / Cargo）

```bash
# 1) 一键验证：fmt、构建、全部测试、重新生成夹具、正常+异常 CLI 矩阵
./scripts/run_tests.sh
# 结果与逐用例 stdout/stderr/exitcode 存到 fixtures/results/<run-id>/

# 2) 仅跑测试
cargo test                 # 单元测试 + tests/golden.rs + tests/generated.rs
cargo test --lib           # 捕获避免替换 / 预算 / 回滚 / 空域 / 节点上限
cargo test --test golden   # 手算期望值、错误类别、证明篡改检测
cargo test --test generated# 由 Rust 生成器产出的夹具

# 3) 重新生成本地合成夹具
cargo run --example gen_fixtures

# 4) 单个请求（路径或内联）
cargo run -- check-file fixtures/requests/req_budget_mid.json
cargo run -- check --model fixtures/model/finite_model.json \
  --request fixtures/requests/req_success_f03.json

# 5) 独立验证一份证明记录
cargo run -- verify --model fixtures/model/finite_model.json --proof <proof.json>

# 6) 行分隔 JSON 的 TCP 服务 + 正常/异常调用示例
./scripts/serve_demo.sh
# 或手动：
cargo run -- serve --addr 127.0.0.1:8140
echo '{"model_path":"fixtures/model/finite_model.json",
       "formula_path":"fixtures/formulas/f03_alt_true.json"}' \
  | cargo run --example tcp_client -- --addr 127.0.0.1:8140
```

依赖锁定见 `Cargo.lock`（仅 `serde`、`serde_json` 及其传递依赖），可离线复核。

## 夹具清单（全部本地合成，无真实业务数据）

- `fixtures/model/finite_model.json`：排序 `X(3)`、`Y(2)`、`Z(空)`；常量、
  三元循环函数 `next`、部分二元函数 `pair`；谓词 `p/q/r`（`r` 在空排序上）。
- `fixtures/formulas/f01..f13`：交替量词、变量遮蔽、空关系/空排序、相等与函数
  循环、预算边界、捕获替换用公式。
- `fixtures/requests/req_*.json`：正常与异常请求（含 `allow_empty_domain`
  开/关、预算不足/精确/为零、节点上限、未知符号、元数错、自由变量等）。
- `fixtures/gen/`：由 `examples/gen_fixtures.rs` 重新生成。
- `fixtures/results/<run-id>/`：每次 `run_tests.sh` 的可复核结果与运行日志。

## 关键真值（夹具中独立手算，非被测核心生成）

| 公式 | 含义 | 参考真值 |
| --- | --- | --- |
| f03 `∀x∃y q(x,y)` | x3 无任何 y 满足 | false（成本 9） |
| f04 `∃y∀x q(x,y)` | y1/y2 都不能覆盖全部 x | false（成本 8） |
| f05 遮蔽 | 内层 `∃x x=x3` 对每个外层 x 成立 | true（成本 12） |
| f06/f07 空排序 Z | `∀z`=true，`∃z`=false（仅在允许空域时） | true / false |
| f08 `next^3(x)=x` | 3 元循环，三次复合为恒等 | true |
| f09 `next(a)=b` | a=x1→x2=b | true |
