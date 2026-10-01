# 协议契约（实现子集）

本文件明确 localstun 实现的 RFC 5389 语义、模块间数据与错误契约。
以 RFC 5389（2008）为准，仅实现 Binding；不实现 RFC 5766 TURN。

## 1. 报文结构

```
 0                   1                   2                   3
 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1 2 3 4 5 6 7 8 9 0 1
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|0 0|     STUN Message Type     |         Message Length        |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                         Magic Cookie                          |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                                                               |
|                     Transaction ID (96 bits)                  |
|                                                               |
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
```

- 前两位必须为 0，否则 `input: ErrLeadingBits`。
- Message Length 只计消息体字节数，必须等于 `len(wire)-20`，否则
  `input: ErrBadLength`。
- Magic Cookie 固定 `0x2112A442`，否则 `input: ErrBadCookie`。
- 支持的 Message Type：`0x0001` Request、`0x0101` Response、
  `0x0111` Error Response；其他 `input: ErrUnknownMethod`。

## 2. 属性 TLV 与四字节填充

```
 TLV:  Type(16) | Length(16) | Value(Length 字节) [+ padding]
```

- Length = 值的字节数，**不含填充**。
- 每个属性按 4 字节对齐：`pad = (4 - Length%4) % 4`。
- 填充字节内容无意义，解码端跳过且不校验其值（非零填充属“填充异常”，
  专门有用例验证可被容忍）。
- 属性值越过报文末尾 → `input: ErrTruncatedAttr`。

## 3. XOR-MAPPED-ADDRESS（§15.2）

```
 Reserved(8)=0 | Family(8) | X-Port(16) | X-Address(32 或 128 bit)
```

- Family：`0x01` IPv4（值总长 8），`0x02` IPv6（值总长 20）。
- `port = X-Port XOR (Cookie >> 16)`。
- IPv4：`ip[i] = X-Address[i] XOR Cookie[i]`。
- IPv6：`ip[i] = X-Address[i] XOR (Cookie || TxID)[i]`——掩码前 4 字节是
  魔数、后 12 字节是事务 ID，故 IPv6 结果依赖事务 ID。
- 保留字节非 0、未知族、长度与族不符 → `input`。

## 4. MESSAGE-INTEGRITY（§15.4）

- 值 = `HMAC-SHA1(key, 待鉴权内容)`，20 字节，常量时间比较。
- 待鉴权内容 = 从报文开头到 MI 属性**之前**的所有字节，但头部 Length
  字段临时改写为“覆盖到 MI 值末尾”的值（即在 MI 之前内容基础上 +24）。
- MI 之后只允许 FINGERPRINT。
- 不匹配 → `integrity: ErrIntegrityMismatch`；携带 MI 但未给校验密钥 →
  `compute`（配置错误，与“验过但不匹配”可区分）。
- 测试服务端用对称预共享密钥；另有 `stun.DeriveLongTermKey`
  = `MD5(user:realm:pass)` 供长期凭证路径使用（夹具为 ASCII，省略 SASLprep）。

## 5. FINGERPRINT（§15.5）

- 值 = `CRC32-IEEE(待校验内容) XOR 0x5354554E`，4 字节。
- 待校验内容 = 报文开头到 FINGERPRINT 之前，Length 临时 +8。
- 不匹配 → `integrity: ErrFingerprintMismatch`。

## 6. 未知属性处理

- Type 最高位为 0（< 0x8000）为 comprehension-required；未知则返回
  `*stun.UnknownRequiredError`（`Kind()=integrity`，携带类型列表）。
  服务端据此回 `420` + UNKNOWN-ATTRIBUTES（值为 16 位属性类型序列）。
- Type 最高位为 1（≥ 0x8000）为 comprehension-optional；未知则保留属性、
  继续处理。
- 已识别的必选属性集合：MAPPED-ADDRESS, USERNAME, MESSAGE-INTEGRITY,
  ERROR-CODE, UNKNOWN-ATTRIBUTES, REALM, NONCE, XOR-MAPPED-ADDRESS，
  以及 ICE 的 PRIORITY/USE-CANDIDATE（用于能解析 RFC 5769 请求夹具）。

## 7. ERROR-CODE（§15.6）

值 = `Reserved(16) | Class(8, 只用低3位=百位) | Number(8)=余数 | Reason(UTF-8)`。
本实现产出：`401 Unauthorized`（签名服务端收到无有效 MI 的请求）、
`420 Unknown Attribute`（未知必选属性）。

## 8. 客户端状态机契约

- 待办表 `map[TxID]*pending`，受 `MaxOutstanding` 限制（满 → `exhausted`）。
- 发送登记后启动定时器；超时 → `exhausted`；ctx 取消 → `state`。
- 读回路单点解复用：先过源匹配（IP 与端口全等），再完整性解码。
  - 源不符：`response_source_mismatch`，丢弃，不影响等待者。
  - 完整性失败：`integrity`，立即终结该事务等待者（防伪响应静默超时）。
  - 普通格式错误：记录 `response_decode_error`，等待者仍可接受后续合法响应。
  - TxID 无在办请求：`response_unknown_transaction`（迟到/伪造）。
- 成功匹配即从待办表删除；重复/迟到数据报不可能再命中任何等待者。

## 9. 跨边界数据契约

- 编解码：入 `[]byte + key`；出 `*stun.Message`，错误统一 `*stunerror.Error`
  或 `*UnknownRequiredError`（同样实现 `Kind() stunerror.Kind`）。
- 服务端 `HandlePacket(pkt, src) (resp []byte, kind Kind, event, detail string)`
  为纯函数（无 I/O），UDP 收发只在 `handle`/`loop`，便于直接单测。
- 审计：`audit.Record` JSON 标签固定，`audit.Sink` 为唯一落库接口；
  `store.SQLiteSink` 是其唯一数据库实现。
