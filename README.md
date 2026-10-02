# cfs-sim — 单 CPU CFS 式公平调度仿真

对单逻辑 CPU 上合成任务集的 CFS 式公平调度建模：固定权重/虚拟运行时间公式、
有界唤醒放置、最小粒度与目标延迟的显式关系、阻塞时间不计入执行、运行时统计守恒。
所有输入均为本地合成夹具（`fixtures/*.json` 或内置场景），不依赖任何生产账号或真实业务数据。

## 简化范围（明确声明，非 Linux 完整复刻）

- 单逻辑 CPU，基于 tick 的虚拟时钟（默认 1ms/tick），不执行真实指令；
- 扁平任务列表：无 cgroup/组调度、无 SMP 负载均衡、无 nice→load 完整表（权重直接给定，`NICE_0_LOAD=1024` 仅作换算基准）；
- 无实时/截止期调度类，无优先级继承，无 autogroup；
- 阻塞用脚本化 `sleep` 阶段建模，非内核等待队列；
- 抢占在 tick 边界判定（量化误差 ≤ 1 tick，已计入等待上界）。

## 固定算法公式（`src/config.rs`）

| 量 | 公式 |
|---|---|
| 虚拟时间增量 | `Δv = Δexec · NICE_0_LOAD · 2^20 / weight`（定点，2^20 为缩放因子） |
| 时间片 | `slice_i = max(min_granularity, target_latency · w_i / Σw)` |
| 调度周期上界 | `P(n) = max(target_latency, n · min_granularity)` |
| 新任务放置 | `v = min_vruntime`（无奖励） |
| 唤醒放置 | `v = max(own_v, min_vruntime − wakeup_bonus_v)`，`bonus_v = wakeup_granularity · NICE_0_LOAD · 2^20 / weight` |

- **防无限抢占**：唤醒奖励有界（≤ `wakeup_granularity` 对应的虚拟时间），睡眠者不会被放到
  `min_vruntime − bonus` 之前，因此频繁唤醒的任务无法把现有任务无限期压下 CPU；
  新任务无奖励，到达风暴无法饿死后台任务（见 `tests/short_tasks.rs`、`tests/wakeup_placement.rs`）。
- **最小粒度与目标延迟关系**：当 `n ≤ target_latency / min_granularity`（默认 8/2=4）时，
  每个任务在周期内获得 `latency·w_i/Σw ≥ min_granularity`；超过阈值后周期拉伸为 `n·min_granularity`，
  等待上界相应放宽（`SchedulerConfig::period_bound_ms`）。
- **阻塞不计执行**：`sleep` 是独立状态，vruntime 仅在执行时前进；每 tick 每个已到达任务
  恰好处于 Running/Ready/Sleeping 之一，故 `exec+wait+sleep == 在系统内时间`，
  全局 `Σexec + idle == elapsed`（`src/metrics.rs` 守恒检查）。

## 模块关系

```
src/
  config.rs     调参 + 固定公式（slice/period/Δv/唤醒奖励）与配置校验
  task.rs       任务脚本状态机（Run/Sleep 阶段、重复次数、统计）
  runqueue.rs   按 (vruntime, 序号) 排序的就绪队列（BTreeMap ≈ CFS 红黑树）
  scheduler.rs  引擎：tick 循环、到达/唤醒/抢占/选路/记账/min_vruntime/采样
  metrics.rs    独立判定层：守恒、长期份额、等待上界适用条件、完成性（不复用引擎内部）
  scenario.rs   场景定义 + 5 个内置合成场景 + JSON 夹具加载与校验
  persist.rs    文件系统持久化：data/runs/<run_id>/{meta.json, report.json, samples.jsonl}
  diag.rs       Axum 诊断 API（错误分类：400/404/422/500，绝不把异常统一返回成功）
  main.rs       CLI：scenarios / run / serve
fixtures/       与内置场景一一对应的 JSON 夹具（tests/fixtures.rs 防漂移）
tests/          独立集成测试，参考值为手算常数，不由被测核心生成
```

## 依赖与版本

- Rust 1.98.1（edition 2021）；本 crate 版本 0.1.0
- axum 0.8、tokio 1（full）、serde 1 / serde_json 1、thiserror 2、uuid 1（v4）、tracing 0.1 / tracing-subscriber 0.3
- 开发依赖：tempfile 3、tower 0.5（util，用于 ServiceExt::oneshot 测试）

## 本地验证命令与预期判断

```bash
cargo test                       # 全部 28 个集成测试应通过（0 failed）
cargo test -- --nocapture        # 查看结构化判定日志：
                                 # [CFS-SIM-TEST] version=.. run=.. case=.. step=..
                                 #   expected=.. actual=.. basis=.. verdict=PASS|FAIL
cargo clippy --all-targets       # 0 警告

# CLI 运行单个场景（退出码 0 = 全部检查通过，1 = 有检查失败）
cargo run -- scenarios
cargo run -- run --scenario fair-weights-1-3 --data data
cargo run -- run --fixture fixtures/periodic-sleeper.json --data data

# 诊断 API
cargo run -- serve --addr 127.0.0.1:8080 --data data
curl -s 127.0.0.1:8080/healthz
curl -s -X POST 127.0.0.1:8080/api/runs -H 'content-type: application/json' \
     -d '{"scenario":"short-task-storm"}'
curl -s 127.0.0.1:8080/api/runs            # 列出历史运行
curl -s 127.0.0.1:8080/api/runs/<run_id>   # 报告（含每项检查的判定依据）
curl -s 127.0.0.1:8080/api/runs/<run_id>/samples
```

预期判断方式（关键手算参考值）：

| 场景 | 预期 |
|---|---|
| `fair-weights-1-3` | 份额 0.25/0.75（±0.02），exec≈1500/4500ms，idle=0，max_wait≤9ms |
| `equal-trio` | 各 exec≈1000ms，max_wait≤9ms（P=max(8,3·2)+1） |
| `periodic-sleeper` | exec=300、sleep=500、idle=500、elapsed=800、vruntime=300·2^20 |
| `short-task-storm` | idle=0，background.exec=1160，短任务响应≤12ms，全部完成 |
| `idle-trace` | elapsed=150、idle=100、busy=50，采样中 idle 单调不减 |

错误分类预期：未知场景 → 400 `unknown_scenario`；缺参数 → 400 `invalid_request`；
权重为 0 → 400 `invalid_scenario`；不存在的 run → 404 `not_found`；
超 tick 预算 → 引擎 `ExceededMaxTicks` / API 422 `run_failed`（落盘为 Failed 运行，不返回成功）。

## 测试日志与身份关联

每个断言先输出一行结构化日志再断言，包含：crate 版本、运行标识（`run=test-run` 或持久化
`run_id`）、用例、计算步骤、期望值、实际值、判定依据（basis）与判定结果。失败时日志仍在，
可直接 grep：`cargo test -- --nocapture 2>&1 | grep 'verdict=FAIL'`。

## 当前验证状态

- `cargo test`：28/28 通过（2026-10-02，rustc 1.98.1，本机 Linux x86_64）
- `cargo clippy --all-targets`：0 警告
- CLI 三个子命令与 API 全部端点：已在本机手动验证通过
- 未运行项：无（全部测试均已执行）；未通过项：无
