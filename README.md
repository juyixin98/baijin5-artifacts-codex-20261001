# psi-dh — 教学级两方私有集合交集（DH-PSI over ristretto255）

本工程实现一个**本地、教学用途**的两方私有集合交集（Private Set Intersection）服务：
参与方 A 与 B 各持一个元素集合，协议结束后**只有 A 学到交集**，B 什么也学不到；
中转服务器只看到盲化后的椭圆曲线点，永远接触不到原始元素。

> 教学定位：代码追求可读、可验证、可复现。**不声称抵抗恶意参与者**（见“安全模型与非目标”）。

## 协议

经典 Diffie–Hellman PSI（半诚实地模型下的一个半轮次）：

```
会话建立: 服务器生成随机 session_id（256 bit，每会话全新，混入 hash-to-group）
A:  对每个元素 x:  P = H_session(x);  Q = a·P        —— 发送 {Q_i}      （a 为 A 的会话盲化标量）
B:  对每个元素 y:  R = b·H_session(y)                —— 发送 {R_j}
    并对收到的每个 Q_i:  S_i = b·Q_i                  —— 发送 {S_i}（与 A 提交顺序逐项对齐）
A:  本地计算 T_j = a·R_j；若 encode(S_i) ∈ {encode(T_j)}，则元素 i 在交集中
```

正确性：`S_i = b·a·H(x_i)`，`T_j = a·b·H(y_j)`，标量乘法可交换，故相等当且仅当 `H(x_i) = H(y_j)`。

## 密码学选择（成熟实现，点验证不可省）

| 选择 | 实现 | 理由 |
|---|---|---|
| 群 | ristretto255（`curve25519-dalek`） | 素数阶群、编码规范唯一，无 cofactor/小子群问题 |
| hash-to-group | SHA-512(域分离标签 ∥ session_id ∥ 长度前缀 ∥ 元素) → `RistrettoPoint::from_uniform_bytes` | dalek 内置的 Elligator2 映射，成熟实现；会话域隔离使同一元素在不同会话映射到不同点 |
| 会话随机数 | `session_id` 与双方盲化标量均每会话从 OS CSPRNG 新生成 | 跨会话不可关联；标量用毕即 zeroize |
| 点验证 | 每个入站点必须：32 字节、可规范解压（`decompress` 成功）、非单位元 | 拒绝非规范编码与恒等元；ristretto 保证扭点安全 |

## 集合语义与泄露范围（明确声明）

- 输入在密码运算**之前**排序去重：重复元素按集合语义处理，输出是无重复的有序集合。
- **泄露范围**：A 学到交集与 |B|；B 只学到 |A|；服务器（中转）看到双方集合大小与盲化点。
  集合大小不隐藏，这是本协议的固有泄露，不试图掩盖。
- 服务器对 A 的提交**不去重、不重排**（B 的响应与 A 的提交按下标对齐，重排会破坏协议）；
  观察到的重复数仅作为诊断写入审计日志。

## 安全模型与非目标

- 针对**半诚实（honest-but-curious）**参与者的教学实现。
- **不声称**：抵抗恶意参与者（无不零知识证明/一致性校验）、隐藏集合大小、
  抵抗字典攻击（低熵元素集合上，知道 H_session 输出的一方可以离线枚举——
  这是无盐 DH-PSI 的固有性质；session_id 只提供跨会话隔离，不是秘密盐）。
- 传输为本地明文 HTTP；教学场景不引入 TLS。

## 模块划分

```
src/
  crypto/     唯一的密码适配层：hash-to-group、点编解码与强制验证、随机数
  protocol/   双方状态机（PartyA / PartyB）、集合规范化、交集计算
  server/     Axum HTTP 中转：路由、状态机强制、请求标识、错误语义
  client/     客户端驱动（psi-client 使用）；原始元素不离开本进程
  store/      SQLite 持久化：会话状态、消息、审计日志（事务保证状态迁移）
  audit/      审计记录模型；detail 只含计数与负载摘要（脱敏）
  verify/     独立参考实现：明文交集、转录泄露扫描（测试与演示使用）
  error.rs    统一错误分类，稳定的机器可读错误码
  config.rs   服务器配置（环境变量驱动，见 config/server.env.example）
tests/        协议往返、非法点、集合语义、转录隐私、HTTP 端到端、冻结向量
fixtures/     演示用合成元素与冻结的期望交集
scripts/demo.sh  本地端到端演示
```

