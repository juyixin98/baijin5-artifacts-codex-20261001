# 在线多重检验预算服务（LORD++ 教学实现）

一个**可审查**的在线 FDR（False Discovery Rate）控制服务：针对按固定顺序到达的假设序列，
实现**唯一一种**规则 —— LORD++（Ramdas, Zrnić & Wainwright, 2018）。
每个假设的拒绝阈值 α_t **只依赖 t 之前的结果**；后验修改 p 值无法重写已经花掉的预算。

技术栈：Python 3.12 · FastAPI · NumPy · SciPy · SQLite（标准库 `sqlite3`）。
全部数据与参与者均为**本地合成夹具**，无需任何生产账号或外部服务。

---

## 1. 统计契约（冻结）

合约版本：`lordpp-v1`（见 `app/statistics.py`，常量不可在运行期修改）。

参数（创建 run 时冻结）：

| 量 | 符号 | 冻结值 |
|---|---|---|
| 目标在线 FDR | α | `0.05` |
| 初始财富 | w₀ | `0.045`（= 0.9·α，要求 0 < w₀ ≤ α） |
| 每次拒绝的固定奖励 | b = α − w₀ | `0.005` |
| 时域（最大假设数） | H | `1000` |
| 奖励序列常数 | C | `0.0722` |

奖励序列（在 1..H 上归一化，∑γ(j)=1）：

```
r(j)   = C · log(max(j,2)) / max(j,2)
S_H    = Σ_{i=1..H} r(i)            # math.fsum 精确累加
γ(j)   = r(j) / S_H
```

**决策（因果）公式**，t 从 1 开始，τ_k 为过去**被拒绝**的时刻，α_{τ_k} 为该拒绝**花费的、在看到其 p 值之前就已冻结的阈值**：

```
W_t     = w0 + Σ_{τ_k < t} γ(t − τ_k) · (b − α_{τ_k})     # 只看过去
α_t     = γ(t) · W_t                                        # 在看到 p_t 之前提交
拒绝 H_t 当且仅当  p_t ≤ α_t
```

每次拒绝的净回补是 `(b − α_{τ_k})`：赚取固定奖励 b，并收回它花掉的已冻结水平 α_{τ_k}。
注意回补项用的是**该步花费的阈值 α 而非 p 值**——这是 FDR 保证所依据的规范 LORD++ 递推，
且 α_{τ_k} 本身先于 p 值确定，使整个财富过程只依赖事前冻结量。

### 关键性质（被测试强制）

1. **阈值因果性**：服务采用两阶段协议 —— 先 `reserve`（把 α_t 落库），后 `decide`（提交 p_t）。
   α_t 不可能被当前或未来的 p 值影响；重复提交同一假设的 p 值返回 `STATE_CONFLICT`，历史与预算不变。
2. **参数/财富/奖励冻结**：`LordConfig` 为 frozen dataclass；序列常数、初始财富、奖励公式均为模块常量。
3. **无效 p 值拒绝**：非数值、NaN、inf、∉ (0,1] 一律 `INPUT_ERROR`，服务**绝不**悄悄夹取/修补。
4. **不是离线 BH**：本服务不提供、也不允许用 Benjamini–Hochberg 等离线批次程序冒充在线决策。
   `/contract` 明确列出适用条件与不保证事项。

### 算法假设与适用条件

- 假设按先验固定的顺序到达；决策 H_t 时 p_{t+1} 尚未观测（顺序不可事后按 p 值挑选）。
- 每个 p 值在其原假设下有效：真零假设的 p 值随机地 ≥ Uniform(0,1)。
- p 值相互独立，或满足 LORD++ 成立的标准局部相关（PRDS 类）条件。
- 序列长度不超过声明的冻结时域 H。
- **FDR 是对重复实验的期望** E[V/max(R,1)]；**任何单次 run 都不保证** FDP ≤ α。
  本仓库只用蒙特卡洛聚合展示该期望，从不宣称单次保证（见全局零假设实验中 run 33/40 的 FDP=1）。
- 不主张对任意相关、自适应排序、事后改 p 值、或超出时域的情形有效。

---

## 2. 模块关系与数据/错误契约

```
app/
  errors.py       错误分类（稳定 code）与 HTTP 状态映射
  statistics.py   统计契约 + LORD++ 估计内核（纯函数式状态，从拒绝历史重建财富）
  storage.py      仅追加 SQLite：两阶段提交、严格顺序、假设身份唯一、SHA-256 哈希链、事件日志
  schemas.py      Pydantic 外部请求/响应模型（边界形状校验）
  api.py          FastAPI 工厂、路由、统一错误信封
  simulation.py   独立参考实现 + 固定种子合成流 + 蒙特卡洛聚合（不导入内核做答案）
  diagnostics.py  证据/诊断：发现数核算 V/S、FDP 聚合、决策理由、事件落盘
scripts/
  run_experiments.py  复现全部蒙特卡洛证据，输出 artifacts/tests/ 下 JSON/TSV
tests/                  断言具体数值与失败类别（非“接口可调用”）
```

数据流：`reserve(α_t 仅由已决历史算出并落库) → decide(p_t 比对已冻结 α_t) → 哈希链接力`。
写事务使用 `BEGIN IMMEDIATE` 立即取写锁并配合 `busy_timeout`，跨连接/跨进程下同一槽位的并发
`decide` 恰有一个成功，另一个得到 `STATE_CONFLICT`（乐观守卫 `AND status='pending'` + rowcount 校验）。
重放 `GET /runs/{id}/replay` 从已决历史独立重算每个阈值并核对**覆盖整行字段**（含 wealth_after、
时间戳、γ、run 完成状态）的 SHA-256 哈希链；任何对 p 值/阈值/拒绝标记/财富/时间戳/行/状态的篡改
或删除都产生 `INTEGRITY_ERROR`（审计中损坏的输入也归类为完整性错误，而非普通输入错误）。

