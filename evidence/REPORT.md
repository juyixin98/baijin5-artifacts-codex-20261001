# 证据报告（Evidence）

本文件记录本实现的验证证据、运行身份、环境与结果。所有结论来自实际执行，
原始测试输出见同目录 `test-run.txt`。

## 运行身份与版本

- 引擎版本：`0.1.0`（`Cargo.toml`，编译时经 `CARGO_PKG_VERSION` 写入每个
  `RunRecord.engine_version` 与 `/health` 响应）。
- 每次执行生成唯一 `run_id`：`run-<unix-millis>-<进程内单调序号>`，出现在
  HTTP 响应体、结构化 JSON 服务日志中，用于把一次请求与日志关联。
  - 实跑示例：`run-1790689584368-0`、`run-1790689584396-1`、
    `run-1790689584425-2`，且能在服务日志中 grep 到相同 `run_id`。
- 工具链：`rustc 1.98.1 (48a229cea 2026-09-01)`、`cargo 1.98.1`，
  Linux 6.8.0-90-generic。
- 依赖：`Cargo.lock` 已提交，`cargo test --locked` / `cargo build --locked`
  均通过（锁文件与清单一致）。

> 环境备注：本机的默认 `~/.cargo` 被同机其它并发任务长时间占用包缓存锁
> （`Blocking waiting for file lock on package cache`）。为不受影响地复现，
> 本次构建使用了隔离的 `CARGO_HOME=/home/admin/.cargo-opp464`。该隔离目录
> 不在仓库内；在没有锁竞争的机器上，直接用默认 `CARGO_HOME` 即可，
> `--locked` 会解析同一份 `Cargo.lock`。

## 自动化测试结果（实际运行）

命令：`CARGO_HOME=/home/admin/.cargo-opp464 cargo test --locked --no-fail-fast`

| 测试二进制 | 通过 | 失败 | 忽略 |
|---|---:|---:|---:|
| 库单元测试（config、accumulated 去重语义） | 6 | 0 | 0 |
| `tests/http_api.rs`（真实 Axum router） | 5 | 0 | 0 |
| `tests/recursive_semantics.rs`（语义 + 参考对照） | 13 | 0 | 0 |
| `tests/validation.rs`（失败类别） | 16 | 0 | 0 |
| doc-tests | 0 | 0 | 0 |
| **合计** | **40** | **0** | **0** |

另：`cargo fmt` 已运行；`cargo clippy --all-targets -- -D warnings` 退出码 0。

### 失败 / 未执行项（如实记录）

- **失败项：无。** 最终提交状态下 40 个测试全部通过。
- **忽略项：无**（没有 `#[ignore]`，输出中 ignored 均为 0）。
- **未执行项：无计划内但未跑的测试。** 唯一与“运行”有关的偏差是环境性的，
  非代码问题：首次尝试在 `127.0.0.1:8091` 启动时，该端口已被同机另一会话
  的服务占用（其 `/health` 返回 `0.1.0/group-probe-v1`，并非本程序），
  本程序按预期报错 `failed to bind ... Address already in use` 并以非零码
  退出；改用系统分配的空闲端口后一切正常。
- 开发过程中出现过的编译/断言错误均已修复并复测通过，没有保留为已知失败
  （例如：arrow2 0.18 无独立 `std` feature → 改用默认特性；`Chunk` 无
  `new_with_schema` → 用 `Chunk::new`；`max_depth` 边界约定统一为“深度恰好
  等于上限的行仍输出，更深一层才阻断”，并相应更新了断言）。

## 语义证据如何成立（不是“接口能调用”式断言）

### 独立参考实现，且不由被测核心生成

`src/reference/mod.rs` 是显式递归 oracle：

- 只使用邻接表 + 普通值向量，控制流是字面递归函数 `walk`，与被测引擎的
  工作表/累积表半朴素循环**不共享任何执行代码**。
- `enumerate_all` 直接枚举“每一条游走”（天然是 `UNION ALL` 袋语义，重复边
  产生重复游走）；`enumerate_distinct` 在其上按 `(业务行, 循环标记)` 去重。
- 测试期望值同时来自**两处独立来源**：测试内手写的具体行/路径/多重集，以及
  该 oracle 的输出；二者必须一致。oracle 的答案不是调用被测实现得到的。

### 覆盖的图形态与断言要点

