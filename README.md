# coaplab — 本地 CoAP 可确认请求与 Block1/Block2 分块子集

一个从零实现的、仅在本机运行的 **CoAP (RFC 7252) + 分块传输 (RFC 7959) 子集**：
Go 标准库网络栈、`crypto/*` 密码库、SQLite（纯 Go 驱动 `modernc.org/sqlite`）。
不连接任何真实设备，所有外部参与者与数据都由本地合成夹具或明确的本地依赖提供。

## 实现了什么

**消息层（RFC 7252）**

- 四种消息类型 CON / NON / ACK / RST，完整 4 字节头、Token(0–8B)、选项
  delta/length 扩展编码（13/14）、payload marker `0xFF`。
- **Message ID 与 Token 严格分层**：
  - MID 仅用于消息层——CON 重传去重（按 `(源端点, MID)`），服务器只处理首包，
    重传包重放已存响应，不重复执行业务。
  - Token 仅用于请求/响应关联；背载响应 Token 不符即拒绝。
  - 一次 CON 事务的所有重传**复用同一 MID**；Token 是独立的另一个值。
- 显式可配的重传参数：`ACK_TIMEOUT`、`ACK_RANDOM_FACTOR`（禁止 <1.0）、
  `MAX_RETRANSMIT`，指数退避 `t = ACK_TIMEOUT·2^n·rand(1,factor)`，
  超时后返回分类错误。

**分块（RFC 7959）**

- **Block1 上传（服务端，原子模式）**：中间块回 `2.31 Continue`，末块才提交一次；
  非零起点 / 非连续偏移 → `4.08 Request Entity Incomplete`；
  重复块**字节相同则幂等重放、绝不二次提交**，字节不同 → 拒绝；
  支持中途块大小协商（首块 1024、服务器反提议 32，后续按字节偏移重新编号 NUM）；
  Content-Format 漂移拒绝；部分上传有保留期，过期丢弃；超大 → `4.13` 并带更小 SZX 提示。
- **Block2 下载（客户端重组装）**：容忍乱序缓冲；块大小在块 0 收敛后保持一致；
  **ETag 绑定整份表示**——下载途中表示更新（ETag 变化）即整体拒绝
  `etag_changed`，绝不跨版本拼接；重复块幂等、重复失配拒绝。
- 保留 SZX=7（2048）在请求中按 RFC 返回 `4.00 Bad Request`。

**存储与版本**

- SQLite 中每个表示带 `version`（单调递增）与由
  `SHA-256(content-format ‖ body)` 前 8 字节计算的 ETag；PUT 原子替换并 bump 版本。

**诊断**

- 每条判定都带 remote / MID / Token、明确结论 `ACCEPT | REJECT |
  INDETERMINATE | IGNORE` 与可断言的失败类别（`gap`、`etag_changed`、
  `duplicate_mismatch`、`bad_block_size`、`request_incomplete`、…）。
- 负载内容从不落日志，只打长度（脱敏）；ETag 打掩码。

## 目录结构（模块各有真实职责）

```
cmd/coapd/                受控服务启动入口
cmd/coapget/              诊断客户端（GET 分块下载 / PUT 分块上传）
cmd/genfixtures/          生成确定性合成夹具
internal/
  wire/                   字节编解码（消息/选项/Block 选项），无 I/O
  ids/                    Token(crypto/rand) 与 MID 两个不同类型
  etag/                   表示版本 ETag
  transport/              消息层：UDP 收发、MID 去重缓存、退避重传、客户端
  blocks/                 Block1 装配器 + Block2 重组装器（纯状态机）
  store/                  SQLite 表示/版本/ETag
  engine/                 受控服务处理器 + 高层分块客户端
  service/                组装（store+assembler+engine+server+GC）
  diag/                   结构化、脱敏诊断
  config/                 启动配置（key=value，env 覆盖）
  fixtures/               合成夹具加载与确定性字节公式
configs/                  启动配置（生产默认 + 测试专用）
test/
  fixtures/data/          合成样例数据 resources.json
  harness/                UDP 损伤代理 + 原始报文探针 + 脚本服务器
  oracle/                 独立测试预言机（不 import 被测核心）
  integration/            端到端 / 兼容性 / 损伤场景测试
```

