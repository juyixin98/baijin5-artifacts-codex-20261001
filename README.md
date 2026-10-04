# blindex — 本地敏感字段随机密文 + 带密钥盲索引查询层

本地合成环境演示：敏感字段以 **AES-256-GCM 随机密文** 存储（每次加密新随机 nonce，
同值不同密文），等值查询通过 **HMAC-SHA256 带密钥盲索引**（截断）筛选候选，
候选 **解密后二次确认** 才返回。所有数据均为本地合成夹具，无任何生产账号或真实业务数据。

## 安全定位（明确声明，不含糊）

- **盲索引泄露相等关系**：它是确定性的 keyed hash，同值同索引。持有索引表的人
  可以看出哪些记录字段相同。这是设计前提，**不是匿名化**。
- **确定性索引不是密文**：索引不可解密、不承担机密性；机密性完全由 AES-GCM
  密文承担。测试 `test_index_is_not_ciphertext_substitute` 固化这一约束。
- **日志仅输出记录身份**：审计层用键白名单强制——字段值、规范化结果、索引值、
  密文片段一律写不进日志（尝试写入会直接报错）。

## 模块划分

| 模块 | 职责 |
|---|---|
| `src/blindex/protocol.py` | 协议编码：字段规范化、用途域固定的盲索引输入编码、版本化密文信封 |
| `src/blindex/crypto_adapter.py` | 成熟密码适配：AES-256-GCM（cryptography）、HMAC-SHA256 盲索引（PyCryptodome） |
| `src/blindex/storage.py` | 状态：SQLite 记录表 / 盲索引表 / 轮换状态机 / 审计表 |
| `src/blindex/audit.py` | 审计：白名单强制“日志仅记录身份 + 安全元数据” |
| `src/blindex/service.py` | 业务：写入、双版本查询 + 解密二次确认、索引密钥轮换与可中断重建 |
| `src/blindex/verify.py` | 独立验证：仅用标准库按规范重算期望结果并与实际比对（不经核心实现） |
| `src/blindex/api.py` / `server.py` | FastAPI 接口层与装配 |
| `tests/reference.py` | 测试侧独立明文参考（规范化与期望命中独立重算） |

## 协议规范

- **规范化**（等值语义作用对象）：`email` 去空白+小写；`phone` 去非数字（保留前导 `+`）；
  `name` 折叠空白+casefold；`id_number` 去空白/连字符+大写。`NULL` 不入索引，
  对 NULL 的等值查询按规范拒绝（类别 `NULL_QUERY`）。
- **盲索引输入**：`b"BLIDX\x01" || lp(domain) || lp(field) || lp(规范化值)`，
  `lp` 为 uint16 大端长度前缀（杜绝拼接歧义），`domain` 为部署固定的用途域
  （默认 `local-pii/v1`），实现域分离。
- **盲索引值**：`HMAC-SHA256(索引密钥[版本], 规范输入)` 截断到 `index_bits`（默认 64）。
- **密文信封**：`b"CE1" || key_version(uint16 BE) || nonce(12B) || ciphertext||tag(16B)`。

## 复现（从干净目录）

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt     # 固定版本见文件
.venv/bin/python scripts/gen_keys.py          # 生成 config/dev_keys.json（本地开发密钥环）
.venv/bin/python -m pytest tests/ -q          # 全部独立测试
```

启动服务并请求样例：

```bash
BLINDEX_DB=data/blindex.db PYTHONPATH=src .venv/bin/uvicorn blindex.server:app --port 8527

curl -X POST localhost:8527/records -H 'X-Request-Id: demo-1' \
  -H 'Content-Type: application/json' \
  -d '{"fields":{"email":"Alice@Example.com","phone":"+1 (555) 010-2030"}}'
curl -X POST localhost:8527/query -H 'Content-Type: application/json' \
  -d '{"field":"email","value":"ALICE@example.COM"}'
curl -X POST localhost:8527/admin/rotate-index-key          # 开始轮换 → 双版本查询
curl -X POST localhost:8527/admin/reindex -H 'Content-Type: application/json' -d '{"limit":1}'
curl localhost:8527/admin/rotation-status
curl localhost:8527/audit                                    # 日志仅含记录身份
```

环境变量：`BLINDEX_KEYFILE`（默认 `config/dev_keys.json`）、`BLINDEX_DB`（默认 `data/blindex.db`）。

## 接口结果的可解释性

- 每个请求关联 `X-Request-Id`（可显式传入，否则生成），响应体与响应头均携带，
  审计日志以同一身份落库。
- 查询结果单列：`searched_index_versions`（查了哪些索引版本）、`candidates`、
  `rejected_candidates`（碰撞被解密确认排除数）、`uncertain`（解密失败等不确定
  结论及其失败类别，不静默丢弃）。
- 失败按类别返回：`VALIDATION_ERROR / NULL_QUERY / ENVELOPE_MALFORMED /
  UNKNOWN_KEY_VERSION / DECRYPT_AUTH_FAILED / NOT_FOUND / ROTATION_STATE_ERROR /
  CONFIG_ERROR`。

## 独立测试覆盖（tests/）

- `test_protocol.py` 规范化等价类、编码无拼接歧义、域分离、信封损坏类别
- `test_crypto.py` 同值不同密文、篡改→`DECRYPT_AUTH_FAILED`、未知版本类别、
  索引确定性（显式断言相等关系泄露）、索引≠密文、PyCryptodome↔hashlib 交叉一致
- `test_query.py` 同值不同表示命中、**4-bit 强制短索引碰撞**（候选=2、确认=1、
  排除=1 精确断言）、NULL 语义、全夹具 × 表示变体对照明文参考
- `test_rotation.py` 轮换全程双版本查询不漏记录、中断恢复、状态机非法迁移、
  崩溃重启后查询版本集自愈
- `test_verify.py` 独立验证器与核心一致、漂移检出、审计白名单与无明文断言
- `test_api.py` 请求身份关联、失败类别、轮换端点、审计无明文、独立验证端点

参考答案来源：`tests/reference.py`（独立编写的规范化 + 明文扫描）与
`src/blindex/verify.py`（标准库 hashlib 重算），均不经过被测核心实现。

## 验收记录（2026-10-04 实跑）

- `pytest tests/ -q`：**69 passed**（约 2s）。
- 干净目录起服务后手工链路：写入两条同值不同表示记录 → 以第三种表示查询命中
  两条（`searched_index_versions:[1]`）→ NULL 查询返回 `NULL_QUERY` →
  轮换至 v2（`query_versions:[1,2]`）→ 只重建 1 批后查询仍命中两条
  （`searched_index_versions:[1,2]`）→ 重建完成（`state:idle`，
  `index_versions_present:[2]`）→ 审计日志只含 record_id / request_id /
  版本与计数，无任何字段值。
