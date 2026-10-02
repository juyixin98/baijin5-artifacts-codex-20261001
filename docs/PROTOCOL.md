# 协议子集、模块边界与错误契约

本文档固定实现的协议范围、模块间数据契约与错误分类，便于审查与复用。

## 1. 实现的 STUN 子集（RFC 5389 / RFC 8489）

| 元素 | 状态 |
|------|------|
| 20 字节消息头（type / length / magic cookie `2112A442` / 96-bit txn） | ✅ |
| Binding Request / Success / Error（type `0x0001/0x0101/0x0111`） | ✅ |
| TLV 属性：`type(2) length(2) value(length) [0-3 字节零填充]` | ✅ |
| `XOR-MAPPED-ADDRESS`（IPv4/IPv6） | ✅ |
| `MAPPED-ADDRESS`（legacy，兼容） | ✅ |
| `ERROR-CODE`、`UNKNOWN-ATTRIBUTES`（420） | ✅ |
| `SOFTWARE`（comprehension-optional） | ✅ |
| `MESSAGE-INTEGRITY`（HMAC-SHA1，短期共享密钥） | ✅ |
| `FINGERPRINT`（0x8028）发送 | ❌（仅在"已理解属性"白名单中，不发送） |
| 长期认证（USERNAME/REALM/NONCE/MD5 key） | ❌（实验室用短期对称密钥） |
| **TURN 中继（Allocate/ChannelData/Send/Data 指示）** | ❌ **明确不实现** |
| DNS 发现 / NAT 行为判定流程 | ❌ |

### 关键正确性规则

- **填充**：`length` 字段是属性值的**未填充**字节数；属性整体按 4 字节对齐，
  填充字节必须为零；`解码时若声明的填充越过消息尾部，判 `input_error`。
- **消息长度**：头部 `message length` 必须恰好等于 datagram 减去 20 字节，
  多一字节少一字节都判 `input_error`（防止旧响应/截断混入）。
- **XOR-MAPPED-ADDRESS**（RFC 5389 15.2）：
  - port XOR `magic cookie >> 16`（`0x2112`）；
  - IPv4 地址 XOR 4 字节 magic cookie；
  - IPv6 地址 XOR `magic cookie(4) || transaction id(12)`。
  - 用 RFC 5769 §2.3/§2.4 的固定向量做已知答案校验。
- **未知必须理解属性**：type 最高位为 0（`< 0x8000`）即 comprehension-required；
  服务端不认识时回 `420 Unknown Attribute` 并附 `UNKNOWN-ATTRIBUTES`。
  type ≥ `0x8000` 的可选属性（如 SOFTWARE）忽略。
- **MESSAGE-INTEGRITY**（RFC 5389 15.4）：
  - HMAC 输入 = 到 MI 属性末尾为止的整段，头部 length 改写为覆盖 MI，
    **MI 的 20 字节 value 槽在计算时置零**（tag 不能覆盖自身）；
  - MI 必须是最后一个属性且 value 长度恰为 20；
  - 验签失败按 RFC **静默丢弃**（服务端不回错），但证据库记录
    `integrity_failure` 以便重放。

## 2. 模块边界与数据契约

```
cmd/stunc ──▶ client.Client.Bind(ctx, serverAddr) (*Result, error)
cmd/stund ──▶ server.Server.Serve(ctx)
                     │
                     ▼
        internal/stun （纯函数式编解码 + HMAC，无 I/O）
                     │
   client / server ──┴──▶ evidence.Logger（JSONL）+ store.Store（SQLite）
```

- **`internal/stun`**：无 goroutine、无网络/文件 I/O。核心类型：
  - `Message{Method, Class, TransactionID, Attributes, Raw}`；
  - `Attribute{Type, Value}`（Value 不含填充）；
  - `Address{IP net.IP, Port int}`；
  - `TransactionID [12]byte`；`ErrorCode{Code, Reason}`。
  - 所有跨模块错误都是 `*stun.Error`。
- **`internal/client`**：
  - `TransactionTable`：`Add → (<-chan *inbound, error)`、`Take`、`Deliver →
    "matched"|"source_mismatch"|"stale"|"malformed"`。
  - 完成条件（二者同时满足才投递，且**只投递一次**）：
    1. datagram 的 96-bit txn 等于未完成请求；
    2. datagram 源 `host:port` 等于请求目标。
  - 超时后条目即删除——迟到的旧 txn 响应落入 `stale`，无法完成新请求。
  - 表满 → `resource_exhausted`；txn 重复登记 → `state_conflict`。
- **`internal/server`**：每包一个处理分支，结果落 `exchanges` 表：
  `success` / `error_response(400|420)` / `integrity_failure`（静默）/
  `bad_request` / `dropped`（非 request class）。
- **`internal/store`**：`database/sql` 的薄封装，三张表
  （`runs`、`events`、`exchanges`），所有写错误都包裹操作名返回。
- **`internal/evidence`**：`run_id`（含组件前缀+UTC 纳秒时间+随机）+ 单调 `seq`；
  `Info/Warn/Error(event, kind, fields)` 与 `Decision(name, passed, reason, fields)`。

### SQLite schema

```sql
runs(run_id PK, component, started_at, note)
events(id PK, run_id, ts, seq, component, level, event, fields_json)
exchanges(id PK, run_id, ts, txn_id, remote_addr, family, outcome,
          mapped_ip, mapped_port, error_code, detail)
```

## 3. 错误契约（`stun.ErrorKind`）

| Kind | 触发点示例 | 调用方处置 |
|------|-----------|-----------|
| `input_error` | 截断、cookie 错、length 不符、保留位非零、地址族非法 | 服务端回 400（或非 STUN 则丢弃）；CLI exit 4 |
| `unknown_critical_attribute` | 出现不认识的 type<0x8000 | 服务端回 420 |
| `integrity_failure` | MI 缺失/非末尾/长度错/HMAC 不匹配/密钥错 | 服务端静默丢弃；客户端 CLI exit 2 |
| `state_conflict` | txn 已在途却重复 Add；旧响应撞新请求被拒 | 编程错误/丢弃，不完成请求 |
| `resource_exhausted` | 事务表满 | 背压，调用方可重试或报错 |
| `timeout` | 重传用尽仍无匹配响应 | CLI exit 3 |
| `source_mismatch` | txn 对但源地址不同 | 丢弃该响应，请求继续等/超时 |
| `compute_failure` | 随机数/HMAC/编码等本地计算异常 | 立即失败，CLI exit 4 |

用 `stun.ErrorOf(err)` 取得类别；`*stun.Error` 同时带 `Op`、`Attr`、`Detail`
与被包装的底层 error，日志中原样保留，保证判断理由可重放。
