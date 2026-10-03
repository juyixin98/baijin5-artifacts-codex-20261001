# imapd — 本地夹具 IMAP 受限查询服务

一个纯后端、仅本机可用的 IMAP 子集服务：预置合成邮件数据，支持
`SELECT`、`FETCH`、`UID FETCH`、`STORE`/`UID STORE`（仅 `\Deleted`、`\Seen`，
为 EXPUNGE 模型服务）、`EXPUNGE`、`NOOP`、`CAPABILITY`、`LOGOUT`。
无生产账号、无真实业务数据、无网络外部依赖；所有邮件来自
`testdata/messages/` 下的确定性合成夹具。

## 构建、运行、演示

```bash
go build ./...                 # 构建（Go ≥ 1.22）
go test ./...                  # 运行全部测试（实际执行并报告结果）
./scripts/demo.sh              # 本地演示：起服务 + 脚本化客户端会话
```

手动运行服务：

```bash
go run ./cmd/imapd -addr 127.0.0.1:1143 -db /tmp/imapd.db \
    -seed testdata/messages -uidvalidity 20260105
# 另开终端：
go run ./cmd/imapdemo -addr 127.0.0.1:1143
```

`imapd` 参数：`-addr`（监听地址，`:0` 表示随机端口，日志打印实际地址）、
`-db`（SQLite 路径）、`-seed`（.eml 夹具目录，邮箱已存在则跳过）、
`-mailbox`（默认 INBOX）、`-uidvalidity`（0 = crypto/rand 随机）、
`-reset`（重置邮箱并签发新 UIDVALIDITY）。

## 依赖清单

| 依赖 | 版本 | 用途 |
|---|---|---|
| Go 标准库 `net` / `crypto/rand` / `crypto/sha256` | go1.22+ | TCP 服务、UIDVALIDITY/运行编号生成、身份校验 |
| `modernc.org/sqlite` | v1.60.1 | 纯 Go SQLite 驱动（无 cgo），邮件持久化 |

完整传递依赖见 `go.mod` / `go.sum`。夹具再生成：`testdata/make_fixtures.sh`
（同时用外部 `sha256sum(1)` 重建 `testdata/manifest.sha256` 参考值）。

## 工程结构与模块契约

```
cmd/imapd        服务入口（参数、播种、运行编号日志）
cmd/imapdemo     演示客户端（scripts/demo.sh 使用）
internal/errs    错误分类契约：input / state / resource / internal
internal/wire    字节编解码：命令行与 literal 帧、响应渲染、序列集展开、字节上限
internal/store   SQLite 邮箱：UID 身份、派生序列号、UIDVALIDITY、EXPUNGE 事件
internal/proto   协议状态机：命令分派、状态检查、错误→状态码映射、事件中枢 Hub
internal/server  受控服务：TCP 监听、每连接一个 Session、共享 Store 与 Hub
internal/fixture 夹具加载（testdata/messages/*.eml + flags.txt）
internal/testlog 测试审计日志（运行编号 + 中间状态 + 判断理由，JSONL）
```

模块间契约：跨边界错误一律为 `errs.Error{Cat, Op, Msg, Err}`；`Msg` 可上
线给客户端，`Err`（底层原因）只进服务端日志。`wire` 不保存协议状态；
`store` 是 seq↔UID 映射的唯一推导处（`List` 按 UID 升序赋序列号）；
`proto.Hub` 把删除与事件广播放在同一临界区，保证所有并发观察者看到
同一邮箱事件顺序。

## 核心语义

- **序列号随删除移动，UID 保持身份**：EXPUNGE 删除 `\Deleted` 邮件后，
  后续邮件序列号下移；每条 `* n EXPUNGE` 携带该邮件被删时刻的序列号。
  例：5 封邮件删除 seq 4（UID 4）后，UID 5 移到 seq 4。
- **UIDVALIDITY 变化使旧 UID 失效**：`store.Reset`/`Replace` 签发新
  UIDVALIDITY 并清空邮件；客户端重新 SELECT 时通过
  `* OK [UIDVALIDITY n]` 发现变化，旧 UID 查询返回空（UID 无匹配不是
  错误，与序列号越界的 NO 明确区分）。
