# sae — 大消息分段认证加密服务

将大消息切分为若干段，每段用成熟的 AEAD（ChaCha20-Poly1305）认证加密；
**只有整条流完成认证后，完整明文才会被发布**。分段暂存与明文发布在存储层
分离，任何删段、重排、截尾、篡改都会在 finalize 阶段被分类拒绝。

## 架构

| 模块 | 职责 |
|---|---|
| `sae/protocol.py` | 线路协议：AAD 编码、nonce 派生、HKDF 密钥派生（纯 stdlib，无 AEAD 依赖） |
| `sae/crypto_aead.py` | 核心密码适配器（`cryptography` / ChaCha20-Poly1305），唯一加解密路径 |
| `sae/crypto_verify.py` | 独立验证器（PyCryptodome），发布前对每个段做第二实现交叉验证；也用于生成参考测试向量 |
| `sae/store.py` | SQLite 状态：消息、暂存段、发布区（独立表）、审计；release 为单事务 |
| `sae/audit.py` | 审计：每次接受/拒绝/无法判定都带 request id 与原因，只记录哈希与长度等脱敏元数据 |
| `sae/service.py` | 业务逻辑：分段加密、幂等重试、完整性校验、原子发布 |
| `sae/api.py` | FastAPI 传输层：请求标识、错误分类映射 |
| `sae/config.py` | 环境变量配置（`SAE_MASTER_KEY` 等），启动即校验 |
| `sae/errors.py` | 失败类别分类法（`incomplete_stream`、`tag_mismatch`、`nonce_reuse_conflict` …） |

## 安全性质

- **nonce 唯一且绑定身份**：nonce = 每条消息随机的 8 字节 `nonce_base` ‖ 4 字节
  大端序号；AAD 绑定协议版本、消息 id、序号、终止标记与段长。消息密钥 =
  HKDF(master_key, 每条消息随机 salt, 消息 id)。
- **重试安全**：`(message, seq)` 在数据库中唯一。相同内容的重试直接返回已存
  密文（不重新加密）；相同序号不同内容 → `nonce_reuse_conflict`，绝不出现
  同 nonce 加密不同内容。
- **删段/重排/截尾可检测**：序号必须连续 `0..N-1`，终止标记必须恰好出现在
  最后一段；每段密文与其序号通过 nonce+AAD 绑定，换序即 tag 校验失败。
- **完成前不发布**：明文只在 finalize 全部认证通过后，于单个 SQLite 事务内
  写入 `released` 表；进程在发布前崩溃不会泄露部分明文，重启后可重试。
- **独立验证**：发布前用 PyCryptodome 对每个段做第二次 tag 验证；测试向量由
  PyCryptodome 生成，与被测的 `cryptography` 核心实现互相独立。

## 快速开始

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export SAE_MASTER_KEY=$(python3 -c "import os; print(os.urandom(32).hex())")
.venv/bin/python -m sae                       # SAE_PORT=8391 可改端口
.venv/bin/python examples/client_demo.py      # 另开一个终端运行示例
```

## API 摘要

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/v1/messages` | 分配消息流 → `{message_id}` |
| POST | `/v1/messages/{id}/chunks` | `{seq, final, plaintext_b64}` → `{ciphertext_b64, replayed}` |
| POST | `/v1/messages/{id}/finalize` | 完整性+认证校验，原子发布明文 |
| GET | `/v1/messages/{id}` | 状态：已收序号、终止标记位置、发布哈希 |
| GET | `/v1/messages/{id}/plaintext` | 仅 released 后可用，否则 409 |
| GET | `/v1/messages/{id}/audit` | 该消息的审计轨迹（脱敏） |

错误响应统一为 `{"error": {category, reason, context}, "request_id": ...}`，
`category` 取值见 `sae/errors.py` 顶部注释。

## 测试与复现

见 [docs/REPRODUCE.md](docs/REPRODUCE.md)（环境、命令、预期结果）与
[docs/TEST_RESULTS.md](docs/TEST_RESULTS.md)（真实运行留存的结果）。
协议细节见 [docs/PROTOCOL.md](docs/PROTOCOL.md)。
