# 边界语义与验证契约

## 1. 派生树语义

```
root（本地测试根，32 字节，存于 SQLite meta 表）
 └─ L1 tenant   → HKDF-SHA256(parent, salt=FIXED_SALT, info=TLV[(scope,tenant),(tenant,T)])
    └─ L2 purpose → HKDF-SHA256(parent, salt=FIXED_SALT, info=TLV[(scope,purpose),(purpose,P)])
       └─ L3 version → HKDF-SHA256(parent, salt=FIXED_SALT, info=TLV[(scope,version),(version,u32be)])
          └─ L4 context → HKDF-SHA256(parent, salt=FIXED_SALT, info=TLV[(scope,context),(context,C)], length=L)
```

- **盐与 info 角色固定**：盐永远是部署常量 `FIXED_SALT`
  （`SHA256("keytree-local-test-salt-v1")`），不接受调用方输入；info 永远
  承载 TLV 编码的层级标签。两者角色不可互换。
- **派生长度上限**：HKDF-SHA256 的算法上限 `255 × 32 = 8160` 字节。
  中间层固定 32 字节；末层长度由调用方指定，越界报
  `resource_exhausted`，绝不静默截断。
- **确定性**：相同身份四元组 + 相同长度 ⇒ 相同密钥（测试断言）。
- **分离性**：任一身份字段不同 ⇒ info 块不同 ⇒ 密钥不同（测试按
  租户/用途/版本/上下文分别断言）。
- **前缀性质（已知且有意接受）**：HKDF-Expand 对同一 info 的不同 L 输出
  前缀一致，因此同一身份不同长度的密钥互为前缀。本服务按
  `(key_id, length)` 登记，不视其为冲突；调用方若需要长度间隔离，应
  使用不同 `purpose`。

## 2. 标签编码（无歧义）

标签**绝不**裸拼字符串。每个字段编码为：

```
u16be(len(tag)) || tag || u32be(len(value)) || value
```

info 块为 `b"KDT1" || u16be(field_count) || field*`。该编码是单射：
`("ab","c")` 与 `("a","bc")` 在裸拼接下碰撞，在 TLV 下必然不同——
测试以该反例显式断言。解码严格：截断、尾随字节、错误魔数均为
`input_error`。

限额：`tag ≤ 64B`、`value ≤ 4096B`、`字段数 ≤ 32`、
`context ≤ 256B`、`tenant/purpose` 匹配 `^[a-z0-9][a-z0-9._-]{0,63}$`、
`version ∈ [0, 2^32)`。

## 3. 密钥身份 vs 显示名

- 身份 = `(tenant, purpose, version, context)` 四元组；
  `key_id = "kdt1_" + SHA256(TLV(identity))[:40hex]`。
- 显示名是独立元数据，**不参与**身份与派生。名称绑定表
  `display_name → key_id` 唯一：把已绑定名称绑到别的 key_id 是
  `state_conflict`；派生与查找永不按显示名进行，避免名称混淆身份。

## 4. 错误类别（可区分、进审计）

| 类别 | 含义 | HTTP | 示例 |
|---|---|---|---|
| `input_error` | 输入违反契约 | 400 | 非法 tenant、坏 base64、超长 context |
| `state_conflict` | 与持久状态冲突 | 409 | 注册表指纹被篡改、显示名重复绑定 |
| `resource_exhausted` | 超算法/配置上限 | 413 | length > 8160 |
| `computation_failure` | 后端/存储故障 | 500 | 加密后端异常、SQLite 故障 |

每次操作（成功或失败）写入审计表：`run_id, event, outcome,
error_category, detail_json`，可按 run_id 回放。

## 5. 日志契约（可回放、不泄密）

诊断日志为 JSONL，每条含 `run_id`、`event`、`rationale`（判断理由）及
安全标识符。事件序列：`input_validated → level_derived×4 → completed`
或 `failed`。**硬规则**：根密钥、中间链密钥、派生密钥永不入日志、
审计或注册表；只有 `key_id` 与指纹（`HMAC-SHA256(key,
"keytree-check-v1")[:16]`）可出现。测试扫描日志断言密钥材料缺席。

## 6. 验证矩阵

| 验收点 | 机制 | 参考答案来源 |
|---|---|---|
| HKDF 正确性 | RFC 5869 向量 × 双后端 | RFC 5869（外部标准） |
| 树派生正确性 | 黄金树向量比对 | `tools/independent_vector_gen.py`（PyCryptodome 独立实现，不导入被测核心） |
| 编码无歧义 | 拼接碰撞反例 + 严格解码 | 构造性反例 |
| 确定性/分离性 | 服务级断言 | 服务自身（性质测试，非结果来源） |
| 错误类别 | 逐类触发并断言 category 与 HTTP 状态 | 构造性故障注入 |
| 日志卫生 | 扫描日志/审计/注册表 | 已知密钥材料 |

## 7. 无法在本环境执行的检查（不声称已通过）

- **恒定时间性**：后端（OpenSSL/PyCryptodome）的侧信道性质无法在本地
  测试环境核验，依赖上游安全公告。
- **密钥材料的内存清零**：Python 无法保证 bytes 对象的及时清零；
  本服务为本地测试用途，未做 mlock/显式擦除。
- **并发派生的吞吐与锁竞争**：SQLite 单写者模型下的性能未做压测。
- **持久化根的机密性**：测试根以 hex 明文存于本地 SQLite（夹具根甚至
  提交在仓库中）。这是本地测试语义，不是生产密钥管理方案；生产化需
  替换为 KMS/HSM 信封加密，本仓库不覆盖。
- **跨进程/跨主机一致性**：未部署多实例，未验证。
