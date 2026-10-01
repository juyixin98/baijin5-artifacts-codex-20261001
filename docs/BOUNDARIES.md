# 边界与失败语义

本文档精确规定 socks5d 在协议边界、转发边界与资源边界上的行为。所有结论都
对应代码中的稳定常量，测试按这些常量断言，而非按人类可读文案。

## 1. 方法协商（RFC 1928 第 3 节）

| 客户端行为 | 服务端行为 | 结果类别 | 是否拨号上游 |
|---|---|---|---|
| 提供服务端接受的方法 | 回 `05 <METHOD>`，继续 | `established` 路径 | 否（此后才可能） |
| 服务端免认证，客户端未提供 `0x00` | 回 `05 FF`，随后关闭连接 | `no_acceptable_method` / `method_not_offered` | **否** |
| 服务端要求认证，客户端未提供 `0x02` | 回 `05 FF`，随后关闭连接 | `no_acceptable_method` / `userpass_not_offered` | **否** |
| `VER != 0x05` | **不回任何字节**，关闭连接 | `protocol_error` / `unsupported_version` | 否 |
| 方法数为 0（`NMETHODS=0`） | 不回，关闭连接 | `protocol_error` / `no_methods_offered` | 否 |
| 帧截断 / 协商中 EOF / 超时 | 不回，关闭连接 | `protocol_error` / `frame_truncated`、`client_eof`、`deadline_exceeded` | 否 |

要点：协商失败是**明确终止**。协议格式错误（版本错、截断）不产生 SOCKS
应答（此时还无法确定客户端能理解 SOCKS5）；仅“可识别但无共同方法”才发
`0xFF`。

## 2. 用户名/密码子协商（RFC 1929）

| 行为 | 服务端 | 结果类别 |
|---|---|---|
| 子协商 `VER != 0x01` | 不回，关闭 | `bad_userpass_version` |
| 凭据正确 | 回 `01 00`，进入请求阶段 | 继续 |
| 凭据错误 | 回 `01 01`，随后关闭连接 | `auth_failed` / `bad_credentials` |
| 帧截断 / EOF | 不回，关闭 | `frame_truncated` / `client_eof` |

比较使用 `crypto/subtle.ConstantTimeCompare`；配置时空密码被拒绝，空用户名
按 RFC 1929 允许。凭据仅来自环境变量 `SOCKS5D_USERNAME` / `SOCKS5D_PASSWORD`。

## 3. 请求与地址（RFC 1928 第 4–5 节）

- 仅 `CMD=CONNECT(0x01)`。`BIND(0x02)` / `UDP ASSOCIATE(0x03)` 等收到格式
  正确的帧后回 `REP=0x07`（命令不支持），不拨号。
- `ATYP`：`0x01` IPv4 要求地址体恰为 4 字节、`0x04` IPv6 恰为 16 字节、
  `0x03` 域名长度 1..255；长度不符分别报 `ipv4_length` / `ipv6_length` /
  `domain_empty` / `domain_too_long` / `frame_truncated`。
- 未知 `ATYP` 回 `REP=0x08`（地址类型不支持）。服务端在读到 4 字节定长头、
  识别出未知 ATYP 后即应答，**不消费**后续地址体。
- `RSV` 必须为 0，否则 `bad_reserved_byte`，不回应答直接关闭。
- `VER` 必须为 5，否则 `unsupported_version`。

### 地址长度校验（逐字节）

| 形态 | 定长头后应有字节 | 不符的 Reason |
|---|---|---|
| IPv4 | 4 地址 + 2 端口 = 6 | `ipv4_length` |
| IPv6 | 16 地址 + 2 端口 = 18 | `ipv6_length` |
| 域名 | 1 长度 + N 名称 + 2 端口 | `frame_truncated` / `domain_empty` / `domain_too_long` |

流读取器与纯解码器共用同一份解析逻辑，因此任意 TCP 分段（逐字节到达）
与一次性喂入的判定完全一致。

## 4. 白名单策略与“解析—连接”一致性

顺序固定为：**名称/IP 判定 → 域名解析 → 逐地址过 CIDR → 仅拨已放行地址**。

