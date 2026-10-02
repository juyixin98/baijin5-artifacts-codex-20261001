# mbfixture — 本机 Modbus TCP 主从夹具

纯本地 Modbus TCP 主站（master/client）与从站（slave/server）夹具，支持
**读保持寄存器（FC03）** 与 **写多个寄存器（FC16）**。所有验证只经过
`127.0.0.1` 回环地址与 `net.Pipe`，**不连接任何真实工业设备**。

## 模块关系

```
cmd/mbfixture        进程入口（3 行薄壳）
internal/mbcli       CLI 子命令实现：serve / read / write（可进程内测试）
internal/mbconfig    JSON 配置加载与校验
internal/mbcodec     字节编解码：MBAP 头、帧重组（半包/粘包）、FC03/FC16 PDU、异常 PDU
internal/mbproto     协议词汇：功能码、异常码、协议上限、ExceptionError 类型
internal/mbstore     寄存器存储：SQLite 持久化，写多寄存器单事务原子提交
internal/mbserver    从站：连接循环、每请求独立 goroutine 派发、写串行化
internal/mbclient    主站：事务 ID 分配、pending 表、响应按 TxID 匹配（允许乱序）
internal/mblog       结构化 JSON 日志：run ID（crypto/rand）、帧指纹（crypto/sha256）
compat/              独立兼容测试：手算报文向量 + 端到端行为测试
```

依赖方向：`mbcli → mbserver/mbclient → mbcodec/mbproto/mbstore/mblog`，
`mbconfig` 仅被 `mbcli` 使用；`compat` 从外部同时压测主从两侧。

## 算法与协议假设

- **字节序**：线上所有多字节字段（MBAP、地址、数量、寄存器值）一律大端；
  寄存器值为 16 位无符号，范围 `[0, 65535]`，CLI 与编解码层双重校验。
- **MBAP 三项分别校验**：Protocol ID 必须为 0（否则关连接）；Length 必须在
  `[2, 254]`（UnitID + PDU，否则关连接）；Transaction ID 不由从站校验，
  由从站原样回显、由主站用来匹配响应身份。Unit ID 不在配置集合内时，
  从站回异常 `0x0B GATEWAY_TARGET_NOT_RESPONDING`（网关语义），而非静默丢弃。
- **不混包**：每条连接一个读循环，按 MBAP Length 精确切帧；每个请求在独立
  goroutine 处理，响应**可以乱序**，身份仅靠回显的 TxID；同一连接上的响应
  写操作由互斥锁串行化，帧不会交错。主站按 TxID 投递响应，未知 TxID 记日志
  丢弃，绝不错投。
- **写多寄存器原子**：整个地址区间在一个 SQLite 事务内更新；任一地址越界
  （或未供给）整体回滚，并发读者只能看到全旧或全新，不会看到撕裂窗口。
- **异常而非数据**：不支持的功能码回 `01 ILLEGAL_FUNCTION`；地址越界回
  `02 ILLEGAL_DATA_ADDRESS`；数量为 0、超过上限（读 125 / 写 123）、
  数量与字节数不符、PDU 截断回 `03 ILLEGAL_DATA_VALUE`；存储内部错误回
  `04 SERVER_DEVICE_FAILURE`。
- **帧解析失败即关连接**：Protocol ID 或 Length 非法后流位置不可恢复，
  从站记录失败类别（`protocol_id` / `mbap_length`）并关闭连接。
- **寄存器空间**：每单元 `register_count` 个地址（0..N-1），启动时按单元
  预置全零行；读未供给单元按地址越界处理。
- **日志可解释**：每条记录带 `run`（进程随机 ID）、`txid`/`unit`/`func`、
  决策（`read_ok`/`write_ok`/`request_rejected`）、失败 `reason` 与异常名，
  请求帧带 SHA-256 指纹 `frame_fp` 便于收发关联。

## 依赖版本

- Go 1.22+（开发环境 go1.22.2）
- `modernc.org/sqlite v1.34.5`（纯 Go SQLite，无 cgo；间接依赖见 go.sum）
- 其余仅用标准库：`net`、`encoding/binary`、`database/sql`、`crypto/rand`、
  `crypto/sha256`、`encoding/json`、`testing`

