# iosched-compare

本地块请求的 **deadline 与 SCAN 调度比较服务**。Rust + Axum + 文件系统持久化，
全部数据来自本地合成夹具，无需任何外部账号或真实业务数据。

## 边界声明（先读）

- **时间模型是合成的机械寻道模型，不是 SSD 实测。** 服务耗时 =
  `seek_base_ms + 磁道差 × seek_ms_per_track + 扇区数 × transfer_ms_per_sector`
  （磁道号 = `lba / sectors_per_track`）。所有参数可在请求中覆盖，结果可复现。
  任何“延迟”数字只在该模型内有效。
- 扇区地址为逻辑扇区号（LBA），范围 `[lba, lba + sectors)`，方向显式为读/写。
- 合并只发生在**同方向且扇区相邻**的请求之间；合并组完成时，每个原始请求
  身份各自记账（`merged_with` 字段列出同组成员），不丢不重。
- 仿真使用虚拟时钟（f64 毫秒），只随事件前进，完全确定。

## 模块划分

| 模块 | 职责 |
|---|---|
| `src/model.rs` | 运行模型：请求/取消/轨迹、设备耗时模型、输入校验与错误类别 |
| `src/scheduler/` | 资源算法：`deadline.rs`（防饥饿规则）、`scan.rs`（电梯）、`mod.rs`（合并） |
| `src/engine.rs` | 离散事件仿真引擎：到达→排队→下发→完成/取消，产生事件日志与指标 |
| `src/state.rs` | 持久化：`data/runs/<run_id>/report.json` + `events.jsonl`，索引 `data/index.json` |
| `src/api.rs` | 诊断接口（Axum 路由、错误语义、报告组装） |
| `src/config.rs` | 服务配置（`config/server.json` 可覆盖默认值） |
| `fixtures/` | 合成轨迹：顺序、随机、读写混合、边界取消 |
| `tests/` | 独立测试：手算参考值断言具体结果与失败类别 |

## deadline 调度器的防饥饿规则（可解释）

每次下发都会在事件日志里记录 `reason`，规则按优先级：

1. `starvation_guard:writes_starved>=N` — 连续 N 批读之后强制服务写队头。
2. `deadline_expired:<dir>_head expired at t=<deadline> (now=<now>)` —
   某方向队头已过期，优先服务；双向都过期时截止更早者优先。
3. `earliest_deadline:<dir> at t=<deadline>` — 都未过期时服务截止更早的队头。

注意：同方向内部是 FIFO（只看队头截止期），排在长请求后的紧截止请求会发生
队头阻塞——这是真实 mq-deadline 的语义，测试 `deadline_miss_count_reflects_actual_lateness`
专门验证这一点。

## 取消语义（已下发 ≠ 未下发）

| 结果类别 | 含义 |
|---|---|
| `cancelled_queued` | 请求还在队列中，成功移除，不会完成 |
| `rejected_in_flight` | 请求已下发到设备，无法撤回，将照常完成 |
| `rejected_terminal` | 请求已完成或已取消 |
| `rejected_not_arrived` | 取消时刻早于请求的到达时刻 |

## HTTP 接口

- `GET  /v1/health` — 健康检查（含时间模型免责声明）
- `POST /v1/runs` — 提交轨迹并运行对比。二选一：`trace`（内联）或
  `fixture_name`（`fixtures/<name>.json`）。可选 `schedulers`、`device`、`deadline` 覆盖。
- `GET  /v1/runs` — 运行摘要列表
- `GET  /v1/runs/:run_id` — 完整报告（指标、完成记录、notes）
- `GET  /v1/runs/:run_id/events` — NDJSON 事件日志（每个关键步骤一条，含请求身份与原因）

### 错误语义

统一错误体：`{"error": {"category", "message", "request_id?", "details?"}}`

| HTTP | category | 含义 |
|---|---|---|
| 400 | `malformed_request` | 请求体无法解析，或 `trace`/`fixture_name` 未恰给一个 |
| 404 | `run_not_found` | run_id 不存在 |
| 404 | `fixture_not_found` | 夹具不存在（含路径穿越尝试，名称仅允许 `[A-Za-z0-9._-]`） |
| 422 | `sector_out_of_range` | 扇区范围超出设备容量，`details` 逐条列出 |
| 422 | `empty_request` | 扇区数为 0 |
| 422 | `duplicate_request_id` | 轨迹内请求 id 重复 |
| 422 | `unknown_request` | 取消目标不在轨迹中 |
| 422 | `invalid_device_model` | 设备模型参数非法 |

校验一次返回全部错误（`details` 数组），不是遇到第一个就停。

## 复现步骤

```bash
cargo build
cargo test          # 12 个测试：引擎 8 + 接口 4，全部应通过
./scripts/demo.sh   # 启动服务，跑全部四个夹具并打印对比指标与下发原因
```

手动示例：

```bash
cargo run -- config/server.json &
curl -s -X POST http://127.0.0.1:18099/v1/runs \
  -H 'content-type: application/json' \
  -d '{"fixture_name": "random"}' | python3 -m json.tool
curl -s http://127.0.0.1:18099/v1/runs/run-000001/events
```

## 测试策略

- 参考值**手工按模型公式推算**（注释里给出算式），不从被测实现回抄；
  浮点断言用 1e-9 容差。
- 覆盖：顺序合并身份保留、随机轨迹两调度器的顺序与寻道成本、过期原因字符串、
  写饥饿保护、四种取消类别、不丢不重恒等式（全部夹具 × 两调度器）、
  校验失败类别、截止 miss 计数、HTTP 状态码与错误类别、报告持久化往返。
