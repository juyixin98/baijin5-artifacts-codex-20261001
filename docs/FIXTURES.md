# 测试夹具与独立预言机

本实验台的核心证据原则：**参考答案不由被测的 Go 核心实现自己生成**。
所有"正确字节"来自两类外部来源：

1. **RFC 规范中的固定已知答案向量**（RFC 5769 §2.3/§2.4 的 XOR-MAPPED-ADDRESS）；
2. **一个用 Python 标准库从零独立实现的 STUN 编解码器**
   （`test/oracle/stun_oracle.py`），与 Go 代码零共享。

## 1. 独立预言机 `test/oracle/stun_oracle.py`

它独立完成：属性 TLV 编解码、四字节填充、MAPPED/XOR-MAPPED 地址、
ERROR-CODE、消息封装，以及 HMAC-SHA1 的签名与验签——全部按 RFC 重写，
未 import、未复制任何 Go 代码。三种用法：

```bash
# (a) 生成冻结夹具（Go 黄金测试只读取、不生成答案）
python3 test/oracle/stun_oracle.py fixtures test/testdata/fixtures.json

# (b) 解码任意抓包/导出的 STUN datagram（独立第二意见）
python3 test/oracle/stun_oracle.py decode some.bin

# (c) 充当 UDP 对端
python3 test/oracle/stun_oracle.py reply --mode reflect --key labkey   # 正常响应
python3 test/oracle/stun_oracle.py reply --mode badtxn                  # 错误事务
python3 test/oracle/stun_oracle.py reply --mode badsrc                  # 异源响应
python3 test/oracle/stun_oracle.py reply --mode tamper --key labkey     # 篡改 MI
python3 test/oracle/stun_oracle.py bind 127.0.0.1:3478 --key labkey     # 作为客户端
python3 test/oracle/stun_oracle.py bind 127.0.0.1:3478 --expect-error 420
```

## 2. 冻结夹具 `test/testdata/fixtures.json`

由 `make fixtures` 生成，纳入版本管理，字段：

| 区块 | 内容 | Go 侧断言 |
|------|------|-----------|
| `rfc5769_xor_vectors` | RFC 5769 IPv4/IPv6 XOR 属性固定字节 | Go 编码必须**逐字节等于** RFC 字节；Go 解码必须还原 IP/端口 |
| `messages` | 预言机签名/不签名的完整 IPv4/IPv6 成功消息 | Go 必须能解析、解码地址、验签；并且 Go 用相同输入重建的消息与预言机**逐字节相同** |
| `padding_vectors` | 值长度 1–8 的属性线上长度（覆盖全部填充余数） | Go 编码出的线长必须等于预言机 |
| `error_messages` | 400 与 420（含 UNKNOWN-ATTRIBUTES） | Go 必须解出相同 code/reason/未知属性列表 |
| `integrity_negative` | 标签位翻转、事务位翻转、错误密钥、长度谎报 | Go 必须报与预言机相同的失败类别（`integrity_failure` / `input_error`） |

每个夹具条目都带**期望的具体结果或失败类别**，不是"接口能调用就算通过"。

关键互锁：`oracle_encodes_correctly` 是预言机用 RFC 5769 常量对**自身**的校验。
若该字段为 false，Go 黄金测试会直接失败并提示夹具不可信——防止"两个实现
犯了同一个错"。

## 3. 跨语言互操作测试 `test/cross/`

真实 loopback UDP，两个方向都测：

- **Go 客户端 ↔ Python 响应端**：`reflect`（必须接受且 HMAC 由 Python 算出）、
  `badtxn`（错误事务 → 超时）、`badsrc`（异源 → 超时）、`tamper`（标签被改 →
  `integrity_failure`）、密钥不一致（→ `integrity_failure`）。
- **Python 客户端 ↔ Go 服务端**：IPv4/IPv6 成功（Python 独立验签并核对反射端口）、
  未知必须理解属性 → 420、Go 不签名而 Python 强制验签 → `integrity_failure`。

这些测试会真实 `exec python3`；无 Python 时自动 `t.Skip`，其余 Go 测试不受影响。

## 4. 负向向量一览（需求点对应）

| 需求 | 向量来源 | 断言类别 |
|------|----------|----------|
| IPv4 | RFC 5769 + Go↔Python | 地址/端口精确相等 |
| IPv6 | RFC 5769 + Go↔Python | 地址/端口精确相等，16 字节 |
| 填充异常 | Go 单测 + 预言机 padding_vectors + 服务端 400 | `input_error` |
| 错误事务 | Python `badtxn` / Go 状态机旧响应测试 | `timeout`（不完成请求） |
| 异源响应 | Python `badsrc` / Go 双 socket 测试 | `timeout`（源不匹配先拒） |
| 完整性篡改 | 预言机 tamper / Go 标签·事务位翻转 | `integrity_failure` |
| 未知必懂属性 | 420 夹具 + Python client / Go 服务端 | 420 + UNKNOWN-ATTRIBUTES |
| 资源耗尽 | Go 事务表容量测试 | `resource_exhausted` |
| 状态冲突 | Go 事务表重复登记 | `state_conflict` |