## 前置要求

- Go 1.22+（仅用标准库 + 一个纯 Go SQLite 驱动；**不需要 cgo**）。

## 快速开始

```bash
# 1) 生成合成夹具（确定性，可重复生成得到相同字节）
go run ./cmd/genfixtures

# 2) 启动受控服务（默认 127.0.0.1:5683，SQLite 文件 coaplab.db）
go run ./cmd/coapd

# 3) 另开一个终端，用诊断客户端取数据
go run ./cmd/coapget get /hello -show
go run ./cmd/coapget -szx 6 get /cd/3073b          # 4 个 1024B 块
go run ./cmd/coapget -szx 0 -file README.md put /demo   # 16B 小块上传
```

> 注意：Go 的 flag 必须写在子命令与位置参数**之前**
> （`coapget -addr … -file f put /path`），写在 `put` 之后不会被解析。

启动配置见 `configs/coaplab.conf`；测试用更快、更确定的
`configs/coaplab-test.conf`（ACK 超时 40ms、随机因子 1.0、保留期 250ms、
服务器首选 64B 块）。

## 运行测试

```bash
# 全部单元 + 集成测试，带竞态检测
go test -race ./...

# 聚合覆盖率（集成测试对内部包的覆盖也计入）
go test -race -coverpkg=./internal/... -coverprofile=cov.out ./...
go tool cover -func=cov.out | tail -1     # 总覆盖率
go tool cover -html=cov.out              # 逐行查看
```

测试覆盖的关键场景（均在本地 UDP 上制造，**不接真实设备**）：

1. **丢失确认**：代理丢弃首个 S2C ACK → 客户端以**同一 MID** 重传，
   服务器重放缓存响应、处理器仍只执行一次。
2. **全部 ACK 丢失**：客户端恰好发送 `MAX_RETRANSMIT+1` 份、MID 恒定，
   然后报超时。
3. **乱序块**：
   - Block2：0、尾块、中间块乱序取回，独立预言机缓冲并在补齐后拼出一致字节；
   - Block1：代理延迟块 0 使块 1 先到 → `4.08` 且不留半成品。
4. **块大小变更**：RFC 7959 图 7/图 9 的逐字节兼容向量；运行时 1024→32 协商。
5. **表示更新**：下载途中（代理钩子在第 3 块请求时）改写资源，
   客户端以 `etag_changed` 失败，不拼接新旧版本。
6. **网络复制**：代理复制上行 PUT，消息层去重保证处理器按唯一块数执行、
   存储只提交一次、终态字节正确。

## 独立测试预言机（关键）

`test/oracle` **不导入任何被测实现**：它用自己的最小报文解析器读字节、
自己重组装分块，并用两类独立参照判定：

- 硬编码 golden：`Hello, CoAP!` 的 SHA-256 由系统 `sha256sum` 独立算出后写死；
- 对 `cd/*` 夹具，**另写一份**夹具字节公式来生成期望正文。

因此“参考答案”不是由被测核心自己产生的；即使被测核心错了，预言机仍给出正确答案。

## 诊断输出示例

```
14:02:11.203 remote=127.0.0.1:54012 mid=0x9002 token=90 verdict=REJECT cat=gap block 2 offset 128 leaves gap (frontier at 64)
14:02:12.044 remote=127.0.0.1:54099 mid=0x6000 token=01 verdict=ACCEPT cat= GET /cd/300b block 0 szx=2 (64 bytes) etag=0xa1b…c3d version=1 m=1
```

## 非目标 / 子集边界

未实现：DTLS/DTLS-SRP、Observe(RFC 7641)、组播、Block1+Block2 同交换（图 10）、
资源发现 `.well-known/core`、动态查询。消息类型与方法限定在文档列出的集合。