- **树**（1→2,3；2→4,5）：BFS 层序与手写 5 行完全相等；DFS 前序
  `1,2,4,5,3` 与 oracle 显式递归前序逐项相等；无循环标记。
- **多父菱形**（2→4 与 3→4 汇合）：
  - `UNION DISTINCT`：节点 4 仅 1 行，路径取首达 `[1,2,4]`，
    `rows_deduplicated=1`；
  - `UNION ALL`：保留两条游走，同业务行 4 配两条不同路径
    `[1,2,4]` / `[1,3,4]`；多重集与 oracle 相等。
- **自环**（1→1,1→2）：输出 `(1,[1])`、`(1,[1,1],cycle)`、`(2,[1,2])`；
  循环行被输出但不再展开，因此 `UNION ALL` 下也**不会无限膨胀**，状态为
  `complete`；ALL/DISTINCT 结果一致。
- **二节点环回根**（1→2→1，2→3）：回根行被标记 cycle 且路径 `[1,2,1]`；
  由于循环标记进入身份，`(1,false)` 与 `(1,true)` 各出现一次。
- **重复边**（两条 1→2、两条 2→3）：`UNION ALL` 下 2 出现 2 次、3 出现
  4 次（每条重复游走独立展开），与 oracle 多重集一致；`UNION DISTINCT`
  收敛为 3 行且 `rows_deduplicated=2`。
- **循环检测按声明键而非整行**：业务列为 `(id,label)`，声明键仅 `id`；
  2→1 带回与根不同的 label，仍必须以 `is_cycle=true`、路径 `[1,2,1]` 报告。
  若错误地按“整行（含路径）”判环则会漏判。
- **稳定遍历顺序可配置且确定**：乱序边下 BFS 排序为 `1,2,3,4,5`，
  `input` 保持边声明序 `1,3,2,5,4`，DFS 为前序；重复运行逐行一致。
- **限额返回明确不完整**：
  - `max_depth=1`（链下仍有子节点）→ HTTP 200 但
    `status=incomplete_max_depth, complete=false`，输出恰好含深度 0..=1 的行，
    trace 末轮 `basis` 明确写出被阻断的子行数；
  - `max_rows=2` → `incomplete_max_rows`，行数恰好为上限，trace 含
    “row limit” 判定依据。
- **Arrow2 类型化批次**：断言输出列为 arrow2 `PrimitiveArray<i64>` /
  `Utf8Array<i32>` / `BooleanArray`，列名 `["id","path","is_cycle"]`，
  并验证 `Chunk` 列数。

### 失败类别断言（异常/未知绝不统一返回成功）

`tests/validation.rs` 的 16 个用例对每类坏输入断言 HTTP 400 +
`outcome="error"` + `category="validation_error"`，且消息点名具体问题：
未知 union/order、键列不存在、空键列、类型不符（字符串/float 进 int64）、
行元数不符、投影引用未知边列、投影元数不符、父键列用常量、连接键类型不符、
辅助列名冲突、键列重复、限额超进程上限、零限额、未知 JSON 字段。
HTTP 层另测：畸形 JSON 体返回 400 validation_error；执行未开始时
`run_id=null`。

## 手动端到端验证（真实启动 + curl）

启动 `target/debug/recursive-cte`（系统分配空闲端口）后：

- `GET /health` → `{"status":"ok","version":"0.1.0"}`
- `tree.json` → `complete`，5 行，路径 `[1]..[1,2,5]`
- `diamond_union_all.json` → `complete`，5 行，节点 4 两条不同路径
- `self_loop.json` → `complete`，3 行，含 `[1,1], is_cycle=true`
- 深度超限链 → `outcome=ok, status=incomplete_max_depth, complete=false`，
  trace：`max_depth=1 reached; 1 child row(s) not emitted`
- 坏 JSON → HTTP 400，`outcome=error, category=validation_error, run_id=null`
- 服务 JSON 日志包含与响应相同的 `run_id`（关联可追溯），并含版本、bind、
  默认顺序与限额。

## 复现命令

```bash
# 默认 CARGO_HOME（无锁竞争时）
cargo test --locked
cargo clippy --all-targets -- -D warnings
cargo fmt --check
cargo run --locked

# 本次使用的隔离方式（规避同机 cargo 包缓存锁竞争）
CARGO_HOME=/home/admin/.cargo-opp464 cargo test --locked
RCE_BIND_ADDR=127.0.0.1:<free-port> ./target/debug/recursive-cte
```
