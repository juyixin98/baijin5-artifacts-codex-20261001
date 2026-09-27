# 受限默认规则的可解释推理后端

一个从零实现的、带明确受限语义的可废止推理（defeasible reasoning）服务：
**严格规则**与**可撤销默认**分离、**无环优先关系**、强否定冲突在不可比较时
**保留冲突**（不选先出现者）、**失败即否定（NAF）**且**缺证据不是反面事实**，
并为每个结论给出**支持 / 击败 / 悬而未决**三类可重放的论证链。

技术栈：Python 3.12 · FastAPI · SQLite（标准库 `sqlite3`）· 无外部业务依赖，
全部数据为合成符号事实（鸟、企鹅、尼克松菱形等）。

---

## 1. 它满足的四条硬性规则

| # | 规则 | 实现位置 / 保证 |
|---|------|----------------|
| 1 | 严格规则与可撤销规则分开，优先关系无环 | `ast_nodes.RuleKind` 两栏存储；`validator._find_cycle` 解析期拒环；`kernel/priority.py` 取传递闭包前再次拒环（脏数据也拦） |
| 2 | 相反结论无可比优先级时**保留冲突**，不选先出现者 | `engine._add_rebuttal_pair`：不可比较 ⇒ **双向攻击**；grounded 语义下双方 `undecided / PENDING_MUTUAL_CONFLICT` |
| 3 | 缺少证据 ≠ 反面事实 | 无论证的文字为 `no_evidence / NO_EVIDENCE_AT_ALL`；NAF 是假设，被证成才失效；无种子正循环不产生任何论证 |
| 4 | 结果列出支持、击败、悬而未决链 | `kernel/explanation.py` 输出三桶 + 每棵证明树 + `counter_chains`（攻击类型与优先依据） |

---

## 2. 快速开始

```bash
# 依赖（环境中已具备；需要时）
pip install -r requirements.txt

# 方式 A：命令行演示，无需起服务
python3 scripts/demo.py

# 方式 B：启动 HTTP 服务
./scripts/run_dev.sh            # 默认 http://127.0.0.1:8000，交互文档 /docs
```

### 三条 HTTP 调用走完核心流程

```bash
# 建库
curl -s -X POST localhost:8000/api/kbs \
  -H 'Content-Type: application/json' \
  -d '{"kb_id":"birds","name":"鸟样例"}'

# 装载理论（严格规则 :- / 可撤销 := / priority）
curl -s -X PUT localhost:8000/api/kbs/birds/theory \
  -H 'Content-Type: application/json' \
  -d "{\"theory\": $(python3 -c 'import json;print(json.dumps(open("samples/birds.rdrl").read()))')}"

# 查询
curl -s -X POST localhost:8000/api/kbs/birds/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"Flies(polly)"}'
```

`Flies(polly)` 的真实结论（节选自实际响应）：

```json
{
  "goal": "Flies(polly)",
  "status": "rejected",
  "reason_code": "REJECTED_OPPOSITE_GROUNDED",
  "defeated_chains": [{
    "rule_id": "r1",
    "counter_chains": [{
      "rule": "r2", "attack_kind": "REBUTTAL",
      "detail": "-Flies(polly) 反驳 Flies(polly) (显式优先关系 r2 > r1)",
      "tree": { "conclusion": "-Flies(polly)", "rule": "r2",
                "premises": [{"conclusion": "Penguin(polly)", "rule_kind": "fact"}] }
    }]
  }]
}
```

尼克松菱形（无优先级）则返回 `status: "undecided"`、
`reason_code: "PENDING_MUTUAL_CONFLICT"`，并在 `pending_chains` 里给出对攻双方。

---

## 3. 规则语言（RDRL）一览

```
Bird(tweety).                      % 接地事实
@r1 Flies(X) := Bird(X).           % 可撤销默认（箭头 :=）
@r2 -Flies(X) := Penguin(X).       % 强否定结论（- 前缀）
@s  Wingless(X) :- Penguin(X).     % 严格规则（箭头 :-），体中禁止 not
@w  Wings(X) := Bird(X), not Wingless(X).   % 失败即否定 not
priority(r2, r1).                  % r2 强于 r1（必须无环）
```

- 变量：大写或 `_` 开头；常量符号：小写开头；另支持 `"字符串"`、整数。
- 规则安全性：头变量与 `not` 中的变量都必须被规则体正文字约束。
- 注释：`%` 或 `#` 到行尾。

