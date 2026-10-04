# commit-reveal-draw

本地多参与者**承诺—揭示（commit-reveal）协议**后端，用组合种子驱动确定性抽取。
纯本地运行：所有参与者、随机值、盐都是合成夹具，不需要任何生产账号或真实业务数据。

> ⚠️ **适用范围**：本工程用于演示和验证协议机制，**不用于真实博彩或资金抽奖**。
> 协议存在选择性弃权（selective abort）偏置，见「协议局限」。

## 模块划分

```
src/commit_reveal/
├── protocol/     # 协议编码：长度前缀规范化编码、域分离标签、类型化错误（含失败类别）
├── crypto/       # 成熟密码库适配：commitment.py 用 cryptography(SHA-256)，
│                 #   seed.py 用 PyCryptodome(HKDF-SHA256 种子、SHAKE256 抽取流)
├── draw/         # 确定性抽取：拒绝采样的 Fisher-Yates，无模偏置
├── state/        # SQLite 存储、轮次状态机（commit→冻结→reveal→finalize）、审计日志
├── verify/       # 独立验证：仅凭公开证据 JSON 离线重算全部派生值
└── api/          # FastAPI 表面：请求 ID 中间件、错误类别映射
tests/
├── helpers/reference_impl.py   # 仅标准库的独立参考实现（hashlib/hmac 手写 HKDF）
├── fixtures/reference_vectors.json  # 冻结参考向量（由参考实现生成，非被测核心）
└── test_*.py                   # 按场景组织的测试
```

## 协议规范（crp-v1）

**编码**：所有被哈希结构都是长度前缀字段序列（2 字节大端长度 + 原始字节），
带版本化域分离标签（`CRP-COMMIT-v1` / `CRP-SEED-v1` / `CRP-SET-v1`）。

**承诺**（绑定参与者与轮次）：

```
C = SHA-256( encode(DOMAIN, round_id, participant_id, value[32B], salt[32B]) )
```

**阶段**（由注入时钟判定，测试用 ManualClock）：

| 时间区间 | 允许操作 |
|---|---|
| `now < commit_deadline` | 提交承诺（每人一条，不可修改） |
| `commit_deadline ≤ now < reveal_deadline` | 承诺集合已冻结；提交揭示 |
| `now ≥ reveal_deadline` | 可 finalize |

**揭示验证**：服务端重算承诺并与冻结集合中的存储值常数时间比较；
随机值与盐都必须匹配，否则拒绝（`COMMITMENT_MISMATCH`）。每个参与者的揭示只计入一次
（重复揭示 → `DUPLICATE_REVEAL`）。

**种子与抽取**：

```
master = 拼接(排序后的 length-prefixed 揭示值)
seed   = HKDF-SHA256(master, salt=round_id, info="CRP-SEED-v1")
ranking = Fisher-Yates(eligible=已揭示参与者, stream=SHAKE256(seed))  # 拒绝采样，无模偏置
winner = ranking[0]
```

**未揭示处理**：截止后未揭示的承诺被**排除在种子之外**，finalize 照常进行
（若揭示数 < `min_reveals` 则整轮中止，审计记 `UNDECIDABLE`）。
证据中列出 `unrevealed_commitments` 并置 `bias_warning=true`。

**公开证据**：finalize 产出的 evidence JSON 包含轮次配置、冻结承诺集合及其哈希、
全部揭示（值+盐）、种子、排名、获胜者与偏置警示。任何人可离线重算：

```bash
PYTHONPATH=src python3 -m commit_reveal.verify.independent evidence.json
```

## 协议局限（务必阅读）

1. **选择性弃权偏置**：揭示阶段中，后揭示者能看到先揭示者的值。若某个参与者
   发现自己的输入会导致不利结果，可以选择不揭示，把自己的输入从种子中剔除，
   从而偏置结果。测试 `test_abstention.py` 用冻结向量实证了这一点：
   carol 弃权使获胜者从 bob 变为 dave。缓解方向（未实现）：
   揭示值同时提交、惩罚保证金、或使用阈值方案/VRF。
