# smtpsink — 仅向本地测试邮箱落盘的 SMTP 服务

`smtpsink` 是一个**本地专用**的 SMTP 接收端（sink）：它讲 SMTP 协议、把接收到的
邮件持久化到本地磁盘（`.eml` 文件 + SQLite 索引），**从不向外转发或发送任何邮件**。
所有数据均为本地合成夹具，无需任何生产账号或真实业务数据。

## 支持范围

- 命令：`EHLO`/`HELO`、`MAIL FROM`、`RCPT TO`、`DATA`、`RSET`、`NOOP`、`VRFY`(252)、`QUIT`
- 每封邮件事务状态机显式：`connected → ready → mail → rcpt`，顺序错误一律 `503`
- `DATA` 点透明（dot-unstuffing）按字节状态机处理，与底层分块方式无关
  （测试覆盖逐字节投递的最坏分块）
- 多收件人**部分接受**：本地域收件人 `250`，外域 `550`，最终投递范围 = 已接受集合
  （重复 `RCPT` 每次均接受，落盘时按地址去重，只投递一次）
- **先落盘后确认**：`250 Queued as <id>` 仅在 fsync 文件、原子 rename、fsync 目录、
  SQLite 事务提交全部成功后才返回；任何一步失败返回 `451` 且不留残余文件
- 限制：行长（默认 1000 字节，不含 CRLF）、单封大小（默认 1 MiB，按解码后计）、
  每事务收件人数（默认 100）、并发会话数（默认 32，超限新连接得 `421`）
- 空反向路径 `MAIL FROM:<>`（退信）被接受

明确**不**支持（取舍）：STARTTLS、AUTH、PIPELINING 显式流水（逐命令处理，客户端
流水发送仍安全）、外发中继、8BITMIME 之外的扩展。本服务面向本地测试，不监听
公网；请只绑定 `127.0.0.1`。

## 关键取舍

- **SQLite 选 `modernc.org/sqlite`（纯 Go，无 cgo）**：可复现构建、跨平台，
  代价是体积略大。版本经 `go.sum` 锁定。
- **消息正文落 `.eml` 文件，索引放 SQLite**：正文不进数据库，便于直接 diff/查看；
  索引（发件人、收件人、大小、路径、时间）支持查询。两者在同一 `Commit` 中
  先后完成，任一失败即整体失败并清理。
- **重复 RCPT 在协议层接受、存储层去重**：协议层忠实记录每个被接受的 RCPT，
  投递范围（`recipients` 表主键去重）是最终语义。
- **坏点序列（如 `.x`）直接断连**：流无法重新同步时不回响应、关闭连接，
  避免把残余字节误解析为命令。
- **超长命令行**：读完该行再报 `500`，保持流同步，后续命令不受影响。

## 诊断与日志

每行日志带会话 ID（`sid=s-...`）、状态机位置、命令与决策原因，例如：

```
sid=s-e2953b37dae2 state=rcpt decision=reject cmd=RCPT to=m***@evil.example reason=non-local-domain
sid=s-e2953b37dae2 state=rcpt decision=accept cmd=DATA id=msg-1790... from=a***@example.test rcpts=2
```

邮箱地址本地部分始终脱敏（`a***@example.test`）；邮件正文永不进日志。
`decision=accept|reject|tempfail|reset` 与 `reason=...` 说明为何接受、拒绝或无法判定。

## 构建与启动

需要 Go 1.22+。依赖已锁定（`go.mod` / `go.sum`），可完全离线构建：

```sh
go build ./cmd/smtpsink
./smtpsink -config configs/smtpsink.json     # 默认监听 127.0.0.1:2525
```

配置项见 `configs/smtpsink.json`（监听地址、本地域列表、各项上限、存储目录）。
存储目录下生成：`sink.db`（SQLite 索引）、`messages/<id>.eml`（正文）、
`tmp/`（spool，正常时为空）。

## 示例会话

```
S: 220 smtpsink.local Service ready
C: EHLO client.test
S: 250-smtpsink.local greets client.test
S: 250-SIZE 1048576
S: 250 8BITMIME
C: MAIL FROM:<alice@example.test>
S: 250 2.1.0 Sender OK
C: RCPT TO:<bob@example.test>
S: 250 2.1.5 Recipient OK
C: RCPT TO:<mallory@outside.example>
S: 550 5.7.1 Relay denied: not a local domain
C: DATA
S: 354 End data with <CR><LF>.<CR><LF>
C: Subject: hi
C:
C: ..dot-leading line
C: .
S: 250 2.0.0 Queued as msg-1790835352476255443-1917d614f9884e5a
C: QUIT
S: 221 2.0.0 smtpsink.local closing connection
```

本地验证投递结果：

```sh
ls data/messages/
sqlite3 data/sink.db 'select * from messages; select * from recipients;'
```

## 模块划分

| 模块 | 职责 |
|---|---|
| `internal/lineio` | 字节编解码：CRLF 行读取（限长且保流同步）、DATA 点透明解码 |
| `internal/protocol` | SMTP 协议状态机、命令解析、回复码、地址脱敏 |
| `internal/storage` | 存储接口 + SQLite/文件实现（先持久化后确认） |
| `internal/server` | 受控服务：监听、并发会话上限、会话 ID、日志 |
| `internal/config` | 独立 JSON 配置加载与校验 |
| `cmd/smtpsink` | 可执行入口（信号处理、组装） |

## 测试

```sh
go test ./...            # 全部
go test -race ./...      # 竞态检测
go test -cover ./...     # 覆盖率（各包均 ≥80%）
```

覆盖的关键场景（均断言具体回复码与落盘结果，而非"接口能调"）：

- 重复 `RCPT`：每次 `250`，投递范围去重后只含一份
- 点开头行：`..x` 线上形式解码为 `.x`，逐字节分块亦正确
- `RSET`：事务清空，随后 `DATA` 得 `503`，新事务可正常完成
- 连接中断（DATA 中途）：无消息入库、spool 无残余文件
- 落盘失败（Begin/Commit/rename 注入故障）：`451`，无接受、无残余
- 部分收件人接受：外域 `550`，最终投递范围仅含本地域已接受地址
- 限制：超长行 `500`、超大消息 `552`、超收件人 `452`、超并发 `421`
- 兼容测试：`internal/server/testdata/` 中的**手写** golden 会话脚本
  （`session_basic.replies`）与期望落盘字节（`session_basic.eml`）对照真实
  TCP 会话逐字节校验；参考答案不由被测实现生成

最近一次本地运行：`go test -race -count=1 ./...` 全部通过，无失败、无跳过项。