## 本地验证命令与预期判断

```bash
# 1. 构建（预期：无输出，退出码 0）
go build ./...

# 2. 全部测试（含竞态检测；预期：全部 ok，无 FAIL）
go test -race ./...

# 3. 覆盖率（预期：total >= 80%）
go test -race -coverpkg=./... ./...

# 4. 端到端手动验证
go build -o /tmp/mbfixture ./cmd/mbfixture
/tmp/mbfixture serve -config config.example.json &   # 预期打印 serving on 127.0.0.1:1502
/tmp/mbfixture write -unit 1 -reg 2 -values 10,258,65535
#   预期 stdout: {"addr":2,"ok":true,"op":"write","qty":3,"unit":1,"values":[10,258,65535]}
/tmp/mbfixture read -unit 1 -reg 2 -qty 3
#   预期 stdout: {"addr":2,"ok":true,"op":"read","qty":3,"unit":1,"values":[10,258,65535]}
/tmp/mbfixture read -unit 1 -reg 127 -qty 2          # 越界（128 个寄存器）
#   预期退出码 1，stderr 含 "category":"modbus_exception:ILLEGAL_DATA_ADDRESS"
/tmp/mbfixture read -unit 9 -reg 0 -qty 1            # 未知单元
#   预期退出码 1，stderr 含 "category":"modbus_exception:GATEWAY_TARGET_NOT_RESPONDING"
```

判断方式：读回值必须与写入值逐一相等；失败场景必须退出码非零且
stderr 的 JSON 中 `category` 字段指明失败类别（异常名 / timeout /
connection_closed），而不是返回普通数据。服务端 stderr 的 JSON 日志可用
`txid` 把 `frame_rx → read_ok/write_ok/request_rejected → frame_tx`
串成一次完整请求。

## 测试清单与状态

| 测试 | 验证点 | 状态 |
|---|---|---|
| compat/TestVectorWriteThenRead | 手算 FC16+FC03 报文向量逐字节断言 | ✅ 通过 |
| compat/TestVectorExceptions | 6 类失败的手算异常向量（功能/地址/数量/数量不符/未知单元） | ✅ 通过 |
| compat/TestHalfPacket | 三段分片发送，正确重组 | ✅ 通过 |
| compat/TestStickyPacket | 两帧一次写入，分别正确应答 | ✅ 通过 |
| compat/TestOutOfOrderResponses | 人为延迟制造乱序，TxID 身份正确 | ✅ 通过 |
| compat/TestMBAPViolationsCloseConnection | 协议 ID/长度非法 → 关连接无响应 | ✅ 通过 |
| compat/TestClientRoundTrip | 主站 API 读写回环 + 异常类型断言 | ✅ 通过 |
| compat/TestClientConcurrentIdentity | 24 并发请求乱序下身份不串 | ✅ 通过 |
| compat/TestClientIgnoresForeignTxID | 假从站发错 TxID，主站丢弃不错投 | ✅ 通过 |
| compat/TestClientTimeout | 静默从站 → ErrTimeout | ✅ 通过 |
| compat/TestClientValidatesArguments | 主站本地数量校验 | ✅ 通过 |
| compat/TestWireAtomicity | 线上并发读写不撕裂 | ✅ 通过 |
| compat/TestCLIEndToEnd | 真实二进制 serve/read/write + 失败类别 | ✅ 通过 |
| mbcodec 单元测试 | 编解码回环、截断、数量不符、半包/粘包 | ✅ 通过 |
| mbstore 单元测试 | 原子回滚、越界、并发一致性、持久化重开 | ✅ 通过 |
| mbcli/mbconfig/mblog/mbproto 单元测试 | CLI 行为、配置校验、日志结构、异常名 | ✅ 通过 |

最近一次完整运行：`go test -race -coverpkg=./... ./...` 全部 `ok`，
总覆盖率 **84.5%**（`cmd/mbfixture` 为 3 行入口薄壳，逻辑均在已测的
`internal/mbcli`）。无未通过、无跳过的测试。
