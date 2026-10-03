# njtree — 合成距离矩阵的 Neighbor Joining 建树后端

从合成距离矩阵或合成序列（FASTA）构建 Neighbor Joining 树，输出 Newick、
叶身份映射与拟合残差，并把每次运行的关键中间状态写入 SQLite 供回放。
所有数据均为本地合成夹具，无生产账号、无真实业务数据。

## 快速开始

```bash
pip install -r requirements.txt   # 固定版本依赖
python3 -m pytest -q              # 单元/集成测试（73 个）
python3 scripts/validate.py       # 端到端夹具验证 + 回放
python3 fixtures/generate.py      # 重新生成派生夹具（可选）
```

启动 API 服务：

```bash
uvicorn --app-dir src "njtree.api:create_app" --factory
# 持久化 provenance：create_app 接受 db_path 参数（默认 :memory:）
```

## 模块边界与数据契约

```
fixtures/            合成夹具（手工计算或独立生成器产生，见 fixtures/README.md）
src/njtree/
  errors.py          错误分类学：所有异常携带稳定 category
  models.py          跨模块数据契约（dataclass）：DistanceMatrix / BuildParams /
                     JoinStep / NegativeBranchEvent / ResidualReport / BuildResult
  matrix.py          矩阵验证边界：raw lists -> DistanceMatrix
  parsing.py         合成序列边界：FASTA 文本 -> Hamming 距离矩阵
  nj.py              领域算法核心：DistanceMatrix -> TreeNode + JoinStep + 事件
  tree.py            树结构、Newick 序列化、叶映射、路径距离
  residuals.py       拟合残差报告
  provenance.py      SQLite 溯源存储（runs / steps / results）
  service.py         编排：运行生命周期、日志、回放
  api.py             FastAPI 验证/构建接口
tests/
  independent.py     独立的 Newick 解析/路径/分裂实现（与核心零共享代码）
scripts/validate.py  端到端验证脚本
```

模块间只通过 `models.py` 中的 dataclass 传递数据；跨边界错误一律是
`errors.NJError` 的子类。无效输入在创建运行之前被拒绝（不产生 run 记录）；
计算期失败在运行之内被记录（run 状态为 `failed`，带错误类别）。

## 错误类别（可区分的失败）

| category | 含义 | HTTP | 例子 |
|---|---|---|---|
| `input_error` | 输入未通过声明的校验 | 422 | 非对称矩阵、非法字符、未知 run_id（404） |
| `state_conflict` | 运行状态冲突 | 409 | 同一 run_id 提交不同输入；覆盖已终结运行 |
| `resource_exhausted` | 超出声明的资源上限 | 413 | `n > max_taxa`（默认 500） |
| `computation_failure` | 算法无法按声明模式继续 | 500 | `mode=error` 下出现负枝长 |

API 错误体：`{"error": {"category", "message", "run_id", "details"}}`。

## 关键语义

### 矩阵校验（先检查，再计算）

按声明顺序执行，首个失败即抛出（验证接口 `POST /v1/matrices:validate`
返回全部可检违规）：

1. `labels` — 非空、唯一、字符集 `[A-Za-z0-9_.-]`
2. `min_taxa` — n ≥ 3
3. `max_taxa` — n ≤ max_taxa（资源类错误）
4. `shape` — 数值型、方阵、与标签数一致
5. `finite` — 无 NaN/Inf
6. `non_negative` — 全部 ≥ 0
7. `zero_diagonal` — 对角线严格为 0
8. `symmetric` — `max|D-Dᵀ| ≤ 1e-9`

**非加性矩阵不是输入错误**：允许通过校验，拟合残差在结果中报告。

### 平局稳定性

Q 准则取到**精确**最小值的所有对中，按节点 id 字典序选最小对（叶 id 为
输入顺序 0..n-1，内部节点 id 按创建顺序递增；活跃集始终按 id 排序）。
每步的平局数（`tie_count`）与所选对记入 `JoinStep`、写入 SQLite 并输出
日志（含 `tie_break=lexicographic_node_id`）。同一输入必得同一 Newick；
平局规则以输入标签顺序为准。