错误信封（四类必需错误可区分，另加两类）：

```json
{ "success": false, "error": { "code": "...", "message": "...", "details": {} } }
```

| code | HTTP | 触发示例 |
|---|---|---|
| `INPUT_ERROR` | 400 | p 值 ∉ (0,1]、参数非法、请求形状错误 |
| `STATE_CONFLICT` | 409 | 重复假设身份、未 reserve 就 decide、改写已决假设、上一个仍 pending |
| `RESOURCE_EXHAUSTED` | 413 | 超过冻结时域 H |
| `COMPUTATION_FAILED` | 422 | 存储阈值与重算不一致、非有限/负财富或阈值 |
| `NOT_FOUND` | 404 | 未知 run / step |
| `INTEGRITY_ERROR` | 500 | 重放发现历史被篡改或哈希链断裂 |

事件日志（`events` 表 / `/events`）逐条记录 `run_id, seq, kind, code, message, payload, created_at`，
含每次 reserve/decide/reject 与分类错误，保留运行编号、阈值、财富等关键中间状态与判断理由，可重放问题。

---

## 3. 本地验证命令

```bash
# 0) 环境（已在 Python 3.12.3 验证；依赖见下）
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 1) 全部单元/集成/统计测试（含覆盖率，阈值 80%）
python3 -m pytest --cov=app --cov-report=term-missing

# 2) 复现蒙特卡洛证据（确定性；固定种子 20260927）
python3 scripts/run_experiments.py --reps 300 --steps 200
# 产物：
#   artifacts/tests/experiment-report.json   每轮 run 编号/种子/计数/FDP + 聚合 + 判定理由
#   artifacts/tests/replication-log.tsv      可 grep 的逐轮日志

# 3) 启动服务手测（可选）
FDR_DB_PATH=/tmp/fdr.db uvicorn app.api:app --reload
# 另开终端：
curl -s localhost:8000/contract | python3 -m json.tool
curl -s -X POST localhost:8000/runs -H 'content-type: application/json' \
     -d '{"run_id":"demo"}'
curl -s -X POST localhost:8000/runs/demo/reserve -H 'content-type: application/json' \
     -d '{"hypothesis_id":"h1"}'
curl -s -X POST localhost:8000/runs/demo/decide -H 'content-type: application/json' \
     -d '{"hypothesis_id":"h1","p_value":0.0005}'
curl -s localhost:8000/runs/demo/replay | python3 -m json.tool
```

### 预期判断方式

- **测试**：全部通过（62 个）；`app/` 总覆盖率 ≥ 80%（当前 96%，逐文件均 ≥ 95%）。
- **手算短序列**（α=0.05, w₀=0.045, H=1000；p=[.0005,.5,.0005,.9,.0004]）：
  阈值轨迹与拒绝时刻必须为 **t = 1, 3, 5**；
  α₁≈`0.000646170509`，α₃≈`0.000683719252`，α₅≈`0.000601804945`；S_H=`1.742601339689304`。
  这些字面值由**第三份独立 stdlib oracle** 算出并硬编码断言，不由被测内核生成。
- **全局零假设**（100 或 300 复现 × 200 步，纯 Uniform）：经验 FDR 应 ≤ α；
  在冻结的 100 复现设计中恰有 **run 33（seed 20293215）与 run 40（seed 20300278）** 各发生 1 次错误拒绝，
  FDR 估计 `0.02`（单次 run FDP 可为 1 —— 这正是必须聚合的原因）。
- **混合流**（30% 备择、effect 3.5）：FDR 远低于 α 且功效明显为正；
  100 复现冻结结果 FDR≈`0.000345`、边际功效≈`0.36`、首轮（seed 20260927）拒绝 17 个且全部为真。
- 脚本对每个实验打印 `CONSISTENT` / `FLAG_FOR_INSPECTION` 及理由（按 α+教学裕度 0.05 的 95% 上界判定）。
- **复现性**：相同 `--reps/--steps` 两次运行，除墙钟字段 `generated_at` 外，JSON/TSV 内容逐字节一致
  （每个重复的 `run_no` 与 `seed` 固定，base seed = 20260927，stride = 1009）。

---

## 4. 参考答案为何不是“自己考自己”

- 生产内核：`app/statistics.py`（NumPy 数组 + 类）。
- 独立参考：`app/simulation.py`（仅用 `math.fsum` 的独立循环，经验 FDR 全部来自这条路径）。
- 第三份 oracle：`tests/test_reference_oracle.py` 内联的纯标准库实现，既不导入内核也不导入 simulation；
  三份实现 + 硬编码手算字面值互相校验（30 条混合流、多条零假设流逐条决策一致）。

---

## 5. 依赖版本（本地冻结）

Python **3.12.3**；numpy 2.4.6；scipy 1.15.3；fastapi 0.141.1；pydantic 2.13.5；
starlette 1.7.0；uvicorn 0.54.0；httpx 0.28.1；pytest 9.1.1；pytest-cov 7.1.0。
SQLite 使用 Python 标准库 `sqlite3`（WAL 模式），无额外数据库。

## 6. 测试状态

- 所有自动化测试：**已运行并通过**（命令见第 3 节，可一键复现）。
- 无被标记 `skip`/`xfail` 的用例；无“未运行”测试。若在缺少上述依赖的环境中执行，
  请先 `pip install -r requirements.txt`，否则收集阶段即失败（属于环境缺失，非测试失败）。