2. **无活性的惩罚**：弃权者除被点名外无任何代价。
3. **最后揭示者优势**：最后一个揭示者在揭示前已能离线算出完整结果。
4. 因此本协议**不适用于真实资金场景**。

## 本地启动

```bash
pip install -r requirements.txt        # 或用 requirements-lock.txt 精确复现
cp .env.example .env                    # 可选，全部为本地默认值
./scripts/run_dev.sh                    # 监听 127.0.0.1:8529
```

## 示例请求

```bash
# 1. 创建轮次（两个截止时刻，ISO-8601 带时区）
curl -s -X POST localhost:8529/rounds -H 'Content-Type: application/json' -d '{
  "participants": ["alice", "bob", "carol"],
  "commit_deadline": "2026-10-04T12:00:00+00:00",
  "reveal_deadline": "2026-10-04T12:10:00+00:00",
  "min_reveals": 2
}'

# 2. 提交承诺（客户端本地计算，值与盐不出本机）
curl -s -X POST localhost:8529/rounds/<rid>/commitments -H 'Content-Type: application/json' -d '{
  "participant_id": "alice",
  "commitment": "<64 hex>"
}'

# 3. 截止后揭示（值+盐，服务端重算验证）
curl -s -X POST localhost:8529/rounds/<rid>/reveals -H 'Content-Type: application/json' -d '{
  "participant_id": "alice", "value": "<64 hex>", "salt": "<64 hex>"
}'

# 4. 揭示截止后定稿，取公开证据与审计
curl -s -X POST localhost:8529/rounds/<rid>/finalize
curl -s localhost:8529/rounds/<rid>/evidence
curl -s localhost:8529/rounds/<rid>/audit
```

一键演示（自动走完全流程并离线验证证据）：

```bash
./scripts/run_dev.sh &                  # 终端 1
PYTHONPATH=src python3 scripts/demo_flow.py   # 终端 2
```

错误响应统一携带失败类别，便于客户端分支处理：

```json
{"error": {"category": "COMMITMENT_MISMATCH", "message": "...", "request_id": "..."}}
```

类别一览：`ROUND_NOT_FOUND`、`UNKNOWN_PARTICIPANT`、`LATE_COMMITMENT`、
`DUPLICATE_COMMITMENT`、`NO_COMMITMENT`、`REVEAL_PHASE_NOT_OPEN`、`LATE_REVEAL`、
`COMMITMENT_MISMATCH`、`DUPLICATE_REVEAL`、`ROUND_STATE`。

## 诊断与审计

- 每个请求分配 `X-Request-ID`（客户端可用同名头指定），贯穿响应、审计表与结构化日志。
- 审计落库（`audit_log` 表）并输出 JSON 日志行：事件、决策（ACCEPT/REJECT/UNDECIDABLE）、
  理由、轮次、参与者标签。
- **脱敏**：日志只出现参与者 ID 的 SHA-256 截断标签和值/盐的指纹，
  绝不打印原始随机值或盐。

## 测试

```bash
python3 -m pytest            # 36 个测试
```

测试的参考答案来自 `tests/helpers/reference_impl.py`（仅标准库的独立实现，
HKDF 为手写 RFC 5869）和 `tests/fixtures/reference_vectors.json`（由参考实现
生成后冻结的常量），**不由被测核心实现生成**。覆盖：全揭示、错误盐、迟到承诺、
重复揭示、选择性弃权、不足 quorum 中止、回放一致（同库重载/异库重跑/CLI 验证）、
篡改证据检测、HTTP 端到端。运行记录见 `docs/TEST_REPORT.md`。

## 关键取舍

- **一人一次承诺、不可修改**：简化状态机，避免截止前的替换歧义；代价是输错需新开一轮。
- **未揭示即排除而非中止**：保证活性，但引入选择性弃权偏置——已在证据中显式标注。
- **时钟注入**：截止判定依赖注入时钟，测试可精确跨界；生产部署须信任宿主机时间。
- **验证器共享编码/密码适配层**：独立验证器与被测服务共用 `protocol.encoding` 与
  `crypto.*`（它们是公开规范），但不读服务端数据库，仅以证据 JSON 为输入；
  测试另用纯标准库参考实现交叉锚定，避免单一实现自证。
