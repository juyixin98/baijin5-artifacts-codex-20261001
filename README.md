# paillier-aggregate-local

基于成熟 Paillier 库 [`phe`](https://github.com/data61/python-paillier) 的**本地测试用**密文求和与加权聚合后端服务。纯本地运行：合成夹具数据、本地 SQLite、本地密钥，无任何外部账号或真实业务数据。

## 支持范围（明确边界）

**支持**（Paillier 算法真实具备的运算）：

- 密文加法：`E(a) · E(b) = E(a + b)`
- 明文标量乘（含负权重）：`E(a)^k = E(k·a)`，`k` 为整数
- 由以上两者组成的**加权和 / 求和**聚合

**不支持**（本服务不声称提供通用密文计算）：

- 密文 × 密文乘法
- 密文比较、分支、除法
- 任何需要在密文上做的非线性运算

`/meta` 接口会如实报告上述支持/不支持清单。

## 核心算法与状态处理

### 有符号编码与总和上界

明文是 `Z_n` 中的剩余类。有符号整数按 `v mod n` 编码。每个批次在创建时绑定一组编码参数（与密钥一同入库，不可更改）：

| 参数 | 默认 | 含义 |
|---|---|---|
| `max_plaintext_abs` | 10⁶ | 单条贡献明文 `\|m\| ≤` 该值（客户端编码时强制） |
| `max_coefficient_abs` | 10³ | 单条贡献权重 `\|w\| ≤` 该值（服务端强制） |
| `max_aggregate_abs` | 10⁹ | 加权和真值必须落在 `[-B, B]` 内，且要求 `2B < n` |

解码规则：

- 剩余 `raw ≤ B` → 正数 `raw`
- `raw ≥ n - B` → 负数 `raw - n`
- 其余（**模回绕已经发生**）→ 返回 `DECODE_AMBIGUOUS` 错误（HTTP 409），**绝不**把回绕结果解释成普通负值。

服务端对每条贡献按 `|w| · max_plaintext_abs` 累计最坏情形上界 `bound_used`，一旦可能超过 `max_aggregate_abs` 即拒绝该贡献（`AGGREGATE_BOUND_EXCEEDED`），从协议上保证正常流程不会进入歧义区；恶意客户端绕过客户端范围检查的情形由解码时的歧义检测兜底（见测试 `test_decode_ambiguous_on_malicious_plaintext`）。

### 密钥与批次绑定

每个批次生成独立密钥对，`key_id = sha256(n)` 随批次下发。提交贡献时必须携带加密所用的 `key_id`，与批次不符即拒绝（`KEY_MISMATCH`），防止不同密钥的密文混入同一聚合。密文还需满足 `0 ≤ c < n²` 的形状检查。

### 批次状态机

```
OPEN --(submit)--> OPEN --(aggregate)--> AGGREGATED --(decrypt)--> DECRYPTED
```

聚合后批次关闭，不再接受新贡献，保证已审计结果稳定。

## 工程结构

```
app/
  encoding.py        协议编码层：有符号编码、范围检查、歧义区判定
  crypto_adapter.py  密码适配层：唯一依赖 phe 的模块（密钥/加解密/同态运算）
  service.py         批次生命周期与聚合编排（含最坏情形上界跟踪）
  verifier.py        独立验证层（不复用被测核心代码，见下）
  storage.py         SQLite 状态（批次/贡献/聚合/审计，大整数存十进制字符串）
  audit.py           审计日志（每条记录带 run_id）
  config.py          环境变量配置
  main.py            FastAPI HTTP 层，结构化错误（category + message + detail）
  errors.py          错误分类法（11 类，映射到 HTTP 状态码）
tests/
  reference.py       独立参考实现（教科书 Paillier + 纯整数明文参考）
  test_encoding.py / test_crypto_adapter.py / test_aggregation.py / test_api.py
scripts/
  run_server.sh      本地启动
  client_encrypt.py  客户端加密夹具（明文不离开客户端）
  demo_flow.sh       端到端演示（手算期望值 -11）
examples/requests.json  示例请求结构
reports/             每次 pytest 运行的日志（run_id 可关联）
```

### 独立验证层

`verifier.py` 不调用被测核心的聚合与解码代码，而是独立地：

1. 用 `∏ c_i^(w_i mod n) mod n²` 重新聚合密文，与服务的聚合密文**逐位比对**（同态运算无再随机化，结果确定）；
2. 用教科书 Paillier 解密（`λ=lcm(p-1,q-1)`, `μ=λ⁻¹ mod n`, `g=n+1`）独立解密并独立解码；
3. 用纯整数算术从明文夹具重算参考值 `Σ w_i·m_i`；
4. 三方（参考值 / 独立解密 / 服务解密结果）交叉一致才判 `PASS`；夹具缺失判 `UNVERIFIABLE`，**不会**缺省判成功。

测试中的参考答案来自手算常数与 `tests/reference.py`，不是由被测核心生成。

## 信任假设（本地测试部署）

- **服务端持有私钥**，即服务端同时扮演解密方。真实部署中解密方应是独立参与方或门限方案，本服务不做此声称。
- **明文范围检查在客户端**（`scripts/client_encrypt.py` 演示了这一分工）：服务端无法检查密文内容，只能强制密钥绑定、权重范围、密文形状与最坏情形上界。恶意客户端可提交超范围明文，此时解码会以 `DECODE_AMBIGUOUS` 失败关闭，而不会产生貌似合理的结果。
- `plaintext_fixture` 字段仅供本地测试验证层重算参考值，由 `PAILLIER_ACCEPT_PLAINTEXT_FIXTURES=false` 可关闭；任何真实部署都不应存在该字段。
- 参与者身份仅作审计标签，无认证机制（本地测试范围外）。

## 本地启动

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt   # 全部依赖已锁定精确版本
./scripts/run_server.sh                     # http://127.0.0.1:8000
```

配置（环境变量）：`PAILLIER_DB_PATH`、`PAILLIER_KEY_SIZE`（默认 2048）、
`PAILLIER_MAX_PLAINTEXT_ABS`、`PAILLIER_MAX_COEFFICIENT_ABS`、`PAILLIER_MAX_AGGREGATE_ABS`、
`PAILLIER_ACCEPT_PLAINTEXT_FIXTURES`。

## 示例请求

端到端演示（手算期望 `-11`：`2·3 + 5·(-2) + (-1)·7`）：

```bash
./scripts/demo_flow.sh
```

手动流程（结构见 `examples/requests.json`）：

```bash
# 1. 创建批次（返回 batch_id / key_id / public_key_n）
curl -s -X POST localhost:8000/batches -H 'Content-Type: application/json' \
  -d '{"label":"demo","key_size":2048,"max_plaintext_abs":1000000,
       "max_coefficient_abs":1000,"max_aggregate_abs":1000000000}'