### 负枝长（按声明模式处理，绝不静默改零）

`BuildParams.negative_branch_mode`：

- `error` — 抛出 `ComputationError`（computation_failure），运行记录为 failed；
- `report`（默认）— Newick 中保留负值，事件记入 `negative_branch_events`；
- `clamp` — 置 0，但事件记录原始估计值；**残差按截断后的树计算**，
  截断引入的拟合误差保持可见（夹具证明：report 总残差 18.0，clamp 24.0）。

### 根与 Newick 约定

树无根；根是最后合并点的三度人工节点，三个孩子按节点 id 升序。
枝长格式 `%.10g`；负值原样输出。`leaf_map` 给出 叶标签 → 稳定叶节点 id。

### 残差定义

对每对叶 `(a,b)`：`residual = d_input − d_tree`（树路径长）。
报告 `total_absolute`（Σ|residual|）、`max_absolute`、`rmse` 及逐对明细。
加性输入的 `total_absolute ≈ 0`。

### 溯源与回放

每次运行记录：输入 JSON + sha256、参数、每个 join 决策（活跃集、所选对、
Q 值、平局数、枝长原始/应用值）、最终 Newick、叶映射、残差、负枝长事件、
状态与错误类别。`POST /v1/runs/{id}/replay` 用记录输入重算并与存储逐步比对。
run_id 幂等：同输入重提交返回已存结果（`idempotent: true`）；不同输入 → 409。

### 日志

每次 join 决策、负枝长事件、完成/失败摘要均带 `run=<run_id>`、关键中间
状态（Q 值、平局数、枝长）与判断理由，失败日志带错误类别，可按 run_id
重放问题。

## API

| 端点 | 说明 |
|---|---|
| `POST /v1/trees` | 建树；body 恰含 `matrix`（labels+values）或 `fasta` 之一，加 `params`（negative_branch_mode / run_id / max_taxa） |
| `POST /v1/matrices:validate` | 仅执行矩阵校验，返回 `{valid, violations}`，不建树 |
| `GET /v1/runs/{run_id}` | 溯源记录（run + steps + result） |
| `POST /v1/runs/{run_id}/replay` | 重放并比对，返回 `{match, differences}` |

## 测试与参考答案的独立性

- `fixtures/additive4.json`、`negative_branch.json`、`sequences.fasta` 的
  期望值全部手工计算（见各文件 description 与 fixtures/README.md）。
- `additive6/noisy6/duplicates` 由 `fixtures/generate.py` 对手工定义的树做
  BFS 路径求和产生，生成器与被测核心零共享代码。
- 测试用 `tests/independent.py`（独立的 Newick 解析器/路径/分裂实现）
  从输出的 Newick 字符串反算路径距离与拓扑分裂，与输入矩阵交叉核验
  总残差——不是核心实现自证。
- 测试断言具体数值、具体 Newick 字符串与具体失败类别，而非“接口能调通”。

## 已执行的检查

- `python3 -m pytest -q`：73 项全部通过（解析、矩阵校验、加性还原、平局、
  负枝长三模式、残差独立核验、溯源/回放/幂等/冲突、API 状态映射、日志）。
- 覆盖率（pytest-cov）：**98%**（559 语句，11 未覆盖，均为防御性分支）。
- `python3 scripts/validate.py`：全部夹具端到端通过，含回放一致性。

## 未执行的检查（如实列出，不计为通过）

- 大规模性能压测（n 接近 max_taxa=500 的耗时/内存）未测量；纯 Python/NumPy
  实现为 O(n³)，未做基准。
- SQLite 并发写入（多 worker 同时写同一库文件）未压测；当前实现用进程内
  锁保证单进程安全。
- 极端浮点输入（~1e±300 量级距离）未系统测试。
- 未做安全审计（本服务面向本地合成数据，无鉴权/限流实现）。
- 未测试 uvicorn 多进程部署形态（`create_app(":memory:")` 每进程独立库）。
