# 可解释可废止推理后端（Defeasible Reasoning Backend）

一个采用**明确定义的受限语义**的可废止推理服务：规则分为不可撤销的**严格规则**与可被推翻的
**默认规则**，优先关系必须无环；结论相反且优先级不可比时**保留冲突**，绝不按规则出现顺序裁决；
**缺少证据不等于反面事实**。每次查询都返回**支持链 / 击败链 / 悬而未决链**及判断理由，
并记录可重放的运行编号与关键中间状态。

技术栈：Python 3.10+ · FastAPI · SQLite · 纯本地合成夹具，无需任何外部账号或业务数据。

---

## 1. 一分钟运行

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 载入合成样例（鸟类 / 尼克松菱形 / 团队击败 / 优先环反例）
PYTHONPATH=src python3 -m defeasible.cli load-fixture fixtures/birds.json
PYTHONPATH=src python3 -m defeasible.cli load-fixture fixtures/nixon_diamond.json

# 启动服务（默认 127.0.0.1:8000）
PYTHONPATH=src python3 -m defeasible.cli serve
```

另开一个终端：

```bash
# 普通鸟 Tweety -> 会飞（proved）
curl -s -XPOST localhost:8000/cases/birds/query \
  -H 'Content-Type: application/json' \
  -d '{"literal":"flies(tweety)"}'

# 企鹅 Polly -> 不会飞（refuted），击败链指认更高优先级的 r_penguin_not
curl -s -XPOST localhost:8000/cases/birds/query \
  -H 'Content-Type: application/json' \
  -d '{"literal":"flies(polly)"}'
```

也可以直接 `pip install -e .` 后使用 `defeasible serve` / `defeasible load-fixture ...`。

### 不启动服务，直接用内核

```python
from defeasible.language import Theory, Term
from defeasible.engine import Engine

t = Theory()
t.add_fact("bird(tweety)", fact_id="f1")
t.add_rule("r_bird", "default", ["bird(X)"], "flies(X)")
t.add_rule("r_peng", "default", ["penguin(X)"], "-flies(X)")
t.add_rule("s_pb",   "strict",  ["penguin(X)"], "bird(X)")
t.add_priority("r_peng", "r_bird")          # 企鹅规则优先

