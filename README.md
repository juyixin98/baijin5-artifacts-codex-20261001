# localstun — 受控本地 STUN Binding 服务与客户端（Go）

一个**仅用于受控 UDP 测试**的 STUN（RFC 5389）Binding 请求/响应实现，
配套独立的 Python 参考实现做双向交叉验证，并用 SQLite 留存可重放审计。

> 范围明确限定：只实现 Binding 方法。**不实现 TURN 中继、Allocation、
> Channel、Permission**，也没有真实业务账号。所有地址、密钥、报文均为
> 本地合成夹具（loopback、`192.0.2.0/24`/`198.51.100.0/24`/`2001:db8::/32`
> 等文档保留段）。

---

## 1. 快速开始

要求：Go 1.22+（纯 Go SQLite 驱动 `modernc.org/sqlite`，**无需 cgo**）；
交叉验证另需 Python 3（仅标准库）。

```bash
# 离线构建（依赖已用 go.sum 锁定）
GOPROXY=off go build ./...

# 全量测试：单元 + 竞争检测 + 覆盖率
GOPROXY=off go test -race ./...
GOPROXY=off go test -cover ./internal/... ./test/compat/

# 双向跨实现兼容测试（Go <-> 独立 Python 预言机 + RFC 5769 已知答案）
GOPROXY=off go test ./test/compat/ -v

# 一键端到端复现：起真实服务/客户端，跑正常与异常向量，写 SQLite 审计
bash scripts/run_e2e.sh
```

手工使用三个命令：

```bash
go run ./cmd/stund     -addr 127.0.0.1:3478 -key localstun-test-key -db results/audit.db
go run ./cmd/stunc     -server 127.0.0.1:3478 -key localstun-test-key -count 3
go run ./cmd/stuninject -server 127.0.0.1:3478 -mode unknown-required -expect error420
```

IPv6：服务端 `-addr [::1]:3478`，客户端 `-server [::1]:3478 -local [::1]:0`
（环境无 IPv6 loopback 时测试自动跳过）。

---

## 2. 工程边界（模块与数据/错误契约）

```
cmd/stund           服务端入口（UDP 监听、信号处理、SQLite 装配）
cmd/stunc           客户端入口（Binding 调用，JSONL 状态事件输出到 stderr）
cmd/stuninject      报文注入器（正常/异常 8 种模式，断言结果类别）
internal/stun       字节编解码：头部/属性/4字节填充/XOR地址/HMAC/FINGERPRINT
internal/stunerror  统一错误分类（input/state/integrity/exhausted/compute）
internal/client     客户端协议状态机：事务表、超时、源匹配、旧响应隔离
internal/server     受控服务端：纯协议 HandlePacket + 真实 UDP 收发
internal/audit      审计记录契约（Record/Sink），不依赖任何 DB 驱动
internal/store      SQLite 落库（唯一导入数据库驱动的包）
test/compat         跨实现兼容测试（驱动独立 Python 预言机）
test/compat/oracle  独立 Python STUN 参考实现（stdlib only，与 Go 零共享代码）
testdata/vectors    RFC 5769 官方十六进制夹具
scripts/run_e2e.sh  端到端复现脚本
results/            运行产物：audit.db、各类日志、summary
```

模块依赖方向单向向下：`cmd -> client/server -> stun/stunerror`，
`client/server -> audit`，只有 `store -> audit + sqlite 驱动`。
协议代码**不 import** 数据库驱动。

### 错误分类契约（`internal/stunerror`）

所有失败都带一个稳定的、可被测试断言的类别，而不是只匹配字符串：

| Kind         | 含义                                  | 典型触发                                   |
|--------------|---------------------------------------|--------------------------------------------|
| `input`      | 输入/报文格式错误                     | 截断、长度不符、魔数错、地址族错、前导位   |
| `state`      | 协议状态冲突                          | 迟到响应、无对应事务、ctx 取消、非请求报文 |
| `integrity`  | 完整性/必选属性冲突                   | HMAC 不符、FINGERPRINT 不符、未知必选属性  |
| `exhausted`  | 资源耗尽                              | 事务超时、事务表满、报文超长               |
| `compute`    | 密码/OS 计算失败                      | 随机源失败、携带 MI 但未提供校验密钥       |

成功为零值 `KindUnknown`（审计中显示为空/`ok`）。用 `stunerror.Of(err)`
取得类别；用 `errors.Is` 匹配 `stun.ErrBadCookie` 等具体哨兵原因。

### 关键协议语义

- **属性长度与填充**：长度字段只记值长度（不含填充），每个属性按 4 字节
  对齐，填充字节内容被忽略（用非零填充夹具专门验证）。