| 情形 | REP | reason |
|---|---|---|
| IP 字面量命中某条 CIDR | `0x00`（拨号成功后） | — |
| IP 字面量不命中任何 CIDR | `0x02` | `ip_not_whitelisted` |
| 端口为 0 | `0x02` | `port_zero` |
| 域名不命中任何域名规则 | `0x02` | `domain_not_whitelisted` |
| 域名命中但解析失败 | `0x04` | `dns_lookup_failed` |
| 域名命中但无解析记录 | `0x04` | `dns_no_records` |
| 域名命中，但任一解析地址在白名单 CIDR 之外 | `0x02` | `resolved_addr_not_whitelisted` |
| 已放行地址拨号被拒/不可达/超时 | `0x05/0x03/0x04/0x06` | `dial_failed`（见下表） |

关键不变量：

1. **多地址全有或全无**：域名即便只解析出一个公网地址，整个请求也被拒绝，
   返回的拨号候选集合为空，公网地址永不被连接。
2. **只拨 vetted 地址**：状态机拿到的是策略产出的具体 `[]netip.AddrPort`，
   拨号器接口只接受 IP:Port，不接受主机名，故不存在第二次解析或 TOCTOU。
3. **多候选按序回落**：全部候选都在白名单内时，按解析顺序依次尝试，首个
   成功的地址被记录为实际连接目标；失败候选不会被记成已连接。

### 拨号失败到 REP 的映射

| 底层错误 | REP |
|---|---|
| `ECONNREFUSED` | `0x05` connection refused |
| `ENETUNREACH` | `0x03` network unreachable |
| `EHOSTUNREACH` | `0x04` host unreachable |
| 拨号超时（net.Error timeout） | `0x06` TTL expired |
| 其它 | `0x04` host unreachable（保守兜底） |

## 5. 转发：半关闭、排空与预算

- **方向化半关闭**：客户端读到 EOF（对端 FIN）只对上游执行 `CloseWrite`，
  上游→客户端方向继续；上游 FIN 对称处理。两个方向都半关闭后转发正常结束
  （`both_directions_closed`）。
- **先排空再半关闭**：某方向已读入、在预算内的字节会先完整写出，然后才发
  FIN。慢上游施加背压时，对端仍能收到完整的允许前缀，再看到 EOF。
- **每方向独立字节预算**：`byte_budget_per_connection` 分别约束两个方向，
  且在**写之前**按剩余额度封顶，因此任一方向转发字节数绝不超过预算（边界
  紧致，而非“最多多一个缓冲”）。触及预算即终止整条管道，原因记为
  `byte_budget_exceeded`。
- **连接数上限**：accept 后先占信号量，满载时立即关闭新连接而非无界排队，
  该连接不会进入协商。
- **空闲超时**：`idle_timeout > 0` 时，任一方向在该时长内无字节即结束，
  原因 `idle_timeout`；`0` 表示不限制（仅靠半关闭/对端关闭终止）。
- 传输层不支持 `CloseWrite` 时退化为整连接关闭并记 `half_close_unsupported`
  （当前 TCP 路径总是支持）。

## 6. 确定性失败 vs 不确定结论

日志/审计用三个互斥的结果值，避免把“没把握”写成“失败”或“成功”：

- `ok`：握手成功、转发因双向半关闭正常结束。
- `fail`：有确定原因的拒绝/越界，`reason` 给出稳定类别码（见上表）。
- `uncertain`：客户端在握手中途干净 EOF 或超时（`client_eof` /
  `deadline_exceeded`）——无法判断对方意图，单列为不确定，不冒充策略拒绝。

## 7. 观测字段

每条结构化日志包含：`ts`、`level`、`event`、`version`（程序版本）、
`at`（主机）、`req_id`（请求关联 ID）、`stage`（处理位置：
`accept/handshake/relay/teardown/...`）、`client`、`target`、`result`、
`reason_code`、`detail`，以及转发字节数等。SQLite `audit` 表按 `req_id`
保存 `connect` 与 `relay` 两个阶段，便于把一次请求串起来。