# 2. 客户端本地加密（明文不发给服务端）
.venv/bin/python scripts/client_encrypt.py <public_key_n> 3
# -> {"value": 3, "ciphertext": "..."}

# 3. 提交密文 + 权重
curl -s -X POST localhost:8000/batches/<batch_id>/contributions \
  -H 'Content-Type: application/json' \
  -d '{"participant_id":"p0","key_id":"<key_id>","ciphertext":"<...>",
       "coefficient":2,"plaintext_fixture":3}'

# 4. 聚合 / 5. 解密 / 6. 独立验证 / 7. 审计
curl -s -X POST localhost:8000/batches/<batch_id>/aggregate
curl -s -X POST localhost:8000/batches/<batch_id>/decrypt
curl -s -X POST localhost:8000/batches/<batch_id>/verify
curl -s      localhost:8000/batches/<batch_id>/audit
```

错误响应统一为结构化 JSON，例如：

```json
{"error": {"category": "DECODE_AMBIGUOUS", "message": "...", "detail": {...}}}
```

## 测试

```bash
.venv/bin/python -m pytest        # 41 个用例
```

覆盖：手算小整数加权和、负权重、零权重、明文/权重超范围、不同密钥混合（含伪造 `key_id`）、聚合上界拒绝、恶意明文导致的歧义解码、状态机、篡改夹具后验证失败、无夹具时判 `UNVERIFIABLE`、HTTP 全链路与结构化错误。

每次运行在 `reports/test_run_<时间戳>_<run_id>.log` 写日志：版本信息（python/phe/fastapi/cryptography/pytest）、逐用例进度与结果、关键用例的输入/期望/实际值。审计表中的每条记录也带 `run_id`，可与测试日志互相关联。

`reports/` 保留了完整运行历史（含失败项，未删除）：

| 运行 | 结果 | 说明 |
|---|---|---|
| `...T000221Z_26be84414b9a` | 40 passed / **1 failed** | 伪造 key_id 用例：外key密文不满足本批次 `n²` 形状检查，在提交期即被拒绝（`CIPHERTEXT_INVALID`），用例改为接受三种失败关闭路径之一 |
| `...T000257Z_e07933ab79c2` | 37 passed / **4 failed** | phe 对负标量乘使用模逆代表元，与教科书 `c^(k mod n)` 参考不一致 → 适配层改为规范形式（见"关键取舍"） |
| `...T000316Z_e5207e170cf0` | 0 passed / **1 failed**（`-x` 提前停止） | 同上问题的中间修复轮次 |
| `...T000513Z_6e537d42de8c` | **41 passed** | 修复后首次全绿 |
| `...T001402Z_233caaa335d9` | **41 passed / 0 failed / 0 skipped** | 最终运行，无未执行项 |

最终运行无失败、无跳过；唯一的警告是 starlette 关于 httpx TestClient 的弃用提示，不影响判定。

## 关键取舍

1. **phe 负标量乘的代表元问题**：phe 对负标量用模逆技巧产生等价但不同的密文代表元。为保证聚合结果确定且可被独立验证层逐位复算，适配层对标量乘使用教科书规范形式 `c^(k mod n) mod n²`（非负标量与 phe 结果完全一致；密钥生成/加解密仍全部使用 phe）。
2. **同态运算不再随机化**：牺牲密文不可区分性换取可审计的确定性复算——本地测试场景下这是想要的行为。
3. **私钥入库**：仅为本地测试方便，README 与代码注释均已标注；真实部署必须拆分解密方。
4. **大整数以十进制字符串传输/存储**：避免 JSON 数值精度丢失。
5. **无浮点定点编码**：只支持整数明文，避免引入精度与指数对齐的复杂度（phe 的 `EncodedNumber` 浮点编码未使用）。
