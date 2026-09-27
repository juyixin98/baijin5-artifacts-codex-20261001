# 闭合频繁项集后端（Closed Frequent Itemset Backend）

在事务集合上挖掘**闭合（closed）**与**极大（maximal）**频繁项集的后端服务。
技术栈：Python 3.10+ / FastAPI / SQLite，无外部服务依赖，所有夹具均为本地合成数据。

- 挖掘内核：垂直 tid 集合（整数位图）+ 闭包/Eclat 深度优先搜索
- 支持阈值：按事务数的**整数**阈值 `min_support`
- 枚举预算：每个作业按"候选评估次数"切块，预算到期返回部分结果，可断点续算
- 结果可解释：请求 id 关联、引擎版本、关键步骤日志、失败类别与不确定结论单列

## 1. 目录结构（真实模块拆分）

| 模块 | 职责 |
|---|---|
| `app/config.py` | 独立配置（环境变量：DB 路径、默认/最大预算、日志级别） |
| `app/corpus.py` | **语料规范**：校验、清洗、事务内去重、空事务保留 |
| `app/vertical_index.py` | **索引与模型**：垂直 tid 位图索引（交集 = `&`，支持度 = `bit_count`） |
| `app/miner.py` | **挖掘内核**：闭包 DFS、显式栈帧、预算切块、闭合→极大推导 |
| `app/repository.py` | SQLite 持久化（数据集、事务、可恢复作业的帧栈与结果） |
| `app/models.py` | Pydantic 请求/响应模型 |
| `app/service.py` | 应用服务：编排摄入、建索引、切块挖掘、持久化与序列化 |
| `app/api.py` | FastAPI 路由：请求关联、错误码、可解释负载 |
| `app/main.py` | 可运行服务入口 |
| `tests/` | 独立测试，含**从零实现的暴力枚举 oracle**（`tests/brute_force.py`） |
| `demo.py` | 本地端到端演示脚本（进程内跑完整 HTTP 栈） |

## 2. 语义定义

1. **事务内重复项只计一次**：`["a","a","b"]` 规范化为集合 `{a,b}`，`a` 的支持度只 +1。
2. **重复事务保留独立身份**：内容相同的两行各自拥有独立 tid，各自贡献支持度。
3. **支持度**：包含该项集的事务数（整数）。空项集的支持度 = 事务总数。
4. **闭合**：不存在具有**相同支持度**的严格超集（等价于项集等于其 Galois 闭包）。
5. **极大**：不存在频繁的严格超集。每个极大项集必闭合，但闭合不一定极大
   （例如 `{a}` 可闭合但有频繁超集 `{a,b}` 时就不是极大）。
6. 空事务是合法事务；它不包含任何单项，因此没有单项能达到"在全部事务中出现"，
   这会影响空项集是否闭合。

## 3. 预算、部分结果与续算

- 预算单位 = 一次候选项集的 tid 集合求交（一次搜索节点评估）。
- 预算到期：作业 `status="running"`、`complete=false`，返回**截至目前的闭合项集**。
  截断运行中找到的闭合项集仍然是**可靠**的（闭合性是局部精确性质）。
- 极大项集依赖全局性质，因此在 `complete=false` 时**刻意留空**，并置
  `maximal_results_certain=false`，`notes` 中以 `PARTIAL ...` 明确标注不确定性。
- `POST /jobs/{id}/resume` 从持久化的显式栈帧恢复，从断点继续；分块累积
  不会重复枚举也不会遗漏（测试 `test_budgeted_chunks_converge_without_duplicates` 断言）。
- 续算状态在 SQLite 中持久化，**重启进程后用新的 Repository 实例仍可继续**
  （`test_resume_state_survives_fresh_repository_instance`）。

## 4. 运行

```bash
pip install -r requirements.txt

# 启动服务（默认 127.0.0.1:8000，SQLite 在 ./fim.db）
python3 -m app.main
# 或
uvicorn app.main:app --host 127.0.0.1 --port 8000

# 环境变量配置
FIM_DB_PATH=/tmp/fim.db FIM_DEFAULT_BUDGET_NODES=10000 \
FIM_MAX_BUDGET_NODES=1000000 FIM_LOG_LEVEL=INFO python3 -m app.main

# 本地演示（无需起服务，进程内完整跑一遍）
python3 demo.py
```

## 5. HTTP API

### `GET /health`
返回服务版本、引擎版本、request id。

### `POST /datasets` → 201
```json
{
  "name": "demo",
  "transactions": [
    {"tid": "t1", "items": ["a", "b", "b"]},
    {"tid": "t2", "items": []}
  ]
}
```
返回 `dataset_id`、`content_hash`（规范化语料的 SHA-256 前 12 位）、统计：
事务数、去重后项数、空事务数、重复内容事务副本数。

### `POST /jobs` → 201
```json
{"dataset_id": "...", "min_support": 2, "budget": 100}
```
`min_support` 必须为 ≥1 的整数；`budget` 可省略（用默认值）、可为 0（只评估根节点）。
响应包含：

