# DTW Alignment Service (opp505-a)

两个标量特征序列的动态时间规整（DTW）：返回对齐路径、（归一化）代价与局部伸缩率。
技术栈：Python 3.12 / FastAPI / NumPy / SciPy。所有验证均使用本地合成数据，无外部账号或真实业务数据。

## 算法假设（行为契约）

| 契约项 | 固定取值 | 说明 |
|---|---|---|
| 局部距离度量 | 绝对差 `\|a_i - b_j\|` | 契约固定，不可配置（API 传 `metric` 字段会被 422 拒绝） |
| 步型 | `{(1,1), (2,1), (1,2)}` | 不含纯横/纵步，从构造上禁止无限横纵走；局部斜率限制在 `[1/2, 2]` |
| 路径窗口 | Sakoe-Chiba `\|i-j\| <= r` | `r` 可由请求覆盖，缺省读 `config/default.json` |
| 累计代价 | 路径格点局部距离之和 | `D(i,j) = d(i,j) + min D(前驱)`，`D(0,0)=d(0,0)` |
| 归一化分母 | `n + m`（消耗样本数） | 对同一对序列的所有合法路径相同，归一代价可跨请求比较 |
| 回溯平局 | 按步型声明顺序取首个最优 | 确定性，偏向对角步 |

**可达性**：步型的可达锥为 `i/2 <= j <= 2i`（每步 `i+j` 增 2 或 3，`|i-j|` 至多增 1）。
服务端先用 `|n-m| <= r` 做快速失败（`WINDOW_TOO_NARROW`），DP 后端点代价为无穷则报
`UNREACHABLE_ENDPOINT`——不可达终点永远显式失败，不会静默返回空路径。

**局部伸缩率**：每个路径转移的 `di/dj ∈ {0.5, 1, 2}`（A 每消耗 di 个样本对应 B 的 dj 个），
另提供边缘填充滑动平均的平滑序列。

**判定状态**：`accepted` / `rejected`（带失败类别）/ `indeterminate`（带内局部距离全为 0，
所有合法路径同等最优，对齐不可辨识——仍返回一条路径并明确标注）。

## 模块关系

```
contracts   样本契约：请求/响应模型、DecisionStatus、FailureCategory
constraints 固定步型 + Sakoe-Chiba 窗口（契约 1 的强制点）
distance    固定局部度量、带内最大距离（退化检测用）
dense       稠密 DP 核心（小规模 + 测试交叉验证基准）
banded      带状存储 DP，O(n·r) 内存，服务运行时使用（契约 4）
backtrack   共享回溯（dense/banded 同一访问器接口）
stretch     路径 → 局部伸缩率 + 平滑
service     校验 → 对齐 → 判定记录的编排（契约 2、3 的落实点）
stream      流式会话状态：分块缓冲、尾部对齐、进度快照
diagnostics 判定记录：request_id + 关键状态；序列只出长度+哈希（脱敏）
api         FastAPI 表面；领域失败返回 200+结构化响应，模式错误返回 422
config/default.json  运行配置（窗口半径、平滑窗、长度上限），独立于代码与测试
tests/reference.py   独立穷举参考实现（与被测核心零共享代码）
```

## 本地验证

```bash
pip install -r requirements.txt   # 版本见下，已在 Python 3.12 实测

# 1) 全部数值/契约测试（246 个）
python3 -m pytest

# 2) 只看穷举参考对照（短序列全路径枚举 vs 两个 DP 核心）
python3 -m pytest tests/test_exhaustive.py -v

# 3) 手算参考用例（期望值来自纸面推导，非被测实现生成）
python3 -m pytest tests/test_dense_handcomputed.py tests/test_stretch.py -v

# 4) 启动服务并冒烟
python3 -m uvicorn dtw_service.api:app --port 8000
curl -s -X POST http://127.0.0.1:8000/v1/dtw -H 'Content-Type: application/json' \
  -d '{"sequence_a":[0.0,1.0],"sequence_b":[0.0,2.0],"window_radius":1}'
# 预期：status=accepted, path=[[0,0],[1,1]], cost=1.0, normalized_cost=0.25, stretch=[1.0]
curl -s -X POST http://127.0.0.1:8000/v1/dtw -H 'Content-Type: application/json' \
  -d '{"sequence_a":[0,0,0,0,0,0,0,0,0,0],"sequence_b":[0,0,0,0],"window_radius":2}'
# 预期：status=rejected, failure.category=window_too_narrow
```

**预期判断方式**：`pytest` 全绿（246 passed）；冒烟请求返回上述具体数值；
失败用例的 `failure.category` 与列出的类别一致。诊断字段中只出现
`len=N sha256=...` 指纹，不出现原始序列值。

## 测试覆盖的行为

- 短序列（n,m ≤ 5，r ≤ 3，共 100 组参数 × 2 个核心）对照独立穷举参考：代价等于全路径最小值，
  路径单调、步型合法、不越窗、重算代价一致
- 手算参考：2x2 对角、压缩步 (2,1)、缺段桥接（最优 0.6，含下界论证）、2 倍加速/减速的格点级断言
- 速度变化：2x 减速路径每格落在 `{2i, 2i+1}`；2x 加速路径唯一 `(2k, k)`，伸缩率全为 2.0
- 局部缺段：跨缝后重新锁定 `j = i - cut_len`，缝区由 (2,1) 步跨越
- 空序列 / 非有限值 / 超长 / 负窗口 / 过窄窗口 / 不可达终点：各自断言具体失败类别
- 带状 vs 稠密：随机序列代价与路径完全一致；带状内存形状 `(n, 2r+1)`
- 流式会话：分块推送、尾部对齐、状态计数、非法块拒绝
- API：具体数值断言、失败类别、422 模式校验、脱敏检查

## 依赖版本（2026-10-03 在 Python 3.12.3 上实测通过）

numpy 2.4.6 · scipy 1.15.3 · fastapi 0.141.1 · pydantic 2.13.5 ·
uvicorn 0.54.0 · httpx 0.28.1 · pytest 9.1.1（`requirements.txt` 为等号锁定）

## 已知限制

- 序列为标量特征（1 维帧）；多维特征需先降维或扩展 `distance.py`
- 精确 2 倍重采样的端点落在可达锥边界外一个样本（见"可达性"），属步型固有性质，
  此时服务按契约显式失败而非近似对齐
- 带内全零距离的退化对齐标记为 `indeterminate`，调用方不应将其路径当作唯一解
