# commit-reveal-draw

本地多参与者**承诺-揭示（commit-reveal）协议**后端：参与者先提交承诺，承诺集冻结后揭示随机值与盐，系统把有效揭示组合成种子，驱动一次**确定性、可独立重算**的抽取。

> **适用范围（重要）**：本工程是协议教学/演示实现，使用本地合成夹具，**不用于真实博彩或资金抽奖**。原因见下文「协议局限」。

## 模块划分

```
commit_reveal/
├── protocol/encoding.py      # 规范编码：长度前缀拼接 + 域分离标签（唯一字节布局来源）
├── crypto/
│   ├── commitment.py         # 承诺与种子哈希（cryptography 包，SHA-256）
│   └── draw.py               # 种子驱动确定性抽取（PyCryptodome HMAC-SHA256，拒绝采样去模偏置）
├── services/round_service.py # 协议状态机与规则执行（OPEN → FROZEN → FINALIZED）
├── state/
│   ├── db.py                 # SQLite 连接与 schema
│   ├── repository.py         # 类型化存取（无协议规则）
│   └── audit.py              # 追加式审计：请求标识、接受/拒绝原因、敏感字段脱敏
├── verify/verifier.py        # 独立验证：仅凭公开证据重算承诺、种子、抽取
├── app.py                    # FastAPI HTTP 表面（请求 ID 中间件、结构化错误）
└── config.py                 # 环境变量配置
tests/                        # 独立组织的测试（服务层 + API 层 + 独立测试向量）
scripts/verify_evidence.py    # 离线证据验证 CLI
examples/demo.sh              # 端到端示例请求（curl）
```

## 协议定义

**承诺**（绑定参与者与轮次，盐防字典攻击）：

```
commitment = SHA-256("CRP1-COMMIT-v1" ‖ enc(round_id) ‖ enc(participant_id)
                                     ‖ enc(random_value) ‖ enc(salt))
```

**种子**（仅组合有效揭示，按参与者 ID 排序，与到达顺序无关）：

```
seed = SHA-256("CRP1-SEED-v1" ‖ enc(round_id) ‖ enc(pid₁) ‖ enc(value₁) ‖ …)
```

**抽取**（无模偏置，确定性）：

```
blockᵢ = HMAC-SHA256(key=seed, "CRP1-DRAW-v1" ‖ i)   # 取前 128 bit
winner_index = 首个 < 2¹²⁸−(2¹²⁸ mod n) 的 blockᵢ mod n   # 拒绝采样
```

`enc(...)` 为 4 字节大端长度前缀拼接，保证字段边界无歧义。`random_value` ≥ 32 字节、`salt` ≥ 16 字节，均为小写 hex 传输。

## 生命周期与规则

```
OPEN ──(承诺截止)──> FROZEN ──(揭示截止 或 全部已揭示)──> FINALIZED
```

- **OPEN**：接受承诺；拒绝揭示（`REVEAL_BEFORE_FREEZE`）。
- **截止后承诺集冻结**：迟到承诺拒绝（`LATE_COMMIT`）；重复承诺拒绝（`DUPLICATE_COMMIT`，承诺具有绑定性，不可覆盖）。
- **FROZEN**：接受揭示；服务端重算承诺比对，错误盐/随机值拒绝（`COMMITMENT_MISMATCH`）且**不计入**；重复揭示拒绝（`DUPLICATE_REVEAL`，揭示只计入一次）；截止后揭示拒绝（`LATE_REVEAL`）。
- **FINALIZED**：终态。未揭示的承诺被**排除在种子与候选集之外**，并记录在证据的 `unrevealed_commitments` 中。零有效揭示时拒绝定案（`NO_VALID_REVEALS`，审计记为 `UNDETERMINED`）。

## 协议局限（务必阅读）

1. **选择性弃权可偏置结果**：最后揭示者在看到他人揭示后，若不喜欢即将到来的结果，可以拒绝揭示。弃权者被排除会改变种子与候选集，从而偏置结果。本实现把弃权者**排除并公开记录**（可审计、可追责），但无法消除偏置。正式场景需要阈值加密、保证金惩罚或可信第三方，均不在本工程范围。
2. **无身份与传输安全**：本地演示无认证、无 TLS；参与者身份只是字符串。
3. **单进程 SQLite**：适合本地单写者部署，非高可用设计。

因此：**不用于真实博彩或资金抽奖。**

## 快速开始

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-lock.txt   # 锁定依赖；或 requirements.txt

# 启动服务（默认 127.0.0.1:8000，数据库 var/commit_reveal.db）
.venv/bin/uvicorn commit_reveal.app:app --host 127.0.0.1 --port 8000

