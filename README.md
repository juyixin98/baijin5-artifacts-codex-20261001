# 本地 Modbus TCP 主从夹具 (Master/Slave Fixture)

一套**仅运行在本机回环**、无任何真实工业设备依赖的 Modbus TCP 夹具。
支持两个功能码：

- **0x03 Read Holding Registers（读保持寄存器）**
- **0x10 (16) Write Multiple Registers（写多个寄存器，原子提交）**

数据与外部参与者全部是本地合成的：回环 TCP、进程内寄存器库、本地 SQLite
审计文件、手算报文向量。**不会、也不能连接真实工业设备**（监听地址被强制
限制为 loopback）。

---

## 1. 目录结构与模块关系

这是一个 Go workspace（`go.work`），多模块，职责分离，依赖单向：

```
                         ┌──────────────┐
                         │   vectors    │ 零依赖：手算报文/异常向量(预言机)
                         └──────────────┘
                            ▲ 只被测试引用(不反向依赖被测代码)
        ┌───────────────────┼────────────────────┐
        │                   │                    │
  ┌───────────┐      ┌───────────┐        ┌────────────┐
  │   mbap    │      │   core    │        │  config    │
  │ MBAP字节  │      │ 协议状态机│        │ JSON配置   │
  │ 编解码/流 │      │ FC03/FC16 │        │ 校验/版本  │
  └───────────┘      └───────────┘        └────────────┘
        ▲                   ▲                    ▲
        │                   │                    │
  ┌───────────┐      ┌──────────────────────────────┐
  │  client   │      │           fixture            │
  │ 主站      │─────▶│ 受控从站: TCP服务+SQLite审计  │
  │ 事务ID    │ mbap │ +HTTP控制面 +并发worker池     │
  │ 多路复用  │      └──────────────────────────────┘
  └───────────┘                   ▲
        ▲                         │ 黑盒: 真实回环TCP
        └──────────┬──────────────┘
              ┌───────────┐
              │  compat   │ 独立兼容测试(另一个SQLite句柄直读审计)
              └───────────┘
```

| 模块 | 作用 | 关键文件 |
|------|------|----------|
| `vectors` | **独立预言机**：手算的 golden/异常/坏帧 hex 字面量与独立构造器，零第三方、零被测依赖 | `golden.go`, `malformed_*.go`, `builders.go` |
| `mbap` | MBAP 字节编解码与流式成帧（半包/粘包），只懂传输、不懂功能码 | `codec.go`, `reader.go`, `errors.go` |
| `core` | 纯 PDU 协议状态机 + 寄存器库；无网络/无 I/O，服务端与单测共用 | `engine.go`, `bank.go` |
| `config` | JSON 配置加载与强校验（loopback 强制、worker、单元绑定、schema 版本） | `config.go` |
| `fixture` | 受控从站：TCP server、有界 worker 池、SQLite 审计、HTTP 控制面、启动引导、二进制入口 | `server.go`, `audit.go`, `control.go`, `bootstrap.go`, `cmd/modbus-fixture` |
| `client` | 主站：**单连接事务 ID 多路复用**，乱序响应按事务 ID 正确投递；独立解析 PDU | `master.go`, `cmd/modbus-req` |
| `compat` | **黑盒兼容测试**：真实回环 TCP、独立字节解析、独立 SQLite 句柄 | `*_test.go` |

> 独立性的关键：`vectors` 不 import 任何被测包；`client` 只与从端共享 `mbap`
> 传输编解码，PDU 由各自构造；`compat` 通过真实 socket 和**另开的**
> `database/sql` 句柄读取审计库。因此参考答案不是由被测核心自己生成的。

---

## 2. 协议契约与算法假设

### 2.1 MBAP 头（Modbus TCP，大端）

```
事务ID(2) | 协议ID(2, 必须0x0000) | 长度(2) | 单元ID(1) | PDU(长度-1)
```

- **长度字段** = `1(单元ID) + PDU字节数`，合法范围 **[2, 254]**（PDU 最大 253）。
  长度 `<2` → `length_too_small`；`>254` → `length_too_large`。
- **事务 ID**：从端**原样回显**，绝不重映射；它是跨请求关联身份的唯一依据。
- **协议 ID**：非 0x0000 → `wrong_protocol_id`。
- **单元 ID**：在绑定表中查找；未绑定 → 异常 **0x0B（Gateway Target Failed）**。
- 流式读取由长度字段驱动：天然容忍**半包**（跨多次 TCP 读）与**粘包**
  （一次读到多帧）。一旦发生成帧失败（边界不再可信），**关闭该连接**，
  而不是猜测后续字节。半截帧被分类为 `header_truncated` / `body_truncated`
  （区别于对端干净 EOF）。

### 2.2 寄存器字节序与取值范围

- 每个寄存器是一个 **uint16，网络大端字节序**（高字节在前）。
- 合法取值即完整 uint16 域 **0x0000..0xFFFF**，因此不存在“值非法”的逐值拒绝；
  用 `0xFFFF/0x0000` 等边界值做写入向量。
