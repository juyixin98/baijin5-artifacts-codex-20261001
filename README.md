# h2svc — 本机 HTTP/2 帧处理服务

一个受控的、本机自包含的 HTTP/2（RFC 7540）帧处理服务。面向预定义的简单
请求集合，**不包含完整 HPACK**（仅实现静态表 + 字面量编码子集，拒绝
Huffman 编码，见「范围与限制」）。所有数据均为本地合成夹具，无外部账号
或真实业务数据。

## 工程分层

```
internal/frame     字节编解码层：9 字节帧头、帧类型/标志/错误码常量（纯字节，无状态）
internal/hpack     HPACK 最小子集：整数编码、静态表、动态表、字面量（无 Huffman）
internal/h2        协议状态机：连接/流两级错误、流状态迁移、流控窗口、
                   CONTINUATION 连续性、有界发送队列
internal/server    受控服务层：TCP 监听、读写超时、预定义路由、与状态机接线
internal/config    配置层：JSON 文件加载、默认值、显式校验
internal/journal   诊断层：SQLite 事件日志（run 身份、连接、帧、判定）
internal/compat    兼容测试：执行 fixtures/cases 中的原始帧夹具
cmd/h2svc          服务入口
cmd/h2cli          预定义请求客户端（无需外部工具即可发样例请求）
fixtures/          gen_fixtures.py（独立 Python 参考实现）+ 生成的用例
```

## 行为契约实现位置

1. **连接级与流级错误分离** — `internal/h2/errors.go`：`ConnError`（致
   GOAWAY + 连接关闭）与 `StreamError`（致 RST_STREAM，连接存活）是两个
   独立类型。流状态机（idle/open/half-closed/closed）迁移见
   `conn.go`/`stream.go`，半关闭迁移由夹具 `half_close_transitions` 核验。
2. **SETTINGS 窗口下调可为负** — `conn.go handleSettings` 按 RFC 7540
   §6.9.2 把 delta 应用到所有活跃流的发送窗口；`flushStreamLocked` 在窗
   口 ≤ 0 时不发送任何 DATA。夹具 `window_shrink_negative` 核验
   -65435 → 0 → 正值恢复的完整过程。
3. **帧长与 CONTINUATION 连续性** — `conn.go HandleFrame` 入口统一校
   验：帧长 > SETTINGS_MAX_FRAME_SIZE 为连接级 FRAME_SIZE_ERROR；头块未
   完成时只允许同流 CONTINUATION，否则连接级 PROTOCOL_ERROR。夹具
   `frame_size_exceeded`、`continuation_interleave`、
   `continuation_wrong_stream`、`stray_continuation`、`continuation_valid`
   覆盖。
4. **发送队列有界** — `outq` 为固定容量 channel；控制帧入队失败即判连
   接不可写，DATA 帧入队失败则保留在流级 pending 缓冲（同样有界），由
   `FlushAll` 在写出后重试。单测 `TestSendQueueBounded`、
   `TestPendingBoundExceeded` 与端到端 `TestEndToEndSlowConsumerNoDeadlock`
   核验。

## 依赖与版本

- Go **1.26**（`go.mod` 声明；旧版 Go 在 `GOTOOLCHAIN=auto` 下会自动解析
  该工具链）。标准库提供网络与密码相关能力。
- Python **3.10+**（仅用于生成测试夹具，不参与运行）。
- SQLite 驱动：`modernc.org/sqlite v1.60.1`（纯 Go，无需 cgo），见
  `go.mod`/`go.sum`。
- 预定义路由：`GET /`（200, "hello h2\n"）、`POST|GET /echo`（回显请求
  体）、`GET /large`（200, `large_body_bytes` 字节确定性内容）、其余 404。

## 从干净目录复现

