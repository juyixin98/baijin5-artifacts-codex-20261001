# SAE1 协议

## 常量

- AEAD：ChaCha20-Poly1305（RFC 8439），tag 16 字节，nonce 12 字节
- `MAGIC = b"SAE1"`，`MSG_ID_LEN = 16`，`SALT_LEN = 16`，`NONCE_BASE_LEN = 8`
- `MAX_SEQ = 2**32 - 1`

## 密钥派生

```
msg_key = HKDF-SHA256(ikm = master_key,            # 32 字节，环境变量注入
                      salt = per-message salt,      # 16 字节随机，建流时生成
                      info = b"sae-msg-key-v1" || msg_id,
                      L = 32)
```

HKDF 按 RFC 5869 用 stdlib `hmac`/`hashlib` 实现于 `sae/protocol.py`，
任何一方无需本工程代码即可复现（测试中含 RFC 5869 Test Case 1 向量）。

## 每段 nonce

```
nonce = nonce_base(8B, 每条消息随机) || seq(4B, 大端)
```

唯一性论证：nonce_base 每条消息随机且与消息密钥绑定；`(message_id, seq)`
在存储层有主键约束，同一序号不会第二次加密。相同内容的重试返回已存记录，
不同内容的重试被拒绝（`nonce_reuse_conflict`），因此同一 (key, nonce)
永远不会加密两份不同内容。

## 每段 AAD（37 字节）

```
MAGIC   4B   b"SAE1"
msg_id 16B   消息身份
seq     8B   大端序号（0 起）
flags   1B   bit0 = 终止标记
pt_len  8B   大端段明文字节数
```

段密文存储为 `ciphertext || tag`。序号与终止标记同时进入 nonce 与 AAD，
因此：换序 → tag 失败；删段 → 序号不连续；截尾 → 缺终止标记；
伪造终止位置 → `final_flag_misplaced`。

## 流完整性规则（finalize）

1. 已收序号集合必须恰好是 `0..N-1`（连续、无洞、无重复——主键保证无重复）。
2. 终止标记必须恰好出现一次，且在第 `N-1` 段。
3. 重新计算每段 AAD 并与暂存值比对（防暂存记录被改）。
4. 核心 AEAD（cryptography）逐段解密，独立验证器（PyCryptodome）逐段复核。
5. 解密内容的 SHA-256 与暂存哈希比对。
6. 全部通过后在**单个事务**内写入 `released` 表并置状态 `released`；
   任一步失败则不发布任何明文，tag 类失败将消息置为 `failed`。

## 失败类别

| category | 含义 | HTTP |
|---|---|---|
| `not_found` | 未知消息 id | 404 |
| `incomplete_stream` | 序号有洞 / 缺终止标记 / 空流 | 409 |
| `final_flag_misplaced` | 终止标记位置错误或出现多次 | 409 |
| `tag_mismatch` | AEAD 认证失败（篡改、换序、AAD 不符） | 422 |
| `nonce_reuse_conflict` | 同序号不同内容的重试 | 409 |
| `message_state_conflict` | 状态不允许（未发布读明文、已关闭再写） | 409 |
| `undecidable` | 无法判定（如中断恢复中），调用方需重试 | 503 |