ev = Engine().evaluate(t, [Term.parse("penguin(polly)")])
print(ev.query("flies(tweety)").status.value)   # proved
print(ev.query("flies(polly)").status.value)    # refuted
print(ev.query("flies(ghost)").status.value)    # unknown（无证据，不是反面）
```

---

## 2. 受限语义（明确、可核验）

完整形式化见 [`docs/SEMANTICS.md`](docs/SEMANTICS.md)，此处给出要点。

对每个基文字 `L` 计算三个证明标记：

| 标记 | 含义 |
|------|------|
| `definite(L)` | 仅用事实与**严格规则**得到的严格证明 |
| `supported(L)` | 存在一条支持论证链；只有**严格更强**的反方支持链能击败它 |
| `provable(L)` | 强的、未被击败的证明（歧义传播，见下） |

查询状态为四值之一：

| 状态 | 条件 |
|------|------|
| `proved` | `provable(L)` |
| `refuted` | `provable(-L)`（有明确的反面证明） |
| `conflict` | 双方均 `supported` 但都不可证，且优先级**不可比** |
| `unknown` | 以上皆非（包括“根本没有证据”） |

四条验收规则如何被满足：

1. **严格 / 默认可撤销分离，优先无环** — 规则带 `strict`/`default` 种类；
   加载与求值时校验优先图，发现环即返回 `state_conflict` 并给出环路径。
2. **相反结论不可比时保留冲突** — 尼克松菱形（`pacifist` ↔ `-pacifist`，无优先级）
   双方都得到 `conflict`，实现是良基模型（well-founded model），集合不动点与顺序无关。
3. **缺少证据 ≠ 反面事实** — `flies(ghost)` 在没有任何相关证据时是 `unknown`；
   存储层也不会因为存了 `P` 就返回 `-P`。
4. **结果列出三类链** — 查询响应含 `chains.support / chains.defeat / chains.pending`，
   每条链带完整规则步骤、变量绑定、击败者与中文判断理由。

**歧义传播（ambiguity propagation）**：一个在支持层有争议的文字会阻止建立在它之上的证明，
争议向结论链上方传递而不是被偶然消解。`provable` 层的“被击败”条件引用的是反方规则的
**supported 适用性**，详见语义文档与 `team_defeat` 样例。

---

## 3. 规则语言（精简且受限）

```
strict  : penguin(X) => bird(X)          # 严格、不可撤销
default : bird(X)    => flies(X)         # 默认、可推翻
default : penguin(X) => -flies(X)        # 经典否定 - 是唯一的对立连接词
priority: r_penguin > r_bird             # 仅允许在默认规则之间，且必须无环
```

- 变量**大写开头**，常量**小写开头**；谓词小写。
- 规则必须**范围受限**：头部出现的每个变量都必须出现在某正文字中（不臆造常量）。
- 没有“否定即失败”连接词；查不到就是未知。

理论用 JSON 表示（见 `fixtures/*.json`）：

```json
{
  "rules": [
    {"id": "r_bird", "kind": "default", "body": ["bird(X)"], "head": "flies(X)"}
  ],
  "priorities": [{"higher": "r_penguin", "lower": "r_bird"}]
}
```

---

## 4. HTTP 接口

| 方法 | 路径 | 作用 |
|------|------|------|
| POST | `/cases/{id}/theory` | 上传（替换）规则库，校验无环与范围受限 |
| PUT  | `/cases/{id}/evidence` | 追加基证据 |
| GET  | `/cases` / `/cases/{id}` | 列案例 / 查看理论+证据快照 |
| POST | `/cases/{id}/evaluate` | 全结论表 |
| POST | `/cases/{id}/query` | 单文字查询：状态 + 三类链 + 理由 |
| GET  | `/runs/{run_id}` | 按运行编号重放（中间状态 + 理由） |
| GET  | `/health` | 存活探针 |

查询响应骨架：

```json
{
  "run_id": "run-9a871f212ea6",
  "literal": "flies(polly)",
  "status": "refuted",
  "flags": {"definite": false, "supported": false, "opposite_supported": true},
  "chains": {
    "support": [],
    "defeat":  [ {"chain_id": "c1", "reason": "…被 r_penguin_not 击败…",
                  "attacker_top_rule": "r_penguin_not", "steps": [ … ]} ],
    "pending": []
  },
  "evaluation": { "ground_rule_count": 6, "rounds_used": 5, "conclusions": [ … ] }
}
```

### 四类失败可区分

| code | HTTP | 触发 |
|------|------|------|
| `invalid_input` | 422 | 解析错误、变量未绑定、未知案例/运行等输入问题 |
| `state_conflict` | 409 | 优先环、严格规则推出相反结论（理论本身不一致） |
| `resource_exhausted` | 509 | 命中 grounding / 论证链 / 不动点轮数预算 |
| `computation_failure` | 500 | 真正的内部计算异常（与前三者严格分开） |

注意：**相反默认结论不可比不是错误**，那是正常结果 `conflict`。

---

## 5. 运行测试（真实命令与结论）

```bash
$ python3 -m pytest
95 passed, 1 warning in 0.57s
```

- `tests/unit/` —— 语言解析与校验、引擎具体结论、四类错误分类、存储、运行日志。
- `tests/integration/` —— 经 FastAPI TestClient 的端到端用例（含 422/409/509 与重放）。
- `tests/unit/oracle.py` —— **独立预言器**：用与被测内核完全不同的算法
  （Dung 抽象论证框架的接地扩展 / grounded extension）枚举论证并标注 in/out/undecided，
  与内核在 25 个随机小理论上逐查询交叉比对。参考答案不是由被测核心自己生成的。
- 规则重排不变性：对相互作用的规则做全部 4! 排列、再做 30 次随机洗牌，结论表必须逐字一致。

测试断言的是**具体结果与失败类别**（如 `flies(polly)` 为 `refuted` 且击败者是
`r_penguin_not`、优先环返回 `state_conflict`），不是“接口能调用”。

---

## 6. 可重放的运行日志

每次查询在 `logs/runs.jsonl` 追加三类记录（路径由 `DEFEASIBLE_LOG_PATH` 配置）：

1. `start` —— run_id、案例、查询；
2. `intermediate` —— 理论指纹、论域、证据、ground 规则数、不动点轮数、各状态计数
   （关键中间状态）；
3. `result` —— 最终状态、三类链数量、每条链的判断理由。

失败时追加 `error` 记录并带 `error_code`（四类之一）。用 `GET /runs/{run_id}` 重放。
日志仅追加；尾部损坏行不会让历史不可读。

---

## 7. 配置

复制 `.env.example` 或导出环境变量（均有本地默认值，零配置可跑）：

| 变量 | 默认 | 说明 |
|------|------|------|
| `DEFEASIBLE_DB_PATH` | `data/defeasible.db` | SQLite 文件，`:memory:` 用于测试 |
| `DEFEASIBLE_LOG_PATH` | `logs/runs.jsonl` | JSONL 运行日志 |
| `DEFEASIBLE_MAX_GROUND_RULES` | `100000` | grounding 预算 |
| `DEFEASIBLE_MAX_CHAINS` | `1000` | 论证链枚举预算 |
| `DEFEASIBLE_MAX_ROUNDS` | `1000` | 不动点轮数预算 |
| `DEFEASIBLE_HOST` / `DEFEASIBLE_PORT` | `127.0.0.1` / `8000` | 服务监听 |

---

## 8. 模块划分

| 模块 | 职责 |
|------|------|
| `defeasible/language.py` | 规则语言：项、规则、优先级、解析、范围受限与无环校验 |
| `defeasible/grounding.py` | 论域 grounding + 良基模型不动点 |
| `defeasible/engine.py` | 推理内核：元程序编译、状态判定、论证链枚举与解释 |
| `defeasible/storage.py` | SQLite 证据 / 案例 / 理论持久化 |
| `defeasible/logging.py` | 可重放 JSONL 运行日志 |
| `defeasible/service.py` | 编排存储 + 内核 + 日志 |
| `defeasible/api.py` | FastAPI 查询接口与错误码映射 |
| `defeasible/config.py` | 独立环境配置 |
| `defeasible/cli.py` | `serve` / `load-fixture` 入口 |

设计原则见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)，语义见
[`docs/SEMANTICS.md`](docs/SEMANTICS.md)。