- **命令标签关联响应**：每条带标签响应回显请求标签；同一连接内流水线
  发送的多条命令按序应答。literal 长度**按字节**计（UTF-8 多字节、
  NUL、CRLF 均为数据），同步 literal `{n}` 先回 `+` 再读恰好 n 字节。
- **只实现声明项**：未声明的命令、FETCH 数据项（ENVELOPE、BODY[段]、
  ALL/FAST/FULL 宏）、UID 子命令（COPY/EXPUNGE）、标志（\Flagged 等）
  一律显式拒绝（BAD），不静默忽略。

## 错误语义

| 类别 | 含义 | IMAP 响应 | 例子 |
|---|---|---|---|
| input | 客户端输入畸形或命中未声明项 | 带标签 `BAD`（读取阶段无法取标签时为 `* BAD`） | 未知命令、`{n+}` 非同步 literal、序列号 0、未声明数据项 |
| state | 输入合法但与协议/邮箱状态冲突 | 带标签 `NO` | 未 SELECT 就 FETCH、邮箱不存在、序列号越界 |
| resource | 超出资源上限 | 读取阶段 `* BYE` 并断开；执行阶段带标签 `NO` | 行 > 8192 字节、literal > 1 MiB、序列集展开 > 100000 |
| internal | 存储/计算失败 | 带标签 `NO internal error`（细节不外泄，进服务端日志） | SQLite 故障、响应写失败、panic（recover 后连接保持） |

判断入口：`internal/errs/errs.go` 的 `CategoryOf`；映射入口：
`internal/proto/session.go` 的 `errorResult`。

## 测试与可复核输出

```bash
go test ./...            # 全量
go test -race ./...      # 含竞态检测（并发事件顺序）
go test -v ./internal/server/   # 查看逐条运行编号与中间状态日志
```

关键用例（均断言具体结果与失败类别，而非“接口能调”）：

- `TestGoldenTranscript`：完整会话逐字节比对**手写**黄金文件
  `testdata/golden/transcript.txt`（期望值由夹具字节手工推导，非被测
  实现生成），覆盖删除导致的编号移动。
- `TestMessageIdentityByHash`：FETCH 载荷 SHA-256 对比外部
  `sha256sum(1)` 生成的 `testdata/manifest.sha256`。
- `TestUIDValidityChangeInvalidatesOldUIDs`：旧 UIDVALIDITY 下的 UID 5
  在重置后不可解析。
- `TestConcurrentObserversSeeSameExpungeOrder`：两个连接观察同一邮箱，
  双方事件流必须一致（`[1 1]`）。
- `TestBinaryAndUTF8Literals`：含 NUL 的 literal、6 字节 UTF-8 名按
  `{6}` 帧定界，且事后流不失步。
- `TestOversizedLiteralIsResourceError`：超限 literal → BYE + 断连
  （resource 类别）。
- `TestUnknownAndUndeclaredAreRejected`：逐条钉住 BAD（input）与
  NO（state）的边界。
- `TestFetchBodyPeekVsSeen`：`BODY[]` 置 `\Seen`，`BODY.PEEK[]` 不置。

**测试日志**：每个测试用例生成运行编号（时间戳 + crypto/rand 后缀），
关键中间状态与判断理由以 JSONL 追加到各包目录下
`testlogs/<测试名>.jsonl`（可用 `TESTLOG_DIR` 改位置），同一内容也进入
`go test -v` 输出。字段：`run`（运行编号）、`state`（中间状态）、
`reason`（判断理由）、外加用例特定键值（如 `events`、`uidvalidity`、
`category`），可按 `run` 重放定位问题。

## 与 RFC 3501 的已知偏差（有意为之）

- 无认证（无 LOGIN）：连接后直接进入可 SELECT 状态。
- 仅同步 literal；`{n+}` 拒绝（input 错误）。
- 引号字符串不支持反斜杠转义。
- 序列集展开上限 100000；行/literal 上限见上表。
- STORE 非 SILENT 时仅向本连接回显 FLAGS，不向其他观察者广播标志变更
  （EXPUNGE 事件仍全局广播）。
- 事件广播在 Hub 锁内同步写各连接：慢客户端会阻塞其他观察者的广播
  （本机演示场景可接受，生产需改异步队列）。