- `job_id`、`request_id`、`dataset_id`、`dataset_hash`、`engine_version`、`min_support`
- `status`（`running`/`complete`）、`complete`、`evaluations_used`
- `closed_itemsets`：`[{"itemset": [...], "support": N}, ...]`
- `maximal_itemsets`：完整时才有值，截断时为空
- `maximal_results_certain`：极大结果是否可信（与 `complete` 一致）
- `notes`：人类可读说明，**不确定结论（PARTIAL）在此单列**
- `chunk.evaluations_in_chunk`：本次请求实际消耗的评估数

### `POST /jobs/{job_id}/resume`
```json
{"budget": 100}
```
对已完成的作业续算是空操作（`evaluations_in_chunk=0`）。

### `GET /jobs/{job_id}`
读取持久化的作业状态。

## 6. 错误语义（失败类别单列）

所有错误负载形如：
```json
{
  "error_code": "INVALID_CORPUS",
  "message": "duplicate tids inside one batch are not allowed: ['dup']",
  "request_id": "...",
  "engine_version": "eclat-closure-dfs/1.0.0",
  "details": null
}
```

| HTTP | error_code | 触发条件 |
|---|---|---|
| 422 | `INVALID_CORPUS` | 语料规范错误：空批次、tid 缺失/重复、items 非数组、项非字符串或空白、项超长 |
| 422 | `VALIDATION_ERROR` | 请求不符合 Pydantic 模式（如 `min_support=0`、字段类型错误），`details` 含字段级错误 |
| 404 | `DATASET_NOT_FOUND` | 创建作业引用了不存在的数据集 / 查看不存在的数据集 |
| 404 | `JOB_NOT_FOUND` | 查看或续算不存在的作业 |

日志与响应均带 `X-Request-ID`（可用请求头 `X-Request-ID` 自行指定）、
作业 id 和引擎版本，服务端日志示例：

```
INFO [req=demo-job-create job=8c26..] fim@eclat-closure-dfs/1.0.0: created job ... evals=3 closed=3 complete=False
WARNING [req=... job=-] fim@eclat-closure-dfs/1.0.0: error INVALID_CORPUS: ...
```

边界语义：
- `min_support > 事务总数`：立即 `complete`，结果为空，`notes` 说明无频繁项集。
- `min_support = 事务总数`：只有出现在每个事务中的项（及满足条件的空项集）。
- 请求预算超过 `FIM_MAX_BUDGET_NODES` 时被钳制，并在 `notes` 中说明。

## 7. 测试与复现

测试不只检查"接口能调用"，而是断言**具体结果与失败类别**；参考答案不允许由
被测内核自己生成——`tests/brute_force.py` 是独立的朴素全子集枚举实现
（只用 `set`/`itertools`，与 `app.miner`、`app.vertical_index` 零共享代码）。

```bash
python3 -m pytest                                  # 运行全部测试
python3 -m pytest --cov=app --cov-report=term-missing   # 覆盖率（当前 ≥ 95%）
```

关键测试：

- `test_handcomputed_min_support_2_closed_and_maximal_differ`：手算值断言
  闭合 7 项 vs 极大 3 项（ab/ac/bc）。
- `test_same_support_containing_set_is_not_closed`：同支持包含集不闭合。
- `test_empty_transactions_set_empty_itemset_support`：空事务 + 阈值边界。
- `test_threshold_boundaries`：`min_support=1`、`=n`、`>n` 三个边界。
- `test_exhaustive_matches_independent_oracle`：3 项域下**所有**长度 1–3 的
  语料（含空事务、重复事务）× 每个阈值，与独立 oracle 逐一比对闭合与极大结果。
- `test_random_corpora_match_oracle`：随机语料（含事务内重复项）对拍。
- `test_budgeted_chunks_converge_without_duplicates`：每次只给 1 次预算，
  断言跨块无重复发射，最终结果与 oracle 一致。
- `test_partial_closed_results_are_a_sound_subset_of_final`：部分结果是
  最终结果的可靠子集（支持度一致）。
- `test_resume_state_survives_fresh_repository_instance`：进程重启后续算。
- `test_*_returns_named_error_category` / API 404/422 用例：断言具体错误码。

## 8. 算法说明（为什么闭合结果在截断时仍可靠）

DFS 每个节点是 `(前缀项集 X, tidset T(X))`，子节点用位图交集扩展。
访问到频繁节点 X 时，用**完整频繁项目录**精确计算闭包
`cl(X) = { i | T(X) ⊆ t(i) }`：`X == cl(X)` 当且仅当 X 闭合。该判定不依赖
"搜索是否完成"，所以预算截断时已输出的闭合项集绝无误报；唯一暂缓的是
极大性（需要确认不存在频繁超集，属全局性质），完成后由闭合集之间的
超集关系一次性推导。