- **XOR-MAPPED-ADDRESS（RFC 5389 §15.2）**：端口异或魔数高 16 位；
  IPv4 地址异或魔数；IPv6 地址异或 `魔数(4) || 事务ID(12)`——因此 IPv6
  掩码真正依赖事务 ID（有用错事务 ID 解码必错的测试）。
- **MESSAGE-INTEGRITY（§15.4）**：成熟的 `crypto/hmac` + SHA-1，长度字段
  临时后移 24 字节参与计算；常量时间比较。
- **FINGERPRINT（§15.5）**：CRC-32(IEEE) 异或 `0x5354554E`，长度字段后移
  8 字节；只允许 FINGERPRINT 出现在 MI 之后。
- **未知必选属性**：属性类型 < 0x8000 且不认识 → 明确报错
  （`UnknownRequiredError`），服务端回 `420` + UNKNOWN-ATTRIBUTES；
  ≥ 0x8000 的未知可选属性被容忍并保留。

### 状态机三条强制性质（`internal/client`）

1. **事务超时**：超时返回 `exhausted`（测试校验 ~150ms 量级）。
2. **响应源匹配**：只有来自“所发送服务器的精确 IP+端口”的数据报能完成
   事务；伪造源端口的数据报被记为 `response_source_mismatch` 并丢弃。
3. **旧响应不能完成新请求**：按 96 位事务 ID 索引待办表，完成即删除；
   迟到/无主数据报记为 `response_unknown_transaction`，永不影响在途新请求。

---

## 3. 证据侧（如何证明实现正确）

证据分三层，且**参考答案不是由被测核心自己生成的**：

1. **RFC 5769 官方已知答案（KAT）**：`testdata/vectors/*.hex` 是 RFC 5769
   §2.1/§2.2/§2.3 的逐字节报文。Go 解码必须验证其 HMAC、FINGERPRINT，并
   还原 `192.0.2.1:32853` 与 `2001:db8:...:6677:32853`；XOR 地址编码逐字节
   等于 RFC 公布的 `0001a147e112a643` / IPv6 串。
2. **独立 Python 预言机双向对照**（`test/compat/oracle/stun_oracle.py`）：
   - Python 按 RFC 文本独立生成 14 条向量（IPv4、IPv6、非零填充异常、
     未知必选/可选、坏魔数、长度不符、截断、HMAC 篡改、FINGERPRINT 篡改、
     无密钥校验等）→ **Go 解码**，断言成功与否、失败类别、解码地址。
   - Go 生成 23 条向量（含全部 4 种填充余数、IPv6、篡改、错密钥、畸形）→
     **Python 独立解码判定**。两侧在成功/失败/类别/地址上必须一致。
   - 每个失败都断言**具体类别**（`input`/`integrity`/`compute`），不是
     “接口能调用”。
3. **真实受控服务端到端**（`scripts/run_e2e.sh`）：真实 UDP socket 上跑
   正常流量 + 8 种注入模式，每条都断言结果类别（`success`/`error401`/
   `error420`/`dropped`），并把含事务号、源、类别、原始十六进制报文的审计
   写入 SQLite，可按 run 重放。

独立测试覆盖的失败类别区分：输入错误（`input`）、状态冲突（`state`）、
资源耗尽（`exhausted`）、完整性失败（`integrity`）、计算失败（`compute`）。

---

## 4. 审计与可重放日志

服务端每个数据报写一行 `audit.Record`（见 `internal/audit`）：
`run_id`、单调 `seq`、时间戳、组件、事件、**失败类别**、事务 ID（hex）、
源/目的地址、判定理由、**请求->响应原始十六进制报文**。
客户端状态事件以同样字段以 JSONL 输出（`stunc` 的 stderr）。

用 run_id 即可从日志重放：看到的报文、当时的中间判定、为什么通过/拒绝，
全部可复核。SQLite 查询示例见 `scripts/run_e2e.sh` 尾部与
`docs/REPRODUCTION.md`。

---

## 5. 目录与产物

- 数据夹具：`testdata/vectors/`（RFC 5769 hex）、预言机内置合成向量。
- 调用示例：`examples/`（含样例输出）。
- 依赖锁定：`go.mod` / `go.sum`（`go mod verify` 可校验）。
- 复现文档：`docs/REPRODUCTION.md`；协议契约：`docs/PROTOCOL.md`。
- 运行结果：`results/`（`audit.db`、`e2e_*.log`、`e2e_summary.txt`）。

## 6. 明确不做的事

不实现 TURN 中继及任何 RFC 5766 机制；不做 STUN 鉴权的 nonce 往返（测试
服务端用对称预共享 HMAC 密钥）；不做 NAT 穿透部署、不连接任何外部服务器。