## 运行

```bash
cargo build

# 终端 1：启动服务（配置见 config/server.env.example，均有默认值）
./target/debug/psi-server

# 终端 2：创建会话并运行两方
SESSION=$(./target/debug/psi-client new-session)
./target/debug/psi-client run --role b --session "$SESSION" --file fixtures/bob_elements.txt &
./target/debug/psi-client run --role a --session "$SESSION" --file fixtures/alice_elements.txt

# 查看（脱敏）审计日志
./target/debug/psi-client audit --session "$SESSION"
```

或一键演示（构建、起服务、双方运行、与 `sort/comm` 独立明文参考比对、泄露扫描）：

```bash
./scripts/demo.sh
```

## 测试与复现

```bash
cargo test          # 全部测试（含 HTTP 端到端，使用临时端口与内存数据库）
```

测试要点：

- **参考答案独立生成**：期望交集由 `verify::plaintext_intersection`（独立参考实现）、
  测试内硬编码字面量、以及 demo 脚本中的 `sort`/`comm` 计算，均不经过被测协议核心。
- 冻结回归向量：`hash_to_point(0x00…00, "hello")` 的编码被冻结在
  `tests/reference_vectors.rs`，另有固定标量下对协议恒等式的独立再推导（不经过 `protocol` 模块）。
- 覆盖：合成交集、空集、全等集合、重复元素（集合语义）、非法点（非规范编码/恒等元/错误长度）、
  状态机违例（B 抢先响应 409）、计数不匹配、未知会话 404、转录隐私（服务器可见数据中无原始元素）。

## HTTP API 与错误语义

所有错误响应为统一结构，HTTP 状态码 + 机器可读码 + 请求标识 + 当前会话状态：

```json
{ "error": { "code": "INVALID_POINT_ENCODING",
             "message": "point 7: not a canonical ristretto255 encoding",
             "request_id": "3f8a…", "session_state": "created" } }
```

| code | HTTP | 含义 |
|---|---|---|
| `INVALID_POINT_ENCODING` | 400 | 点不是规范的 ristretto255 编码（解压失败） |
| `IDENTITY_POINT` | 400 | 恒等元，协议禁止 |
| `BAD_POINT_LENGTH` | 400 | 点不是 32 字节 |
| `COUNT_MISMATCH` | 400 | B 的 `a_doubly` 数量与 A 的提交不一致 |
| `SET_TOO_LARGE` | 400 | 超过 `PSI_MAX_SET_SIZE` |
| `BAD_REQUEST` | 400 | 其它请求格式错误（如非法 hex） |
| `SESSION_NOT_FOUND` | 404 | 会话不存在 |
| `INVALID_SESSION_STATE` | 409 | 状态机违例（如 B 在 A 提交前响应、重复提交） |
| `INTERNAL` | 500 | 服务器内部错误 |

路由：

```
POST /v1/sessions                     创建会话 → {session_id, state}
GET  /v1/sessions/{id}                会话状态
POST /v1/sessions/{id}/submissions/a  A 提交盲化点 {points: [hex]}
POST /v1/sessions/{id}/submissions/b  B 响应 {b_blinded: [hex], a_doubly: [hex]}
GET  /v1/sessions/{id}/messages/a     B 轮询 A 的盲化点
GET  /v1/sessions/{id}/messages/b     A 轮询 B 的响应
GET  /v1/sessions/{id}/audit          脱敏审计日志
```

## 诊断与审计

- 每个请求带 `x-request-id`（响应头与错误体中均可见），审计记录关联同一标识。
- 审计记录说明**接受/拒绝及原因**：事件名、结果（accepted/rejected）、
  细节（计数、重复数、负载 SHA-256 截断摘要、错误码与原因）。
- **脱敏策略**：审计与日志只含计数与摘要，绝不包含原始元素，也不包含完整点列表；
  客户端的重复元素提示只打印数量。

## 依赖

axum（HTTP）、tokio（异步运行时）、curve25519-dalek（ristretto255 运算）、
sha2（hash-to-group 与审计摘要）、rusqlite（SQLite，bundled）、
reqwest（客户端）、clap（CLI）、serde/serde_json、thiserror、hex、
tracing/tracing-subscriber、rand_core（OS CSPRNG）、zeroize（标量擦除）。
