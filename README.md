# Entity Resolution Backend (synthetic org names)

实体解析后端：对合成机构名称记录做候选匹配 + 全局约束聚类。Python 3.12 / FastAPI / SQLite，全部数据为本地合成夹具，无外部账号与真实业务数据。

## 核心语义

- **成对相似不自动传递**：聚类不是“阈值 + 连通分量”。候选对按分数降序贪心合并，每次合并前检查 cannot-link 与人工锁；A~B、B~C 相似但 A~C 禁止时，A 与 C 一定落在不同簇（见 `tests/test_clustering.py::test_chain_similarity_does_not_transitively_merge_cannot_link`）。
- **must-link / cannot-link 先校验冲突**：约束在提交时与解析前做传递性校验（must-link 链与 cannot-link 矛盾 → `state_conflict` 409）。
- **名称相同 ≠ 同实体**：完全相同名 + 不同注册号 + cannot-link 的记录保持分离；相同注册号才是强证据。
- **聚类带来源证据**：每个簇输出构建它的成对决策（分数 + 理由）与人工锁；每次解析返回**受影响实体**（与上一运行相比簇成员变化的记录）。
- **人工确认可锁定**：`POST /locks` 锁定的映射在后续解析中保持，且与 cannot-link 矛盾时报状态冲突而不是静默覆盖。

## 模块边界与契约

| 模块 | 职责 | 关键契约 |
|---|---|---|
| `er_backend/corpus/` | 语料规范与规范化 | `Record`（pydantic 校验）；`Normalizer` 输出 `NormalizedName(original, normalized, tokens)`，非法名称抛 `InputValidationError` |
| `er_backend/kernel/` | 挖掘内核 | `pair_score` 纯函数输出 `ScoreBreakdown(score, features, reason)`；`validate_constraints` 先于聚类；`cluster_records` 输出 `assignment + decisions`（每条决策带 reason） |
| `er_backend/index/` | 索引与模型持久化 | `generate_candidates` 受 `max_candidates` 预算约束，超限抛 `ResourceExhaustedError`；`Store` 持久化记录/约束/锁/运行日志（含输入快照，可重放） |
| `er_backend/api/` | 查询验证层 | 错误类别 → HTTP：`input_error`→400、`state_conflict`→409、`resource_exhausted`→413、`computation_failure`→500 |

错误四类可区分：输入错误 / 状态冲突 / 资源耗尽 / 计算失败（`er_backend/errors.py`）。

## 运行日志与重放

每次解析写入运行日志：`run_id`（时间戳 + 输入摘要哈希）、配置、完整输入快照、逐条成对决策（分数与理由）、最终簇分配。`POST /runs/{run_id}/replay` 从快照重算并校验与日志逐条一致，不一致抛 `ComputationError`。

## 快速开始

```bash
pip install -r requirements.txt   # 依赖已锁定
python3 -m pytest tests/ -q       # 40 个独立测试
python3 examples/demo.py          # 端到端演示（服务层）
```

启动 HTTP 服务：

```bash
uvicorn "er_backend.api.main:create_app" --factory --port 8931
# 或带跨语言 token 映射与持久库：
python3 -c "from er_backend.api.main import create_app; import uvicorn; \
  uvicorn.run(create_app(db_path='er.sqlite3', token_map_path='examples/token_map.json'), port=8931)"
```

### 示例调用

```bash
# 1. 摄取记录
curl -X POST localhost:8931/records -H 'content-type: application/json' -d '{
  "records": [
    {"record_id":"A","name":"Acme Trading"},
    {"record_id":"B","name":"Acme Trading Ltd"},
    {"record_id":"C","name":"Acme Trading Company"},
    {"record_id":"F","name":"北京星辰科技有限公司"},
    {"record_id":"G","name":"Beijing Xingchen Technology Ltd"}
  ]}'

# 2. 提交约束（冲突时返回 409）
curl -X POST localhost:8931/constraints -H 'content-type: application/json' \
  -d '{"must_link":[["A","B"]],"cannot_link":[["A","C"]]}'

# 3. 解析 → 簇 + 证据 + 受影响实体
curl -X POST localhost:8931/resolve

# 4. 人工确认锁定映射
curl -X POST localhost:8931/locks -H 'content-type: application/json' \
  -d '{"record_ids":["F","G"],"note":"confirmed same entity"}'

# 5. 运行日志与重放
curl localhost:8931/runs/<run_id>
curl -X POST localhost:8931/runs/<run_id>/replay
```

实测结果（2026-09-27 验证）：解析产出 `{A,B} {C} {F,G}` 三簇（A~C 被 cannot-link 阻断、F/G 跨语言归并），replay 返回 `{"replayed": true}`，冲突约束 409、非法输入 400、候选预算耗尽 413。

## 配置

`er_backend/config.py` 的 `Settings`，环境变量前缀 `ER_`：`ER_MERGE_THRESHOLD`（默认 0.75）、`ER_MAX_RECORDS`（5000）、`ER_MAX_CANDIDATES`（20000）、`ER_BLOCKING_DF_CAP`（0.5）、`ER_DB_PATH`。

## 测试与诊断

- `tests/reference.py`：**独立**穷举分区预言机（自有分词/相似度/目标函数，不 import 内核），`tests/test_reference_oracle.py` 断言内核在小规模夹具上与预言机逐分区一致，且预言机结果锚定手写期望。
- 夹具覆盖：相似链 A-B-C + A-C 禁止、别名匹配合并、跨语言（CJK↔拉丁）规范化、同名不同实体、共享注册号强证据、人工锁强制合并/阻止合并。
- 失败类别测试：输入错误（重复 id、未知 id、空名称）、状态冲突（传递性 must/cannot 矛盾、锁与 cannot-link 矛盾）、资源耗尽（候选预算）、计算失败（replay 校验）。
- 运行日志含 run_id、输入快照、中间决策与理由，可完整重放。

## 已知限制

- 贪心约束聚类不保证全局最优（相关性聚类是 NP-hard）；小规模正确性由穷举预言机保证，大规模下是近似。
- 跨语言/别名归一化是夹具驱动的 token 映射，无真实翻译/音译模型。
- 阻塞基于共享 token，完全无公共 token 且非同名的记录对不会成为候选（共享注册号等属性需至少一个公共 token 才会被评分）。
- SQLite 单文件存储，单进程写入；无并发写入控制与分布式扩展。
- 评分权重（jaccard 0.6 / 编辑距离 0.4）为固定合成默认值，未做标注数据调参。
