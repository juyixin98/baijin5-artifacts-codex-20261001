# ntpsim — 合成本地 NTP 偏移 / 往返时延 / 候选时钟选择后端

`ntpsim` 是一个纯本地、可复现的 NTP 采样与时钟选择后端。它合成带已知
时钟偏移、非对称链路时延、丢包、重复、时钟跳变的 NTP 源，按 RFC 5905
（SNTPv4）的四时间戳模型计算**偏移量**与**往返时延**，用不确定区间如实
表达单样本无法消除的路径非对称性，并用明确策略剔除陈旧样本、极端时延、
离群源与时间跳变，最后通过 Marzullo 交集算法选择候选时钟。

**本程序不修改系统时钟**：客户端"本地时钟"是虚拟时间，真实 UDP 路径也
只读取墙钟。

## 目录结构

```
internal/
  version/    构建与协议版本标识（写入日志）
  clock/      Clock 抽象、WallClock（只读）与 FakeClock
  protocol/   48 字节 NTP 报文按字节编解码、NTP 64 位定点时间戳
  core/       四时间戳数学 + 非对称不确定区间 + 候选选择引擎（纯函数）
  transport/  数据报抽象、客户端 FSM、受控服务端应答状态机、真实 UDP
  sim/        确定性虚拟时间网络（事件堆泵、非对称/丢包/抖动链路、源时钟）
  config/     JSON 配置加载与边界校验
  storage→run/SQLiteStore  纯 Go（modernc.org/sqlite，无 CGO）持久化
  run/        轮询编排、结构化关联日志、SQLite 存储
  harness/    真实回环 UDP 受控服务（兼容测试夹具）
cmd/ntpsim/   命令行入口
configs/      示例场景
test/compat/  真实 UDP 套接字兼容性测试
```

## 四时间戳约定（符号与单位固定）

| 戳 | 含义 | 时钟 |
|----|------|------|
| t1 (Origin)      | 客户端发送请求 | 客户端 |
| t2 (Receive)     | 服务端收到请求 | 服务端 |
| t3 (Transmit)    | 服务端发送应答 | 服务端 |
| t4 (Destination) | 客户端收到应答 | 客户端 |

```
前向腿   f = t2 - t1
后向腿   b = t4 - t3
偏移估计 θ = (f - b) / 2     // 正 = 服务端时钟超前客户端
往返时延 δ = f + b           // 恒等于 (t4-t1)-(t3-t2)
```

**非对称性无法从单样本消除。** 设真实单程时延 d_f,d_b ≥ 0、真实偏移 o：

```
f = d_f + o,  b = d_b - o,  d_f + d_b = δ
```

仅能推出 `o ∈ [θ - δ/2 - ε, θ + δ/2 + ε]`（ε 为本地精度/服务器根色散
附加不确定度）。本后端对每个样本返回该区间，而不是假装点估计无偏。
`internal/core/sample_test.go::TestAsymmetryBoundsTrueOffset` 枚举了全部
非对称分割逐一验证真实偏移始终落在区间内。

## 异常与失败类别（绝不统一返回成功）

样本级：`NEGATIVE_DELAY`（δ<0，重放/跳变/损坏）、`ZERO_TIMESTAMP`、
`KISS_OF_DEATH`（stratum 0 + kiss code）、`UNSYNCHRONIZED`（LI=3）。

传输级：`ErrTimeout`（含虚拟超时）、`ErrOnlyStaleReplies`（只收到被丢弃
的重放/迟到报文）、`ErrMalformedReply`、`ErrWrongPeer`、
`ErrOriginMismatch`（应答未回显请求的 origin 戳，RFC 5905 §8 必须丢弃）。

选择级：`NO_SAMPLES`、`ALL_STALE`、`INSUFFICIENT_SOURCES`、
`NO_INTERSECTION`（源分歧，区间无足够交集）、`CLOCK_JUMP`。

## 选择策略（可配置）

