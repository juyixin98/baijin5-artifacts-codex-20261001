# socks5d — 本地白名单 SOCKS5 CONNECT 代理

一个只允许连接**本地白名单目标**的 SOCKS5（RFC 1928）服务端，仅支持
`CONNECT` 命令。使用 Go 标准库网络与密码学原语，白名单与审计落在
SQLite（纯 Go 驱动 `modernc.org/sqlite`，无需 CGO）。

核心安全性质：

- **默认失败闭合（fail-closed）**：目标不在白名单即拒绝，拒绝发生在拨号之前。
- **解析结果与连接地址一致**：域名先解析、逐条地址过策略，代理只拨策略已放行
  的具体 IP:Port，绝不再做第二次域名解析，消除“检查/使用”间隙与 DNS 重绑定面。
- **半关闭方向保持**：一端 FIN 只关闭对应方向，并先排空待发送缓冲；对向继续。
- **有界**：单连接数有上限，每个转发方向有独立字节预算（写前封顶）。
- **可解释**：每条日志/审计记录带请求 ID、版本、处理位置、阶段，并把
  确定性失败与不确定结论分列。

## 目录结构

```
cmd/socks5d/        服务入口（加载配置、打开 SQLite、装配并启动）
cmd/echod/          本地半关闭回声靶机（离线、仅用于验证）
cmd/smokeprobe/     独立 SOCKS5 客户端探针（不引用任何内部包，用于 E2E）
internal/codec/     字节编解码：帧长度/版本/地址类型严格校验（RFC1928/1929）
internal/proto/     协议状态机：协商→认证→请求→策略→仅拨 vetted 地址→应答
internal/policy/    白名单策略与离线优先解析（域名多地址全部须在 CIDR 内）
internal/relay/     受控转发：方向化半关闭、排空、字节预算、空闲超时
internal/server/    受控服务：accept、连接数上限、请求关联、优雅关停、审计
internal/auth/      用户名/密码认证（crypto/subtle 常量时间比较）
internal/store/     SQLite：规则表 + 审计表
internal/config/    配置解析与边界校验
internal/logx/      结构化 JSON 日志
test/oracle/        独立参考答案（自行从 RFC 重写常量/编码，不引用被测代码）
test/fixtures/      可复用夹具（瞬时代理、临时 SQLite、回声/慢上游、逐字节写）
test/integration/   端到端兼容测试（经真实 TCP，用 oracle 产生期望字节）
configs/            示例配置
scripts/verify.sh   一键验证（静态/竞态测试/覆盖率门禁/真实二进制 E2E）
```

## 快速开始

```bash
# 1) 验证（静态检查 + race 测试 + 覆盖率门禁 + 真实二进制端到端）
./scripts/verify.sh

# 2) 直接运行（无认证）
go run ./cmd/socks5d -config configs/socks5d.json

# 3) 启用用户名/密码认证（凭据只从环境变量读取，不进配置文件、不硬编码）
SOCKS5D_USERNAME=alice SOCKS5D_PASSWORD=secret \
  go run ./cmd/socks5d -config configs/socks5d.json
```

日志默认以 JSON Lines 写到 stderr，可用 `-log path` 落文件。数据库路径由
配置中的 `database.path` 指定。

## 配置

见 [`configs/socks5d.json`](configs/socks5d.json)。要点：

- `rules`：白名单，两类。
  - `{"kind":"cidr","value":"127.0.0.0/8"}`：IP 字面量目标必须落在某条 CIDR 内。
    允许写主机位（`127.0.0.1/8`），加载时归一化为 `127.0.0.0/8`。
  - `{"kind":"domain","value":"echo.local","mode":"exact"}`：域名精确匹配。
  - `{"kind":"domain","value":"internal","mode":"suffix"}`：后缀匹配
    （`.internal` 及其子域；`notinternal` 不匹配）。
  - 域名目标除名称命中规则外，其**每一个**解析地址也必须落在白名单 CIDR 内。
- `resolver.hosts`：离线优先静态表；未命中的名称回落到平台解析器。
- `max_connections`、`byte_budget_per_connection`：连接与字节上界。
- 各 timeout 见 [`docs/BOUNDARIES.md`](docs/BOUNDARIES.md)。

## 测试设计（为什么不是“接口能调用就算过”）

- 期望的线上字节由独立的 `test/oracle` 依据 RFC **另行重写**生成，不经过
  被测的 `internal/codec`，因此“编码器一致”是两套独立实现的交叉验证。
- 测试断言**具体结果与失败类别**：确切 REP 字节、codec 的稳定 Reason 码、
  proto 的 Outcome、审计中的 reason、逐字节回显与确切字节计数。
- 覆盖分段握手（逐字节发送）、拒绝认证、域名多地址回落、单向 EOF、慢上游
  背压排空、白名单外目标“接受连接数为 0”、字节预算与连接上限。

边界与失败语义见 [`docs/BOUNDARIES.md`](docs/BOUNDARIES.md)；
如何解读验证结果与哪些检查未执行见 [`docs/VERIFICATION.md`](docs/VERIFICATION.md)。
