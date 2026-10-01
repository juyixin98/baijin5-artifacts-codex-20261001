# 验证说明

本文件区分**已经实际执行并通过**的检查与**本环境无法执行、因此未计为通过**
的检查。一键脚本 `scripts/verify.sh` 会在结尾再次明确打印 SKIP 项；SKIP
绝不显示为 PASS。

## 已实际执行并通过

环境：Linux x86_64，Go 1.22.2。

| 检查 | 命令 | 结果 |
|---|---|---|
| 编译（含 3 个二进制） | `go build ./...` | 通过 |
| 静态检查（内置） | `go vet ./...` | 通过 |
| 格式 | `gofmt -l .` | 无未格式化文件 |
| 全部单元 + 集成测试 | `go test ./...` | 通过 |
| 竞态检测 | `go test -race -count=3 ./...` | 通过，无 data race |
| 覆盖率门禁 | `go test -coverpkg=./internal/...` | **83.9%**（≥ 80%） |
| 真实二进制 E2E | `scripts/verify.sh` 第 4 节 | 全部通过（含 IPv6） |

### 端到端探针实际断言的具体结果（`cmd/smokeprobe`，独立客户端）

- IPv4 `CONNECT` 到白名单回声服务：观测到 `REP=0`，载荷**逐字节**回显一致，
  客户端半关闭后读到**干净 EOF**（方向化半关闭）。
- 域名 `echo.local` 经**静态离线解析**：`REP=0` 且回显一致。
- 白名单外 IPv4 `8.8.8.8`：`REP=0x02`（connection not allowed by ruleset），
  在拨号前拒绝（离线、不产生任何外部连接）。
- 白名单外域名 `evil.example`：`REP=0x02`。
- 错误用户名/密码：子协商状态 `0x01`，连接随后关闭，**未拨号**。
- 正确用户名/密码：状态 `0x00` 后 `CONNECT REP=0`。
- 强制认证但客户端只提供免认证：服务端回 `05 FF` 并关闭。
- IPv6 回环 `::1`：`REP=0` 且回显一致（本机具备可用 `::1`）。

探针以退出码区分失败类别（2 传输/用法、3 协商、4 认证、5 应答、6 转发），
因此脚本校验的是**确切类别**而非“程序能跑”。

### Go 测试覆盖的关键场景

- `internal/codec`：版本错、0 方法、IPv4/IPv6/域名确切长度、空/超长域名、
  未知 ATYP、错误 RSV、非 CONNECT 命令；逐字节分段读取器；成功/`0xFF`
  应答的确切十六进制字节。
- `internal/policy`：CIDR 边界（`127.255.255.255` 允许、`128.0.0.1` 拒绝）、
  域名多地址全本地允许、**夹杂一个公网地址即整体拒绝且不返回拨号候选**、
  精确/后缀匹配不误伤子串、DNS 失败与无记录两类。
- `internal/proto`：注入式拨号器断言各 Outcome/REP，以及“只拨策略放行地址、
  按序回落、拒绝目标零拨号”。
- `internal/relay`（真实 TCP 连接对）：单向 FIN 只关一个方向且对向仍可用、
  慢上游背压下 400KiB **全部排空后才 EOF**、字节预算写前封顶（恰好转发
  budget 字节后以 `byte_budget_exceeded` 终止）。
- `test/integration`：逐字节分段握手 + 逐字节转发、认证协商失败显式关闭、
  域名多地址回落且审计记录的是实际连接地址、白名单外靶机**接受连接数为 0**、
  连接数上限、预算端到端、日志按 `req_id` 关联。

## 本环境未执行（明确单列，不计为通过）

- **staticcheck / gosec**：环境中未安装。`verify.sh` 会打印 SKIP 并给出安装
  命令；如需，可运行
  `go install honnef.co/go/tools/cmd/staticcheck@latest` 与
  `go install github.com/securego/gosec/v2/cmd/gosec@latest` 后重跑。
- **对真实公网目标的连接测试**：刻意不做。白名单外目标（如 `8.8.8.8`、
  `evil.example`）在拨号前即被策略拒绝，测试以“应答 REP + 靶机接受计数为 0”
  验证“从不外连”，因此整套验证保持离线、确定性、可重复。
- **跨机 / 非回环网卡的半关闭行为**：测试使用本机回环 TCP。非 TCP 传输的
  `CloseWrite` 退化路径（`half_close_unsupported`）在代码中保留但未在真实
  网络上触发。
- **平台解析器（非静态表）的真实 DNS 路径**：测试与示例默认使用
  `resolver.hosts` 静态表以保证离线确定性；`StaticResolver.Fallback` 到
  `net.DefaultResolver` 的分支存在但未联网验证。
- **持久化高并发压测 / 崩溃恢复（WAL）**：SQLite 使用 WAL 与 busy_timeout，
  单连接串行化写入；已测规则原子替换与重开保留数据，但未做长时间高并发压测。

## 复现

```bash
./scripts/verify.sh            # 全部必需检查 + E2E，末尾单列 SKIP
go test -race ./...            # 仅竞态测试
go test -coverprofile=cov.out -coverpkg=./internal/... ./... && go tool cover -func=cov.out
```

依赖版本在 `go.mod` / `go.sum` 中固定：`modernc.org/sqlite v1.34.5`
（纯 Go，无 CGO）及其传递依赖。
