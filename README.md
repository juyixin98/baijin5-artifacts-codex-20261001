# kds — 本地测试根密钥派生树服务

面向本地测试的根密钥派生树服务：从单一本地合成根密钥出发，按
**租户 → 用途 → 版本 → 上下文** 四层派生，再按请求长度输出最终密钥。
所有数据均为本地合成夹具，不依赖任何生产账号或真实业务数据。

## 运行

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python scripts/gen_fixtures.py   # 生成/重建合成夹具（确定性）
.venv/bin/python -m pytest                 # 测试套件
.venv/bin/python scripts/verify.py         # 独立验证脚本（非 pytest）
```

启动 HTTP 服务（根密钥从环境变量注入，不写入日志）：

```bash
export KDS_ROOT_KEY_HEX=$(cat fixtures/root_key.hex)
.venv/bin/python scripts/serve.py   # 监听 127.0.0.1:8471，状态落在 fixtures/kds.sqlite3
```

## 模块边界与数据/错误契约

| 模块 | 职责 | 边界约定 |
|---|---|---|
| `kds/encoding.py` | 协议编码 | 标签唯一的字节化方式：类型标签 + 长度前缀 TLV，禁止裸字符串拼接；编码是单射 |
| `kds/crypto_adapter.py` | 成熟密码适配 | 仅封装 `cryptography`（主）与 PyCryptodome（独立参照）的 HKDF-SHA256；本仓库不实现任何哈希/HMAC 原语 |
| `kds/identity.py` | 密钥身份 | `key_id = "kds1-" + SHA256(身份元组编码)[:16]`；显示名只是元数据，绝不参与身份 |
| `kds/state.py` | 状态与审计 | SQLite 登记表 + 审计表；密钥材料永不进入该层，只有 key_id、请求摘要、结果与原因 |
| `kds/service.py` | 派生树 | 组合上述边界；每次操作带 run_id 并落审计 |
| `kds/api.py` | HTTP 表面 | 把错误类别映射为 HTTP 状态码 |

### 派生语义

- 盐角色固定为协议常量 `FIXED_SALT`；全部上下文隔离都在 `info` 中，
  `info` 只能由 `kds.encoding` 生成（每层带层级标签，输出层绑定 `key_id`）。
- 派生长度上限为 HKDF-SHA256 算法上限 `255 × 32 = 8160` 字节。
- 确定性：相同（根， 身份， 长度）→ 相同输出；任一维度不同 → 输出分离。
- 错误类别（`KdsError.category`，机器可读）：
  `input`（请求非法，HTTP 400）、`state_conflict`（与持久状态冲突，409）、
  `resource_exhausted`（超算法上限或租户配额，413）、`computation`（密码后端异常，500）。
- 诊断日志与审计只含 run_id、key_id、请求摘要、结果与原因类别；
  原始根密钥与派生密钥不进入日志（有专门测试断言）。

## 验证策略

- **黄金向量**：RFC 5869 Appendix A 三组 HKDF-SHA256 向量，期望值誊自 RFC，
  对主后端与参照后端分别断言（`tests/test_hkdf_golden.py`）。
- **碰撞反例**：`("ab","c")` vs `("a","bc")` 在裸拼接下相同，本编码必须区分，
  且两个对应派生密钥必须不同（`tests/test_encoding.py`、`tests/test_service.py`）。
- **独立重算**：服务输出的期望值由 PyCryptodome 参照后端独立重算，
  不由被测主路径自产（`test_output_matches_independent_reference`）。
- **失败类别**：输入错误 / 状态冲突 / 资源耗尽 / 计算失败分别断言具体异常类型，
  而非“接口能调用”。
- `scripts/verify.py` 在 pytest 之外独立重放上述验收点，打印 run_id 与 PASS/FAIL，
  失败时退出码非零。

## 夹具

`fixtures/root_key.hex`（由固定公开种子经 SHA-256 确定性生成的**测试**根密钥）
与 `fixtures/tenants.json`（合成租户；其中两个租户故意共用显示名，用于验证
“身份不可由显示名混淆”）。重建：`python scripts/gen_fixtures.py`。

## 无法在本环境执行的检查（未执行，不计为通过）

- 多进程/多实例并发下 SQLite 审计写入的竞态验证（当前仅单进程 + 线程锁）。
- 真实 HSM/KMS 后端替换根密钥来源的集成测试（本地仅支持环境变量/文件注入）。
- 长时间运行的资源泄漏与审计表增长的 soak 测试。
- 针对 HTTP 层的模糊测试（当前覆盖 schema 校验与错误映射，未做系统性 fuzz）。