```bash
# 1. 生成测试夹具（独立 Python 参考实现，期望值不来自被测 Go 代码）
python3 fixtures/gen_fixtures.py

# 2. 全部测试：单元 + 夹具兼容 + 端到端
go test ./...

# 3. 查看带 run 身份与判定依据的详细诊断日志
go test ./internal/compat/ -run TestFixtures -v

# 4. 覆盖率（聚合内部包）
go test -coverpkg=./internal/... -coverprofile=/tmp/cover.out ./...
go tool cover -func=/tmp/cover.out | tail -1

# 5. 启动服务（配置见 config.example.json，各字段含义在 internal/config）
go run ./cmd/h2svc -config config.example.json -run-id demo-run-1

# 6. 样例请求（另开终端）
go run ./cmd/h2cli -addr 127.0.0.1:8080 -path /
go run ./cmd/h2cli -addr 127.0.0.1:8080 -path /echo -body 'ping-123'
go run ./cmd/h2cli -addr 127.0.0.1:8080 -path /large
go run ./cmd/h2cli -addr 127.0.0.1:8080 -path /nope   # 404

# 7. 检查诊断日志（SQLite）
python3 - <<'EOF'
import sqlite3
db = sqlite3.connect('h2svc-journal.db')
print(db.execute('select run_id, version, go_version from runs').fetchall())
for r in db.execute('select conn_id, direction, frame_type, stream_id, detail, decision from events limit 20'):
    print(r)
EOF
```

## 验证材料说明

- **夹具独立性**：`fixtures/gen_fixtures.py` 是独立的 Python 参考实现，
  所有期望帧、窗口值（如 -65435）、错误码均由 RFC 7540/7541 常量在
  Python 侧计算，不由被测 Go 代码生成。夹具以 JSON 形式提交在
  `fixtures/cases/`，可用 `python3 fixtures/gen_fixtures.py` 重新生成。
- **断言具体性**：每个用例逐步断言出站帧的类型/标志/流 ID/长度/错误码/
  负载字节，以及流状态、发送窗口值、pending 字节数和连接关闭状态；失败
  类别（连接级 vs 流级）分别断言。
- **无死锁**：每个夹具用例在 15 秒看门狗下运行；
  `TestEndToEndSlowConsumerNoDeadlock` 用慢消费者 + 小队列验证背压不会
  死锁。
- **诊断关联**：测试日志每行带 `run_id`、用例名、步骤号、输入帧摘要与
  判定依据（`judgment=...`）；运行期事件写入 SQLite（run 身份、版本、
  Go 版本、配置快照、逐帧决策）。异常与未知状态一律显式报错，不静默
  成功。

## 范围与限制（有意为之）

- HPACK 仅静态表 + 动态表 + 字面量（无 Huffman）；收到 Huffman 编码串以
  连接级 COMPRESSION_ERROR 拒绝。
- 仅 prior-knowledge 明文 HTTP/2（本机受控服务），无 TLS、无 ALPN。
- 响应头块必须单帧装下（预定义响应很小）；不支持 PRIORITY 调度（帧校验
  后忽略）、不支持 PUSH_PROMISE。
- 接收方向不主动发 WINDOW_UPDATE（受控简化，便于流控越界测试）；连接接
  收窗口耗尽后按 RFC 以 FLOW_CONTROL_ERROR 拒绝。

## 本次交付的验证结果（如实记录）

在 go1.26.0 linux/amd64 上执行：

- `python3 fixtures/gen_fixtures.py`：生成 16 个用例。
- `go test ./...`：7 个测试包全部 PASS（compat 16 用例、h2 状态机单测、
  frame/hpack 编解码、config、journal、server 端到端）。
- 聚合覆盖率（`-coverpkg=./internal/...`）：**82.0%**。
- 冒烟：`h2svc` + `h2cli` 实测 `/`（200, 9B）、`/echo`（回显 8B）、
  `/large`（200, 200000B，客户端发 WINDOW_UPDATE 后完成）、`/nope`
  （404）；SQLite 日志含 run 记录与逐帧事件。
