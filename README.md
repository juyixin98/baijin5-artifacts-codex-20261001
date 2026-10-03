# hpacklab — HPACK (HTTP/2 头部压缩, RFC 7541) 后端实现

可审查的 HPACK 编码/解码后端：静态表、动态表（按字节成本驱逐）、Huffman
编码、协议状态机、受控服务（连接隔离 + 可解释日志 + SQLite 审计），以及
基于 RFC 7541 附录 C 公开黄金向量和独立实现交叉验证的兼容测试。

## 模块关系

```
codec/    字节编解码：前缀整数(§5.1)、字符串字面量(§5.2)、Huffman(附录B)。
          无协议状态，只产出字节级错误类别。
table/    静态表(附录A, 61 项) + 动态表(§2.3)：条目成本 = len(name)+len(value)+32，
          驱逐最旧条目；组合索引解析（1..61 静态，62+ 动态）。
state/    协议状态机：Decoder/Encoder 各持一份动态表（每连接隔离）。
          负责表示法合法性（索引零/越界、尺寸更新位置与上限）、
          解压限制、失败后失同步（ErrDesync）、逐步事件日志。
service/  受控服务：连接生命周期（每连接独立 Encoder/Decoder）、
          请求身份（crypto/rand 16 字节）、可解释日志、
          SQLite 审计（步骤/结果/失败分行落库）。
compat/   兼容测试：RFC 7541 附录 C.3–C.6 手工转录黄金向量 +
          与 golang.org/x/net/http2/hpack 的互操作交叉验证。
cmd/hpdemo/ 本地端到端验证 CLI：多块会话、连接隔离、失败与失同步演示。
```

依赖方向：`codec ← table ← state ← service`；`compat` 与 `cmd` 只依赖上述包。
核心机制（整数/字符串/Huffman 编解码、表驱逐、状态机）全部由本仓库实现，
无任何硬编码演示数据替代。

## 算法假设与决策

- **整数解码**（§5.1）：值以 uint64 表示；延续字节导致位移 ≥64 或加法
  回绕时拒绝（`codec.ErrIntegerOverflow`）；缓冲区耗尽报
  `codec.ErrTruncated`。
- **Huffman**（附录 B）：解码用由码表构建的二叉 trie。终止规则按 §5.2：
  剩余位 >7 或非全 1（非 EOS 前缀）→ `ErrHuffmanPadding`；载荷中出现
  EOS 符号（256）→ `ErrHuffmanEOS`。编码仅在 Huffman 形式更短时使用。
- **动态表尺寸更新**（§4.2/§6.3）：只允许出现在头部块起始、任何头部字段
  表示之前；起始处连续多个更新合法（与 x/net 行为一致）。更新值超过配置
  上限（相当于对端宣告的 SETTINGS_HEADER_TABLE_SIZE）→
  `state.ErrSizeUpdateTooLarge`；位置非法 → `state.ErrSizeUpdatePlacement`。
- **驱逐**（§4.1/§4.4）：按条目字节成本 name+value+32 记账，插入/缩容时
  驱逐最旧条目；单条超过上限则清空表且不插入。
- **索引合法性**：索引 0 → `table.ErrIndexZero`；超出当前静态+动态表长度
  → `table.ErrIndexOutOfRange`。
- **失同步策略**：任何解码失败将该连接的 Decoder 标记为 broken，后续
  `Decode` 一律返回 `state.ErrDesync`，不再假设与对端表状态同步
  （对应 RFC 7540 §4.3 的连接错误语义）。
- **解压限制**（`state.Limits`）：头部列表总字节（每条 name+value+32 求和）、
  字段数、单个字符串字面量长度均可配置；默认 64 KiB / 128 条 / 16 KiB。
- **敏感字段**：编码端 `Field.Sensitive=true` 使用 "never indexed" 表示，
  不进入动态表；解码端如实上报 `Sensitive`。
- **并发**：单个连接的 Encoder/Decoder 非并发安全（与 HPACK 的有序性
  语义一致）；`Service` 的连接表本身有锁保护。
- **审计**：每个被处理的头部块获得一个请求 ID；每一步（含偏移、表示
  类型、表操作）与最终结果/失败类别写入 SQLite `audit` 表，
  失败单独以 `phase='failure'` 记录。

## 本地验证命令与预期判断

```bash
# 1. 全部测试（单元 + RFC 黄金向量 + x/net 互操作）
go test ./... -count=1
# 预期：codec/compat/service/state/table 全部 ok；cmd/hpdemo 显示 [no test files]

# 2. 覆盖率
go test ./... -count=1 -coverprofile=/tmp/cover.out && go tool cover -func=/tmp/cover.out | tail -1
# 预期：state=100%、codec≈94%、table≈83%、service≈81%，总计≈80%

# 3. 静态检查
gofmt -l .   # 预期：无输出
go vet ./... # 预期：无输出

# 4. 端到端演示（多块会话 + 连接隔离 + 失败/失同步 + SQLite 审计）
go run ./cmd/hpdemo -audit /tmp/hpdemo-audit.db
# 预期：逐请求打印 req=<id> conn=<id> phase=step|outcome|failure 日志；
# 末行 "RESULT: PASS"；退出码 0。失败类别（如 index-zero、desync）
# 单列在 "=== audit: N recorded failure(s) ===" 段落。
```

## 测试参考来源（非自证）

- `compat/vectors.go`：RFC 7541 附录 C.3–C.6 手工转录的公开黄金向量
  （含期望字段列表、动态表内容与字节尺寸）。
- `compat/crosscheck_test.go`：与独立实现 `golang.org/x/net/http2/hpack`
  互操作——黄金向量同时经 x/net 解码核对（防止转录错误），双方编码器
  输出交叉解码，覆盖多块会话与中途表尺寸变更。
- `codec/huffman_table.go`：规范性码表（附录 B），经 x/net 源码转录生成，
  并由 RFC C.4/C.6 黄金向量与互操作测试双向验证。

## 依赖版本

| 依赖 | 版本 | 用途 |
|---|---|---|
| Go 工具链 | go1.22（go.mod 声明 1.22） | 构建 |
| golang.org/x/net | v0.33.0 | 仅测试：独立参考实现交叉验证 |
| modernc.org/sqlite | v1.34.5 | 纯 Go SQLite 驱动（审计存储） |
| 标准库 net / crypto/rand | — | net.Pipe 传输测试 / 请求 ID |

## 测试状态（如实标记）

- 已运行并通过：`go test ./... -count=1`（codec、table、state、service、
  compat 全部 ok）、`go vet ./...`、`gofmt -l .`（无输出）、
  `go run ./cmd/hpdemo`（RESULT: PASS，退出码 0）。
- 未运行：竞态检测 `go test -race`（本环境未执行；状态机按设计为
  单连接串行使用）；模糊测试（未编写，但有 2000 例确定性伪随机
  垃圾输入的健壮性测试 `TestRandomGarbageNeverPanics`）。
- 曾失败并已修复：service 测试因 `:memory:` SQLite 连接池各连接库独立
  导致挂起（已限制单连接并修复）；C.5.3/C.6.3 测试夹具的期望表状态
  最初误写为 4 项/222 字节，按 RFC 原文更正为 3 项/215 字节
  （被测实现本身输出正确）。
- 评审说明：曾派出独立审查代理做 RFC 语义评审，因网关故障（HTTP 504）
  两次中断，未产出报告；随后由实现会话完成自查，修复了
  `table.Lookup` 在 32 位平台上超大索引截断可能误判为合法索引的
  隐患（先在 uint64 域比较再收窄），并补充了对抗性随机输入测试。
