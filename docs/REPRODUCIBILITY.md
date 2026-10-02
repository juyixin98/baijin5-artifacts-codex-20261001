# 复现指南（Reproducibility）

本文档给出从空目录复现本实验台的完整步骤、已记录的真实结果，以及如何解读证据。
所有构建与测试**完全离线**：依赖已 `vendor/` 锁定，Python 预言机仅用标准库。

## 1. 环境

| 组件 | 记录的版本 | 用途 |
|------|-----------|------|
| Go | go1.22.2 linux/amd64 | 实现 + 测试（需要 cgo，因为 go-sqlite3 是 cgo 包） |
| gcc | 13.3.0 | 编译 go-sqlite3 |
| Python | 3.12.3 | 独立预言机 / 跨语言测试（仅标准库） |
| SQLite | 随 go-sqlite3 v1.14.52 | 证据持久化 |

无需外网、无生产账号、无真实业务数据；所有流量都在 `127.0.0.1` / `::1` loopback。

## 2. 依赖锁定

- `go.mod` / `go.sum`：仅一个直接依赖 `github.com/mattn/go-sqlite3 v1.14.52`
  （纯标准库传递依赖）。
- `vendor/modules.txt` + `vendor/`：依赖源码已入库，用 `-mod=vendor` 构建，
  不访问任何模块代理。
- Python 侧零第三方依赖。

验证离线构建（可断网执行）：

```bash
CGO_ENABLED=1 go build -mod=vendor ./...
```

## 3. 一键复现

```bash
make fixtures   # 用独立 Python 预言机生成冻结夹具 test/testdata/fixtures.json
make check      # = fixtures + go vet + go test -race + 覆盖率
make demo       # 真实 loopback 运行（正常 + 异常）并导出证据
```

单步等价命令：

```bash
python3 test/oracle/stun_oracle.py fixtures test/testdata/fixtures.json
go vet -mod=vendor ./...
CGO_ENABLED=1 go test -mod=vendor -race -count=1 ./...
CGO_ENABLED=1 go test -mod=vendor -cover ./internal/...
./scripts/run_demo.sh
```

## 4. 已记录的测试结果（recorded run）

记录位置：`evidence/runs/recorded/`

- `go-test-verbose.txt`：`-race -v` 完整输出，**111 个子测试全部 PASS，0 FAIL**。
- `coverage.out`：覆盖率原始数据。

包级结果（核心库口径 `./internal/...`，语句覆盖 **89.4%**，均 ≥80%）：

| 包 | 覆盖率 | 主要内容 |
|----|--------|----------|
| internal/stun | 91.7% | 字节编解码、XOR、HMAC、错误分类 |
| internal/client | 90.8% | 事务表状态机、超时、源匹配、响应分类 |
| internal/server | 84.6% | 受控服务 200/400/420/静默丢弃 |
| internal/store | 89.2% | SQLite 落库、重开、关闭后错误 |
| internal/evidence | 83.9% | JSONL 顺序、error_kind、verdict |

> 注：若对整个模块 `./...` 统计，`cmd/` 两个命令行入口（flag/退出码胶水代码）
> 会把数字拉到约 78.7%；核心协议/状态机/服务/存储逻辑（`internal/`）为 89.4%。

质量门：`go vet` 无告警，`gofmt -l` 无输出，全程 `-race` 未报告数据竞争。

## 5. 已记录的真实运行（demo run）

`make demo` 在 `evidence/runs/demo-<UTC时间戳>/` 下产出：

- `transcript.txt`：完整终端回显；
- `stund4/db|jsonl`、`stund6/*`、`stunc*.{db,jsonl}`：服务/客户端证据；
- `*.stdout`：后台进程句柄。

最近一次记录的运行 `demo-20261002T033715Z` 关键判定（见其 `transcript.txt`）：

1. Go 客户端 → Go 服务端 IPv4：`endpoint=127.0.0.1:<port> attempts=1`，exit 0。
2. Go 客户端 → Go 服务端 IPv6：`endpoint=::1:<port>`，exit 0。
3. **错误密钥**：服务端静默丢弃，客户端重传 3 次后超时 exit 3；
   SQLite 中同一 txn 留下 **3 条 `integrity_failure`**（每次重传各一条）。
4. **无响应端口**：重传后超时 exit 3，客户端 JSONL `error_kind=timeout`。
5. Python 独立客户端 → Go 服务端（IPv4/IPv6）：各自 `{"ok": true, ...}`，
   反射端口等于自身临时端口。
6. Python 发送未知必须理解属性 → Go 回 `420` 且 `unknown_attributes=["0x0099"]`。
7. Go 服务端不签名、Python 强制验签 →
   `{"ok": false, "kind": "integrity_failure", "detail": "MI absent"}`，exit 2。
8. SQLite 聚合断言：观察到的类别恰为
   `['error_response','integrity_failure','success']`，脚本打印 `ASSERTIONS PASSED`。

### 手工复核 SQLite

```bash
D=evidence/runs/demo-20261002T033715Z
sqlite3 "$D/stund4.db" \
 "SELECT outcome, COUNT(*) FROM exchanges GROUP BY outcome;"
# error_response|1
# integrity_failure|3
# success|2
```

### 手工复核 JSONL（失败类别可区分）

```bash
cat "$D"/stunc*.jsonl | grep error_kind
# 超时用例为 "error_kind":"timeout"；
# 篡改/密钥不匹配的用例为 "error_kind":"integrity_failure"（见跨语言测试）。
```

## 6. 失败类别如何区分（需求点）

跨模块统一使用 `internal/stun/errors.go` 的 `ErrorKind`：

`input_error`、`unknown_critical_attribute`、`integrity_failure`、
`state_conflict`、`resource_exhausted`、`timeout`、`source_mismatch`、
`compute_failure`。

证据日志里每个错误事件都带 `error_kind` 字段；服务端 SQLite 的
`exchanges.outcome` 与之一一对应（`bad_request` / `integrity_failure` /
`error_response` / `success` / `dropped` / `timeout` / `source_mismatch`）。

## 7. 故障排查

- **cgo 相关报错**：确认安装了 gcc（`xcode-select --install` 或 build-essential）。
- **IPv6 用例被 SKIP**：当前环境未启用 `::1` loopback；IPv4 路径不受影响。
- **端口被占用**：`run_demo.sh` 使用 34478–34490，可在脚本顶部修改。
- **跨语言测试被 SKIP**：确认 `python3` 在 PATH 中（仅需标准库）。