- FC03 响应：`03 | 字节数(=2*qty) | 寄存器数据`。

### 2.3 数量约束（数量不符 → 异常 0x03）

| 功能码 | 数量范围 | 依据 |
|--------|----------|------|
| FC03 读 | **1..125** | 响应字节数 = 2*qty，须 ≤ 一个字节能表达的 250 |
| FC16 写 | **1..123** | `6 + 2*qty` PDU ≤ 253；qty=124 时 MBAP length=255 已**无法成帧** |

FC16 还要求：PDU 恰为 5 个固定字段 + 数据；**字节数字段 == 2\*qty**，
且帧内确实携带这么多数据，否则 `illegal_data_value (0x03)`。

### 2.4 地址越界（→ 异常 0x02）

- 单元绑定一个大小为 N 的寄存器库，合法地址 **0 .. N-1**。
- 判据：`startAddr + quantity <= N`（先于任何写入校验）。
- 夹具默认/测试库 N=125（地址 0..124）：`addr=124,qty=1` 合法；
  `addr=125,qty=1` 或 `addr=124,qty=2` → `illegal_data_address (0x02)`。

### 2.5 校验顺序（决定异常类别，固定且可解释）

1. 单元 ID 已绑定 → 否则 0x0B
2. 功能码受支持（仅 0x03/0x10） → 否则 **0x01 Illegal Function**
3. PDU 形状/数量/字节数 → 否则 **0x03 Illegal Value**
4. 地址跨度在库内 → 否则 **0x02 Illegal Address**

**不支持的功能返回异常码，而不是普通数据**：响应 PDU 首字节 =
`功能码 | 0x80`，第二字节为异常码（如 FC04 → `84 01`）。

### 2.6 跨请求不混包 & 写多寄存器原子

- 每连接一个读循环成帧，请求分发到**有界 worker 池**（`workers` 配置）。
- 响应写出用**每连接互斥锁**保证整帧不交错；每个响应严格带自己的
  事务 ID 与单元 ID。
- 因此同一连接上的响应**允许乱序到达，但身份必然正确**。`txn_jitter`
  延迟档位（`(事务ID % 8) * jitter_slot_ms`）用于确定性地制造乱序以验证。
- FC16 在**单个锁临界区**内整体替换一段：读返回的是该段替换前或替换后的
  完整一致快照，绝不会读到半写（撕裂）数据；任何校验失败都在写入前返回，
  失败的写不改动任何寄存器。

---

## 3. 可解释性：日志、审计与控制面

每条处理日志与审计行都关联请求身份并展示关键步骤：

- 日志（结构化 slog）：`version`、`conn_id`、`txn_id`、`unit_id`、`fc`、
  `addr`、`qty`、`exception`、`category`、`duration_us`、处理组件。
- SQLite 表 `requests`：事务 ID、单元 ID、功能码、地址/数量、异常码、
  **失败类别单列**、原始 PDU hex、连接 ID、时间戳、耗时；带
  `(conn_id, txn_id)` 关联索引。
- HTTP 控制面（仅 loopback）：
  - `GET /healthz` 健康与版本
  - `GET /version` schema 版本、监听、worker、延迟档位、单元绑定
  - `GET /stats` 连接数、成功数、分异常码计数、成帧失败数
  - `GET /audit?limit=n` 最新审计行
  - `GET /registers?unit=1` 当前寄存器快照（未绑定单元 404）

“不确定/失败”的结论与正常数据分开呈现：异常码与 `category`
（如 `illegal_data_address`、`body_truncated`、`wrong_protocol_id`）独立成列。

---

## 4. 依赖版本与运行要求

| 项 | 版本 | 说明 |
|----|------|------|
| Go | **go1.22.2**（linux/amd64 实测；`go 1.22` 指令） | workspace 模式 |
| `github.com/mattn/go-sqlite3` | **v1.14.52** | 唯一第三方依赖，CGO，需 gcc |
| C 编译器 | gcc 13.3.0（实测） | 供 go-sqlite3 编译 |
| 其余 | 仅 Go **标准库**（net、crypto/rand、database/sql、encoding、log/slog…） | |

> 本机环境的 `GOFLAGS=-mod=mod` 与 workspace 冲突；所有命令统一使用
> `-mod=readonly`（脚本里已 `export GOFLAGS=-mod=readonly`）。需要联网首次
> 拉取 go-sqlite3（经 `goproxy.cn` 实测可达），之后离线可重复构建。

---

## 5. 本地验证命令与预期判断

### 一键验证（推荐）

```bash
./verify.sh
```

依次执行：gofmt/vet → 各模块 `-race` 单测 → compat 黑盒 `-race -v`
（半包/粘包/数量不符/地址越界/同时读写/乱序身份）→ 覆盖率 → 真实二进制冒烟。
**成功标志：结尾打印 `ALL VERIFICATION STEPS COMPLETED`，且每一步 PASS/OK。**

