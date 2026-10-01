# sockswhitelist — 仅本地白名单目标的 SOCKS5 CONNECT 代理

用 Go 标准库网络/密码库与 SQLite（`modernc.org/sqlite`，纯 Go，无 CGO 运行时依赖）从零实现的一套**多模块后端**。代理只把 CONNECT 转发到**本地白名单**目标，并对协商、解析、拨号、转发和可解释性给出严格、有界、可核验的语义。

## 模块划分

| 包 | 职责 |
|---|---|
| `internal/wire` | SOCKS5/RFC1929 **字节编解码**：逐字节长度校验、IPv4/IPv6/域名、方法与应答帧 |
| `internal/proto` | **协议状态机**：方法协商 → RFC1929 认证 → CONNECT；每个终止条件映射为确定的失败类别 `Kind` |
| `internal/policy` | **SQLite 白名单、口令校验、请求日志**；域名 + CIDR 双重约束、PBKDF2-HMAC-SHA256 |
| `internal/gateway` | **受控出口**：解析—策略—拨号一致性，多地址顺序尝试；只拨已校验的 IP 字面量 |
| `internal/relay` | **有界转发**：逐字节复制、单向半关闭排空、字节预算与空闲预算 |
| `internal/server` | **受控服务**：连接信号量、请求 ID 关联、结构化日志、全程落库 |
| `internal/config` | YAML 配置与 fail-fast 校验 |
| `cmd/socksproxy` | `serve` / `adduser` 两个子命令 |
| `test/integration` | **独立黑盒测试 + 可复用夹具**：手写 RFC 字节客户端与第三方 `x/net/proxy` 客户端 |

## 快速开始

```bash
go build ./...
# 1) 配置（见 configs/proxy.yaml，所有预算必须显式给出）
# 2) 创建用户（口令不写命令行，取环境变量或 TTY 交互输入）
SOCKS_PROXY_PASSWORD='s3cret' go run ./cmd/socksproxy adduser \
  --config configs/proxy.yaml --name alice
# 3) 启动
go run ./cmd/socksproxy serve --config configs/proxy.yaml
```

一键验证（格式化、vet、单测、race、覆盖率阈值、构建、真实 CLI 冒烟）：

```bash
./scripts/verify.sh
```

## 核心安全语义

1. **认证协商失败即明确结束**：服务端只提供一种方法（0x00 或 0x02）。客户端不提供可接受方法时，回复 `05 FF` 后立即关闭，不进入后续阶段；错误口令回复 `01 01` 后立即关闭。
2. **地址长度严格校验**：IPv4 必须 4 字节、IPv6 必须 16 字节、域名长度 1..255 且标签语法合法；声明长度与实际不符一律拒绝。
3. **解析结果与地址策略一致**：域名请求必须同时满足“名字命中域名规则”**且**“解析出的**每个**地址都命中某条 CIDR 规则”；实际拨号只使用这些已校验的 IP 字面量（拨号串中绝不再出现主机名），消除 DNS-rebinding 与二次解析差异。
4. **半关闭语义**：一端读到 EOF 只对另一条腿的**对应方向**发 FIN（`CloseWrite`），已读缓冲先完整排空再发 FIN；反方向继续传输。
5. **连接与字节预算有界**：最大并发连接数（满则确定性拒绝）、握手/解析/拨号/空闲超时、每方向字节上限全部显式配置、超限即终止并归类。

## 失败类别（独立测试断言这些具体值）

`client_closed`、`malformed_frame`、`protocol_version`、`no_acceptable_method`、
`auth_subnegotiation`、`auth_denied`、`auth_backend_error`、`handshake_deadline`、
`unsupported_command`、`address_type_unsupported`、`policy_denied`、`resolve_failed`、
`dial_refused`、`dial_network_unreachable`、`dial_timeout`、`dial_failed`、
`relay_completed`、`relay_peer_reset`、`byte_budget_exceeded`、`idle_timeout`、
`relay_failed`、`internal_error`。

每个类别同时出现在：进程的结构化日志（`failure_category`）、SQLite `requests.outcome_kind`、SOCKS5 REP 字节。测试直接断言这些值，而不是“接口能调用”。

## 可解释性

每条连接分配不可猜测的 `request_id`，日志事件携带：`request_id`、`client_addr`、
`socks_version=RFC1928/v5`、`step`、`location`；失败事件单列
`failure_category`、`failure_reason`，以及无法由代理单独确定的 `uncertainty`
（例如外部解析器超时 vs NXDOMAIN、超时丢包 vs 对端过滤）。SQLite `requests`
表按请求记录协商阶段、目标、解析结果、逐地址尝试、字节计数与耗时。

## 边界与边界语义

见 [`docs/design.md`](docs/design.md)。其中明确列出：支持/不支持的 SOCKS5
特性、半关闭在各种对端组合下的行为、预算边界是否计入“恰好达到上限”的字节，
以及**无法在本环境执行、因此未被声称为已通过**的检查。
