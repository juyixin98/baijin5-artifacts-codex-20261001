# dmc — 受限可分解确定性逻辑电路的精确模型计数

对一类受限逻辑电路（D-DNNF 风格片段：AND 可分解、OR 确定、显式平滑）做**精确模型计数**，
输出大整数结果、可审计的证明记录和结构化诊断。纯 Rust 实现，本机原生进程运行，无容器依赖。

## 模块职责

| 文件 | 职责 |
| --- | --- |
| `src/syntax.rs` | 逻辑语法：电路 AST、JSON 反序列化、良构性检查（重复 id / 悬空引用 / 空子项 / 环）、作用域计算、赋值求值 |
| `src/validate.rs` | 结构验证：**先**核验每个 AND 的子项变量两两不交；**再**核验每个 OR 的确定性——优先使用可记录证据（子项不可满足 / 强制文字冲突），无证据且联合作用域 ≤ 阈值时做穷举枚举独立检查，否则报"无法判定" |
| `src/count.rs` | 推理核心：自底向上精确计数。OR 子项按父作用域补 2^k 平滑因子；可分解 AND 直接相乘（子作用域是对父作用域的划分）；顶层对声明全集补未出现变量的平滑因子。全部使用 `BigUint` 大整数 |
| `src/proof.rs` | 证明记录：每个 AND/OR 检查的结论与证据、每个节点的原始计数与平滑指数、顶层平滑指数与总计数，JSON 可序列化（大整数按十进制字符串存储） |
| `src/check.rs` | 独立检查器：真值表穷举计数，与核心不共享代码，用于小变量对照和可选的运行时复核 |
| `src/diag.rs` | 诊断：带 `request_id`、节点位置、失败类别与关键状态的结构化报告；敏感 label 只输出 FNV-1a 指纹（`redacted:<hash>`） |
| `src/pipeline.rs` | 端到端流水线：声明全集校验 → 语法 → 验证 → 计数 → 可选独立复核 → 诊断 |
| `src/request.rs` / `src/config.rs` | 请求与配置的 serde 数据格式 |
| `src/main.rs` | CLI：`dmc count --request <req.json> [--config <cfg.json>] [--proof <out.json>]` |

测试与配置独立组织：`tests/`（计数、验证拒绝、独立对照三组）、`config/`（运行配置）、
`fixtures/`（请求样例与严格配置）、`scripts/verify.sh`（端到端验证）。

## 环境与依赖

- Rust 1.98（cargo/rustc，本机原生工具链）
- 依赖（版本锁定见 `Cargo.lock`）：`serde 1.0.229`、`serde_json 1.0.151`、`num-bigint 0.4.8`
- 验证脚本为 Bash；不需要任何其他语言运行时或容器

## 配置

`config/default.json`：

```json
{
  "determinism_enumeration_threshold": 16,
  "independent_check_threshold": 16,
  "run_independent_check": true
}
```

- `determinism_enumeration_threshold`：OR 子项对无强制文字证据时，允许穷举枚举判定确定性的最大联合作用域变量数；超过则报 `undecidable`
- `independent_check_threshold` / `run_independent_check`：小声明全集上是否用独立真值表复核核心计数

## 请求样例

`fixtures/req_or_split.json`（电路为 `x1 OR (NOT x1 AND x2)`，期望计数 3）：

```json
{
  "request_id": "req-or-split",
  "label": "demo-sensitive-label",
  "declared_vars": [1, 2],
  "circuit": {
    "root": 0,
    "nodes": [
      { "id": 0, "type": "or", "children": [1, 2] },
      { "id": 1, "type": "lit", "var": 1, "phase": true },
      { "id": 2, "type": "and", "children": [3, 4] },
      { "id": 3, "type": "lit", "var": 1, "phase": false },
      { "id": 4, "type": "lit", "var": 2, "phase": true }
    ]
  }
}
```

- `declared_vars` 可省略，此时全集取电路根作用域；声明全集必须覆盖电路变量，且不得有重复
- `label` 视为敏感数据，诊断中只出现其脱敏指纹

## 运行与验证

```bash
cargo build
cargo test                      # 20 个单元/集成测试
bash scripts/verify.sh          # 构建 + 测试 + 6 个夹具端到端断言

# 单个请求：
./target/debug/dmc count --request fixtures/req_or_split.json \
    --config config/default.json --proof /tmp/proof.json
# 输出诊断 JSON（含 request_id、类别、关键状态）与 count=3；退出码 0=接受 1=拒绝/无法判定 2=用法/IO 错误
```

## 实测结果（2026-10-06，本机 Linux x86_64，Rust 1.98.1）

- `cargo test`：20 passed / 0 failed（counting 7、independent 3、validation 10）
- `scripts/verify.sh`：9/9 通过，包括
  - `req_or_split.json` → accepted, count=3
  - `req_and_disjoint.json` → accepted, count=6
  - `req_big_smooth.json` → accepted, count=1267650600228229401496703205376（= 2^100，大整数）
  - `req_and_overlap.json` → rejected `and_not_decomposable`（AND 子项共享变量 1）
  - `req_nondet_or.json` → rejected `or_not_deterministic`（带见证赋值）
  - `req_undecidable.json` + 阈值 0 的严格配置 → `undecidable`
  - 证明记录含 `total` 与 `top_smooth_exp`；敏感 label 未泄漏

## 测试设计要点

- 参考答案来源三路独立：手算期望值（写死在测试里）、独立真值表模块（`check.rs`）、被测核心；
  三方互相断言，不是核心自证
- 小变量真值表逐行对照（`truth_table_spot_check`）
- 拒绝案例均断言具体失败类别与关键状态（重叠变量号、见证赋值、阈值），不只检查"接口能调用"
- 大整数：2^64（AND 乘积）、2^70（OR+顶层平滑）、2^100（纯顶层平滑）精确相等断言

## 限制（如实说明）

- OR 确定性在一般情形下是 coNP 难的：无强制文字证据且联合作用域超过枚举阈值时，本实现报
  `undecidable` 而非猜测；阈值可配置
- 独立真值表复核为指数代价，仅用于小声明全集（默认 ≤16 变量）
- 电路按 DAG 共享节点计数；语法层拒绝环与悬空引用
