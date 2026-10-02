# proc-diff — 本地合成 proc 快照的进程资源差分服务

采集本地合成的 proc 快照目录，按 **进程身份 = (PID, 启动代次)** 计算累计 CPU 增量，
维护进程树（含孤儿重挂历史），并把无法确定的区间如实报告出来。全部输入均为仓库内
的本地合成夹具，不依赖任何生产账号或真实业务数据。

## 模块划分（src/）

| 模块 | 职责 |
|---|---|
| `model.rs` | 运行模型：身份、快照、增量、退出/重挂事件、不可确定区间等共享类型 |
| `diff.rs` | 资源算法：累计计数回退的纯函数分类（前进 / 回绕 / 异常） |
| `engine.rs` | 采样状态机：按序应用快照，维护身份表、进程树、增量与不可确定区间 |
| `snapshot.rs` | 从本地合成 proc 目录采集快照（含 `IOERR` 部分读取失败模拟） |
| `store.rs` | 持久化：JSONL 日志追加与重放重建 |
| `diag.rs` | 诊断 HTTP 接口（Axum）：摄入、增量、进程树、不可确定区间查询 |
| `config.rs` / `main.rs` | 配置加载与服务入口 |

## 核心语义

- **PID 复用**：身份含启动代次。同一 PID 以新代次出现时，旧身份以 `pid_reused`
  退出，新身份记 `identity_reset`，**不继承旧计数**（delta 为 `null`）。
- **孤儿重挂**：父进程退出与子进程重挂都带 `(seq, at_ms)`，时间关系可对照；
  进程树查询同时返回当前森林与全部重挂历史。
- **计数回退**：同一身份计数回退时，若按 `counter_max` 回绕的增量
  `counter_max - prev + next + 1` 不超过 `wrap_max_plausible_delta`，判为
  `counter_wrap`（增量可恢复）；否则判为 `counter_anomaly`，区间进入不可确定
  报告，**不编造增量**。
- **部分读取失败**：stat 读取失败（夹具中为 `IOERR` 哨兵）的进程保持存活，
  不判退出；该区间记 `read_failure` 不可确定。完全缺席且非读取失败的进程才退出。
- **采样缺失**：seq 跳号时跨缺口增量仍按累计值给出，但类别标记 `sampling_gap`
  并进入不可确定报告（`delta_known=true` 表示累计值仍精确）。

## 依赖与版本

- Rust 1.98.1（edition 2021）
- axum 0.7、tokio 1、serde/serde_json 1、toml 0.8、uuid 1、tracing 0.1
- 开发依赖：tower 0.4、tempfile 3
- 锁定版本见 `Cargo.lock`

## 从干净目录复现

```bash
cargo build          # 构建
cargo test           # 全部测试（12 个：3 单元 + 9 集成）
```

### 运行服务

```bash
# 默认配置 config/default.toml（counter_max 为 i64 上限）
PROC_DIFF_CONFIG=config/default.toml ./target/debug/proc-diff
# 或使用演示配置（counter_max=1000，可观察到回绕）
PROC_DIFF_CONFIG=config/demo.toml ./target/debug/proc-diff
```

### 请求样例

```bash
# 摄入一份快照（从本地合成 proc 目录采集）
curl -X POST http://127.0.0.1:9485/v1/snapshots \
  -H 'content-type: application/json' -H 'x-request-id: demo-1' \
  -d '{"snapshot_dir": "fixtures/counter-regression/snapshots/0001"}'

# 也可内联快照 JSON：{"snapshot": {"meta": {...}, "procs": [...], "read_failures": []}}

curl 'http://127.0.0.1:9485/v1/deltas?pid=400'      # 按 PID 查增量
curl  http://127.0.0.1:9485/v1/tree                 # 进程树 + 重挂历史
curl  http://127.0.0.1:9485/v1/intervals/undetermined  # 不可确定区间
```

诊断说明：每个请求带 `x-request-id`（客户端提供或服务端生成 UUID），响应头回显；
接受/拒绝/无法判定都在响应与日志中带 `request_id`、seq 与类别原因。日志只记录
seq、计数与类别，不记录进程级负载与路径（脱敏）。

## 夹具（fixtures/）

每个夹具含 `fixture.json`（引擎参数）、`snapshots/NNNN/`（合成 proc 目录）和
**手写的** `expected.json` 参考答案（非由被测实现生成）。stat 行格式：
`pid ppid start_gen utime stime rss_bytes state`。

| 夹具 | 核验点 |
|---|---|
| `pid-reuse` | PID 快速复用：旧身份 `pid_reused` 退出，新身份 `identity_reset`，delta=null |
| `orphan-reparent` | 父退出 + 孤儿重挂到 init，重挂事件与退出事件时间戳一致 |
| `counter-regression` | 950→30 判 `counter_wrap`(delta=81)；800→100 判 `counter_anomaly`(delta=null) |
| `sampling-gap` | seq 2→4 跳号：delta=110 保留但标 `sampling_gap`，区间入报告 |
| `partial-read` | `IOERR` 进程不判退出、记 `read_failure`；缺席进程才退出；恢复后 delta=60 |

## 验收结果记录（2026-10-02，本机实际执行）

`cargo test`：**12 passed; 0 failed**

```text
running 3 tests  (diff 单元测试)                ... ok
running 3 tests  (tests/api.rs)                 ... ok
running 1 test   (tests/counter_regression.rs)  ... ok
running 1 test   (tests/orphan_reparent.rs)     ... ok
running 1 test   (tests/partial_read.rs)        ... ok
running 1 test   (tests/pid_reuse.rs)           ... ok
running 1 test   (tests/sampling_gap.rs)        ... ok
running 1 test   (tests/store_replay.rs)        ... ok
```

`config/demo.toml` 冒烟（实际输出摘录）：

```text
POST /v1/snapshots (0001) -> {"request_id":"demo-1","outcome":"accepted","seq":1,"procs":3,...}
POST /v1/snapshots (0002) -> {"request_id":"demo-2","outcome":"accepted","seq":2,...}
POST /v1/snapshots (0002 重复) -> HTTP 409, "reason":"DuplicateOrOutOfOrder { last: 2, got: 2 }"
GET /v1/deltas?pid=400 -> delta=81, category="counter_wrap"
GET /v1/intervals/undetermined -> 仅 pid 500, category="counter_anomaly", delta_known=false
重启服务后 -> 日志 "replayed snapshot journal replayed=2"，增量查询结果与重启前一致
```