# 端到端示例（创建轮次 → 承诺 → 迟到承诺被拒 → 错误盐被拒 → 揭示 → 定案 → 离线验证）
./examples/demo.sh
```

配置通过环境变量：`CRP_DB_PATH`（默认 `var/commit_reveal.db`）、`CRP_HOST`、`CRP_PORT`。

## 示例请求

```bash
# 创建轮次（截止时间为 Unix 秒）
curl -X POST localhost:8000/rounds -H 'Content-Type: application/json' -d '{
  "round_id": "r1", "participants": ["alice", "bob"],
  "commit_deadline": 1760000000, "reveal_deadline": 1760003600}'

# 提交承诺（承诺值可用任何 SHA-256 工具按上式计算；examples/demo.sh 用纯 hashlib 内联计算）
curl -X POST localhost:8000/rounds/r1/commitments -H 'Content-Type: application/json' \
  -d '{"participant_id": "alice", "commitment": "<64 hex>"}'

# 揭示
curl -X POST localhost:8000/rounds/r1/reveals -H 'Content-Type: application/json' \
  -d '{"participant_id": "alice", "random_value": "<64+ hex>", "salt": "<32+ hex>"}'

# 定案 / 取证据 / 服务端验证 / 审计
curl -X POST localhost:8000/rounds/r1/finalize
curl       localhost:8000/rounds/r1/evidence
curl -X POST localhost:8000/rounds/r1/verify
curl       localhost:8000/rounds/r1/audit

# 离线独立验证（不依赖服务端）
python3 scripts/verify_evidence.py evidence.json
```

错误响应统一为 `{"error": {"code", "reason", "request_id"}}`，`code` 为稳定失败类别（见 `commit_reveal/errors.py`），`request_id` 与响应头 `X-Request-ID` 一致，可在审计轨迹中定位该次决策。

## 诊断与审计

- 每个请求分配请求 ID（也可用 `X-Request-ID` 头自带），贯穿日志与审计。
- 审计表记录：时间、请求 ID、轮次、事件类型、结果（`ACCEPTED`/`REJECTED`/`UNDETERMINED`）、原因（失败类别）、关键状态（如当时轮次状态、已承诺/已揭示数）。
- **敏感数据脱敏**：`random_value` 与 `salt` 在审计与日志中只出现 `sha256:<前12位>` 指纹，永不打印明文。

## 测试

```bash
.venv/bin/python -m pytest            # 43 个测试
.venv/bin/python -m pytest --cov=commit_reveal --cov-report=term-missing
```

当前结果：**43 passed，覆盖率 97%**，无跳过、无预期失败项。

验收场景覆盖：

| 场景 | 测试 | 断言 |
|---|---|---|
| 全揭示 | `test_full_reveal.py` | 定案、证据可验证、胜者属于候选集 |
| 回放一致 | `test_full_reveal.py::test_replay_is_deterministic` | 两个全新实例同输入 → 证据逐字节相同 |
| 错误盐 | `test_wrong_salt.py` | `COMMITMENT_MISMATCH`、不计入、审计脱敏 |
| 迟到承诺 | `test_late_commit.py` | `LATE_COMMIT`、冻结集不变 |
| 选择性弃权 | `test_abstain.py` | 弃权者被排除、种子改变（偏置风险实证）、零揭示不可定案 |
| 重复计入 | `test_duplicates.py` | `DUPLICATE_COMMIT` / `DUPLICATE_REVEAL` |
| 独立验证 | `test_verifier.py` | 篡改胜者/种子/揭示/弃权名单均被检出 |
| API 端到端 | `test_api.py` | 请求 ID、结构化错误、审计轨迹 |

**独立参考答案**：`tests/test_vectors.py` 中的承诺/种子/抽取期望值**不是**由被测实现生成，而是用纯 `hashlib` / 标准库 `hmac` 独立重算后硬编码的测试向量（生成脚本即 README 协议定义的直接翻译），防止实现静默改变字节布局。

## 关键取舍

- **惰性状态推进**：OPEN→FROZEN 在请求到达时按时钟判断，无后台定时器，简单且可测试（时钟注入）。
- **弃权者排除而非惩罚**：实现简单、轮次总能定案，代价是上述偏置风险——已明确记录并测试实证。
- **拒绝采样去模偏置**：128 bit 样本空间，拒绝概率可忽略，保证任意候选数下分布均匀。
- **证据自包含**：`evidence` JSON 包含验证所需全部公开数据（承诺、揭示、种子、算法标识、候选集），验证器不导入服务层，可离线运行。