样例见 `samples/`：`birds.rdrl`、`nixon_diamond.rdrl`、
`mutual_exclusion.rdrl`、`naf_open_world.rdrl`；两个故意非法的样例
（`*.rdrl.bad`）分别触发优先环与严格矛盾。

---

## 4. 模块划分（真实模块，非单文件脚本）

```
app/
  rulelang/          规则语言：ast_nodes / lexer / parser / validator / codecs / dsl
  kernel/            推理内核：grounder 接地 · arguments 论证(证明树)
                     · priority 无环偏序 · consistency 严格一致性
                     · engine 攻击关系 + grounded 不动点
                     · explanation 三桶链 · facade 编译/查询/序列化
  storage/           database(SQLite schema) · repository(CRUD) · runlog(JSONL)
  api/schemas.py     请求/响应模型
  services.py        业务编排（解析→校验→接地→一致性→求值→落库/日志）
  main.py            FastAPI 应用工厂 + 四类错误的 HTTP 映射
  config.py / errors.py
config/default.yaml  独立配置（端口、库路径、资源上限、日志路径）
```

内核 `app/kernel` **不依赖** FastAPI 与 SQLite，可独立单测。形式化语义见
[`docs/SEMANTICS.md`](docs/SEMANTICS.md)。

---

## 5. 运行测试（真实命令与结论）

```bash
python3 -m pytest tests                      # 全量 + 覆盖率
python3 -m pytest tests/unit  -o addopts=""  # 仅单元
python3 -m pytest tests/integration -o addopts=""   # 仅 HTTP/SQLite 集成
```

最近一次干净运行的真实结果：

```
103 passed in 5.6s        # 全量，总覆盖率 94%
83 passed                 # 单元
20 passed                 # 集成
```

测试不是"接口能调通"式断言：

- **具体结果**：`tests/fixtures/expected_results.yaml` 由人工逐条编写
  （状态、理由码、三桶链数量、击败规则、攻击类型、优先依据），
  `tests/unit/test_kernel_semantics.py` 严格按它断言。
- **参考答案不来自被测核心**：`tests/fixtures/oracle.py` 是**第二份独立实现**，
  不 import 任何 `app.kernel` 模块（自写接地器、论证枚举、grounded 不动点）。
  `test_kernel_vs_oracle.py` 对 13 个枚举小理论逐原子交叉比对两边状态。
- **规则重排不变**：`test_reorder_invariance.py` 对理论语句做全排列，
  断言结论、接地实例数、论证数恒定。
- **失败类别可区分**：输入错误 / 状态冲突 / 资源耗尽 / 计算失败分别映射
  422 / 409 / 429 / 500，并有专门测试。
- **优先环、互斥默认、缺证据、NAF 三环、恢复链（reinstatement）**均有用例。

---

## 6. 问题回放与日志

每次请求生成 `run-xxxxxxxx` 编号，写入 SQLite `runs` 表与 JSONL：

- `logs/runs.jsonl`        请求开始/结束（含原始请求、最终判定）
- `logs/kernel.trace.jsonl` 关键中间状态：装载规模、接地/论证数、不动点迭代数、
  每个目标的状态与理由码
- `logs/errors.log`        四类错误的分类记录

```bash
curl -s localhost:8000/api/runs/<run_id>     # 取回原始请求+结果/错误
curl -s "localhost:8000/api/runs?limit=50"
```

用 `run_id` 即可拿到原始请求并复放；trace 日志保留了关键中间状态与判断理由。

---

## 7. 四类错误如何区分

| 类别 | 异常 | HTTP | 触发示例 |
|------|------|------|----------|
| 输入错误 | `RuleLanguageError` | 422 | 语法错、非安全规则、非接地事实、严格规则用 not、优先环、引用未定义规则 |
| 状态冲突 | `KnowledgeStateError` | 409 | 严格理论矛盾、知识库不存在/重复 |
| 资源耗尽 | `ResourceLimitError` | 429 | 接地实例 / 论证数 / 每目标链数 / 不动点迭代超 `config` 上限 |
| 计算失败 | `ReasoningFailureError` | 500 | 内核不变量被破坏（理论上不应发生） |

---

## 8. 配置

`config/default.yaml`（可用环境变量 `REASONER__DB_PATH`、`REASONER__LOG_DIR`、
`REASONER__HOST/PORT`、`REASONER__MAX_GROUND_INSTANCES` 覆盖）。
测试通过 `tests/conftest.py` 重定向到临时库与临时日志目录，不污染开发数据。