1. 样本有效性（样本级状态）
2. 陈旧：`CollectedAt` 早于 `now - max_sample_age` 拒绝
3. 极端时延硬门限：`rtt > max_rtt` 拒绝
4. 时间跳变：同源相对上一接受样本偏移移动 `> max_clock_jump` 拒绝
5. RTT 离群：新鲜样本 ≥3 时，`rtt > 因子 × 中位 RTT` 拒绝
6. Marzullo 最大一致交集
7. 在支撑该交集的源中，按 stratum 升序、RTT 升序选源

每个候选都带 `CandidateEvidence`（裁决 + 依据明细），写入日志与结果。

## 快速开始

```bash
go build ./...
go test ./...                 # 单元 + 仿真集成 + 真实 UDP 兼容测试
go test -race ./...           # 竞态检测

go run ./cmd/ntpsim -config configs/healthy.json
```

带持久化：

```bash
go run ./cmd/ntpsim -config configs/jump_and_disagree.json -db data/ntpsim.db
go run ./cmd/ntpsim -db data/ntpsim.db -query <打印出的 run-id>
```

## 日志可关联性

每行都带 `run=<随机 run-id> round=<轮次>`，并包含：

- 版本：`ntpsim_version` / `ntp_version`（run_start）
- 进度：`round_start`（含虚拟时间）、`round_synced` / `round_no_sync`
- 计算步骤：`sample` 行打印 f、b、θ、δ 与真实偏移区间
- 判定依据：`candidate` 行打印每个源的裁决与明细；异常以真实类别
  （`category=TIMEOUT`、`status=NEGATIVE_DELAY` …）记录

示例（非对称链路）：

```
event=sample source=src-a forward_leg_f=t2-t1=419.999999ms \
  backward_leg_b=t4-t3=-179.999999ms offset_theta=(f-b)/2=299.999999ms \
  rtt_delta=f+b=240ms true_offset_interval=[179.999989ms,420.000009ms]
```

## 配置

见 `configs/`。时长字段用 Go duration 字符串（`"150ms"`、`"2s"`）。

- `sources[].offset`：源相对真实时间的固定偏移；`drift_ppm` 线性漂移；
  `step_at` + `step_offset` 在指定虚拟时刻跳变。
- `links[]`：`to_server` / `from_server` 为**两个方向各自**的单程时延
  （这就是非对称的注入点）；`loss_rate` / `dup_rate` 为每方向概率。
- `run.link_seed`：固定随机种子，保证丢包/抖动逐字节可复现。

## 验收测试如何组织

- **独立手算预言机**：`internal/core/*_test.go` 的期望值全部由人工计算
  （对称 +250ms、服务端超前/落后符号、枚举非对称分割、负 δ、奇纳秒
  RTT 舍入），不调用被测实现生成参考答案。
- **协议字节**：`internal/protocol/packet_test.go` 手工拼装 48 字节并逐
  偏移断言。
- **极端场景**：`internal/sim/sim_test.go` 覆盖非对称偏置、100% 丢包
  超时、6 秒极端时延、800ms 时钟跳变、KoD、同种子复现。
- **策略裁决**：`internal/core/select_test.go` 断言具体裁决源与失败类别。
- **真实 UDP 兼容**：`test/compat/` 用 127.0.0.1 临时端口跑真实套接字，
  含重放服务器拒绝、无服务超时、KoD。

## 依赖锁定

纯 Go SQLite 驱动 `modernc.org/sqlite v1.34.5`（无需 CGO），版本固定在
`go.mod` / `go.sum`。标准库承担全部网络与字节编解码。

## 剩余限制

- 只实现 SNTPv4 的 48 字节头部（无 MAC/认证扩展、无 NTPv5）。
- 不做频率/漂移的长期滤波（NTP 的 loop filter / discipline）；输出是每轮
  区间与选源结果，不调整任何时钟。
- 单进程虚拟网络；真实 UDP 路径仅用于回环兼容性验证，不向公网发包。
- NTP 定点时间戳约 2^-32 秒精度，存在亚纳秒量化误差（测试用微计数容差）。
