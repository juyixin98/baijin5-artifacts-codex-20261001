# procdiff — 本地合成 /proc 快照的进程资源差分服务

对本地合成的 `/proc` 快照目录做进程资源差分分析：按**进程身份（boot 代次 + PID +
启动时间）**跟踪累计 CPU / RSS，识别 PID 复用、孤儿重挂、计数回绕、采样缺失与
部分读取失败，并通过 HTTP 诊断接口报告每个区间是**可确定**还是**无法确定**（含原因）。

## 运行模型

- 每个快照是一个目录：`<root>/<seq>/boot_id` + `<root>/<seq>/proc/<pid>/stat`
  （`/proc/<pid>/stat` 标准字段布局）。
- **进程身份 = (boot_id, pid, start_time)**。PID 被快速复用时，新一代以全新基线
  开始，旧代计数不被继承（区间分类 `reset`）。
- 父进程退出后子进程被重挂（如挂到 init），父链历史以 `ParentSpan{ppid, from_seq,
  to_seq}` 时间区间保存，可重建任意 seq 的进程树。
- 累计 CPU 回退区分：**reset**（换了代次，正常）、**wrap**（同代、回绕模数可解释、
  且在合理速率内）、**anomaly**（同代且无法解释，区间无法确定）。
- 部分读取失败（`proc/<pid>/` 存在但 `stat` 缺失/损坏）**不会**把进程判为退出；
  该区间记为 `partial_read`，基线保留，恢复后给出跨越缺口的诚实累计区间。
- 快照序号缺口（如 0002 之后是 0004）使跨越缺口的区间记为 `missing_samples`；
  缺口期间消失的进程退出时间记为不精确（`exact=false`）。

## 模块划分

| 模块 | 职责 |
|---|---|
| `src/model.rs` | 运行模型：进程身份、快照、stat 记录 |
| `src/snapshot.rs` | 文件系统快照加载与 stat 解析 |
| `src/delta.rs` | 资源算法：计数差分与回绕/异常分类（纯函数） |
| `src/engine.rs` | 采样状态机：代次、重挂、部分读取、退出、区间日志 |
| `src/tree.rs` | 进程树重建与父链时间线 |
| `src/store.rs` | 采样状态持久化（JSON，临时文件 + 原子改名） |
| `src/diag.rs` | 诊断记录（请求标识、接受/拒绝/无法判定、脱敏） |
| `src/api.rs` | Axum 诊断 HTTP 接口 |
| `tests/` | 独立组织的集成测试与合成夹具（`tests/fixtures/`） |
| `config/default.toml` | 独立配置 |

## 依赖与版本

- Rust 1.98.1（edition 2021），cargo 1.98.1
- axum 0.8、tokio 1（full）、serde 1 / serde_json 1、toml 1、thiserror 2、
  tracing 0.1 / tracing-subscriber 0.3
- dev：tempfile 3、tower 0.5（util）

精确版本见 `Cargo.lock`（已提交）。

## 复现步骤（从干净目录）

```bash
cargo build          # 构建
cargo test           # 全部测试：单元 + 集成（28 个）
```

启动服务：

```bash
PROCDIFF_CONFIG=config/default.toml cargo run
# 监听 127.0.0.1:9485，状态持久化到 ./data/state.json
```

## 请求样例

```bash
# 采集一组快照（目录下为编号的快照子目录）
curl -s -X POST localhost:9485/v1/snapshots/ingest \
  -H 'content-type: application/json' \
  -d "{\"path\": \"$PWD/tests/fixtures/pid_reuse\"}"

# 某 PID 全部代次的区间差分（class: ok/reset/wrap/anomaly/partial_read/missing_samples）
curl -s localhost:9485/v1/processes/2000/deltas

# 指定序号的进程树
curl -s 'localhost:9485/v1/tree?seq=4'

# 诊断记录：每条带 request_id、decision(accepted/rejected/indeterminate)、
# reason 与脱敏后的 key_state（comm 只出现 comm#<hash> 形式）
curl -s localhost:9485/v1/diagnostics

curl -s localhost:9485/healthz
```

## 测试夹具与断言要点

所有期望值均为按夹具文件手工计算的参考答案，不是由被测实现生成。

| 夹具 | 验证点 |
|---|---|
| `tests/fixtures/pid_reuse` | PID 2000 在 seq4 被新一代复用：差分序列精确为 `(1→2 ok 15)`, `(2→3 ok 20)`, `(3→4 reset)`, `(4→5 ok 10)`；旧代退出时间不精确 |
| `tests/fixtures/orphan_reparent` | 父 3000 退出后 3001 重挂到 pid 1：父链 `[(3000, 1..2), (1, 3..)]`，seq2 树在 3000 下、seq4 树在 1 下，子进程差分连续 |
| `tests/fixtures/counter_wrap` | 4000 回绕得 `(2→3 wrap 146)`；4001 回退 1500→400 判 `(2→3 anomaly)`，下一区间重新基线化后 `ok 200` |
| `tests/fixtures/missing_sample` | 缺 0003：跨越区间 `missing_samples`；6000 在 0004 stat 损坏 → `partial_read` 且**不判退出**，恢复区间诚实跨 `2→5` 计 150；6001 退出时间不精确 |

另有：重复/乱序快照拒绝（`tests/ingest_order.rs`）、状态持久化往返与不间断运行
逐字节一致（`tests/store_persistence.rs`）、HTTP 端到端含脱敏断言
（`tests/api.rs`，诊断输出中不得出现原始 comm 字符串）。

## 敏感数据处理

进程 comm 视为敏感：服务只存储/输出 `comm#<fnv1a64 前12位>` 脱敏散列；诊断与
日志不含原始命令名与宿主机绝对路径。