### 手动分步

```bash
# 单元/协议测试（竞态检测）
(cd mbap && go test -race -count=1 ./...)
(cd core && go test -race -count=1 ./...)
(cd config && go test -race -count=1 ./...)
(cd client && go test -race -count=1 ./...)
(cd vectors && go test -race -count=1 ./...)

# 黑盒兼容测试（真实回环 TCP + 独立 SQLite 句柄）
(cd compat && go test -race -count=1 -v ./...)

# 多次重复，验证并发稳定性
(cd compat && go test -race -count=5 ./...)
```

### 真实进程端到端

```bash
go build -o /tmp/modbus-fixture ./fixture/cmd/modbus-fixture
go build -o /tmp/modbus-req      ./client/cmd/modbus-req
/tmp/modbus-fixture -config configs/fixture.json &      # 默认 127.0.0.1:5020 / 控制面 :15020

/tmp/modbus-req -addr 127.0.0.1:5020 -unit 1 read  -start 0 -qty 2
# 预期: [0]=0x1234 [1]=0x3344
/tmp/modbus-req -addr 127.0.0.1:5020 -unit 1 write -start 10 -val 0x0001,0x0002,0xffff
/tmp/modbus-req -addr 127.0.0.1:5020 -unit 1 read  -start 10 -qty 3
# 预期: 0x0001 0x0002 0xFFFF
/tmp/modbus-req -addr 127.0.0.1:5020 -unit 1 raw -pdu 0400000001
# 预期: exception fc=0x04 code=0x01（不支持功能，非普通数据）
/tmp/modbus-req -addr 127.0.0.1:5020 -unit 1 read -start 999 -qty 1
# 预期: exception fc=0x03 code=0x02（地址越界），进程退出码非 0
curl -s http://127.0.0.1:15020/stats
```

### 关键黑盒用例 → 具体结果（断言的是确定值与失败类别）

| 用例 | 输入 | 具体预期 |
|------|------|----------|
| golden 读 | `123400000006050300000002` | 回 `12340000000705030411223344` |
| golden 写 | `00010000000b0110000a00020400010002` | 回 `0001000000060110000a0002` |
| 半包 | 在第 1/3/6/7/8/11 字节处切分 | 仍回完整 golden 帧 |
| 粘包 | 两帧一次写入 | 两帧边界正确、事务 ID 各自对应 |
| 协议 ID 错 | `...0001...` | 无响应、关连接、审计 `wrong_protocol_id` |
| length=1 / 255 | 长度字段越界 | 关连接，类别 `length_too_small/large` |
| 半截帧 | 头 6 字节 / 长度声明 6 只给 2 字节 | 关连接，审计 `header_truncated/body_truncated` |
| 数量 0 / 126 | FC03 | `83 03` |
| 数量 124 | FC16（length=255） | 无法成帧，成帧层关连接 |
| 地址越界 | addr=125 / span 出界 | `83 02` / `90 02` |
| FC04/FC06 | 不支持 | `84 01` / `86 01` |
| 未绑定单元 | unit=0x07 | `83 0B` |
| 乱序身份 | txn_jitter 突发 16 读 | 到达可乱序，但每帧值 = `0x4000+地址`，事务/单元精确 |
| 同时读写 | 整段交替写 + 并发读 | 读段恒为全 0xAAAA 或全 0x5555，无撕裂 |
| 失败写原子 | addr=124 qty=2 | `90 02`，随后读 [120..124] 全部不变 |

### 覆盖率（库包口径，实测）

```
mbap 96.2%   core 87.6%   config 83.3%   client(库包) 86.5%
黑盒对 core/fixture/mbap/config 聚合: 81.8%
```

均达到/超过 80% 目标。`cmd/*` 是薄 I/O 入口，不计入库包口径。

---

## 6. 测试状态如实标记

- **已运行且通过**：在 go1.22.2 / linux-amd64 上，全部模块 `go vet` 干净、
  `gofmt` 干净；`go test -race -count=1` 全绿；compat `-count=5` 连续通过；
  真实二进制端到端冒烟通过；独立 SQLite 句柄审计读取通过。
- **未运行/不适用**：未在 Windows/macOS、非 amd64、或无 gcc（CGO 关闭）
  环境运行——这些环境未在本机验证；go-sqlite3 在 CGO_ENABLED=0 时不可用。
- 若网络无法访问模块代理，首次 `go build` 拉取 go-sqlite3 会失败；
  预置 `GOMODCACHE` 或配置可达代理后即可离线复跑。

## 7. 配置示例

见 `configs/fixture.json`。要点：`listen`/`control_listen` 必须是 loopback；
`sqlite_db` 不允许 `:memory:`（审计须落盘供检查）；`workers` 1..256；
`latency` 为 `none` 或 `txn_jitter`；单元 ID 不可重复。
