# SOS Cascade IIR Audio Filter Service

二阶节（second-order sections, SOS）级联 IIR 音频过滤服务。支持多声道、
任意分块（chunked）流式处理与带版本号的参数热切换。

技术栈：Python 3.12 · FastAPI · NumPy · SciPy · pytest

## 结构

```
app/
  config.py            # 资源上限与数值阈值（唯一事实来源）
  errors.py            # 错误分类学：input_error / not_found / state_conflict /
                       #   resource_exhausted / computation_failure
  models.py            # Pydantic 请求/响应契约
  streams.py           # 流注册表：生命周期、param_version、瞬态策略
  main.py              # FastAPI 路由与错误信封
  dsp/
    coefficients.py    # 系数规范化（a0）与极点稳定性检查
    filter.py          # SOS 级联引擎（DF2T，每声道每节独立状态）
tests/
  reference.py         # 独立纯 Python 差分方程参考实现（仅供测试）
  test_coefficients.py # 系数契约：a0=0 拒绝、不稳定极点拒绝、近单位圆接受
  test_filter_numerics.py  # 脉冲/阶跃/多声道/高 Q，对照独立参考与 scipy
  test_chunking.py     # 任意切块与整段输出逐位一致
  test_streams.py      # 参数版本、瞬态策略、状态隔离
  test_api.py          # HTTP 端到端，逐类断言错误类别
```

## 算法与契约

### 数值结构

每节采用转置直接 II 型（DF2T），与 `scipy.signal.lfilter` 的 `zi` 语义一致：

```
y[n] = b0·x[n] + z0
z0   = b1·x[n] − a1·y[n] + z1
z1   = b2·x[n] − a2·y[n]
```

状态形状为 `(n_sections, n_channels, 2)`：**每声道每节状态完全隔离**。
每个样本的运算顺序与切块方式无关，因此同一信号整段处理与任意连续切块
处理的输出**逐位一致**（有测试断言 `np.array_equal`）。

### 系数契约

- 输入为 `(n_sections, 6)` 的 `[b0, b1, b2, a0, a1, a2]` 行。
- `|a0| ≤ 1e-300` 拒绝（`input_error`），绝不静默修补。
- 所有系数必须有限；按行除以 `a0` 规范化。
- 每节极点（`z² + a1·z + a2` 的根）必须满足 `|p| < 1.0`；
  位于或超出单位圆的极点拒绝，任意接近单位圆的极点接受。

### 初始条件与瞬态策略

- 新流以**零初始条件**（zero-state）启动。
- 参数切换时按流的瞬态策略处理：
  - `carry`：保留延迟状态。无状态丢失，但旧状态在新系数下解释，会产生
    短暂瞬态；若节数变化，状态无法对应，确定性地清零。
  - `reset`：切换时清零全部状态，确定性重启，边界处输出可能跳变。
- `POST /streams/{id}/reset` 可随时显式清零状态。

### 参数版本

每个流持有单调递增的 `param_version`（从 1 开始，每次成功的系数更新 +1）。
分块处理与系数更新都可携带 `expected_param_version`；不匹配返回
`409 state_conflict`，使"控制器调参"与"流式工作进程"之间的丢更新竞争
可检测而非静默。

### 非有限输出

输入样本必须有限（否则 `422 input_error`）。每个块处理后检查输出与状态
的有限性；溢出/NaN 抛出 `500 computation_failure` 并报告首个出错位置，
**绝不静默清零或截断**。

## 错误信封

所有失败返回统一结构，类别可机读区分：

```json
{"error": {"category": "state_conflict", "message": "...", "detail": {...}}}
```

| category | HTTP | 含义 |
|---|---|---|
| `input_error` | 422 | 系数/样本非法、形状错误 |
| `not_found` | 404 | 未知流 id |
| `state_conflict` | 409 | 参数版本冲突 |
| `resource_exhausted` | 413 | 超过流/声道/节/块长上限 |
| `computation_failure` | 500 | 数值溢出或非有限输出 |

资源上限见 `app/config.py`：1024 流、64 声道、64 节、单块 1,000,000 样本。

## 运行

```bash
pip install -r requirements.txt   # 依赖已锁定
python3 -m uvicorn app.main:app --port 8765
```

### 示例调用

```bash
# 1. 创建双流道、4 阶 Butterworth 低通（2 节）流
SID=$(curl -s -X POST localhost:8765/streams -H 'content-type: application/json' -d '{
  "sos": [[0.004824, 0.009649, 0.004824, 1.0, -1.0486, 0.29614],
          [1.0, 2.0, 1.0, 1.0, -1.32091, 0.63274]],
  "n_channels": 2, "sample_rate": 48000, "transient": "carry"
}' | python3 -c "import sys,json;print(json.load(sys.stdin)['stream_id'])")

# 2. 分块推送样本（形状为 [声道][样本]）
curl -s -X POST localhost:8765/streams/$SID/chunks -H 'content-type: application/json' \
  -d '{"samples": [[1.0, 1.0, 1.0, 1.0], [1.0, 1.0, 1.0, 1.0]],
       "expected_param_version": 1}'

# 3. 带版本守卫的在线换参（瞬态策略 reset）
curl -s -X PUT localhost:8765/streams/$SID/coefficients \
  -H 'content-type: application/json' \
  -d '{"sos": [[1.0, 0.0, 0.0, 1.0, 0.0, 0.0]],
       "expected_param_version": 1, "transient": "reset"}'

# 4. 查询 / 重置 / 删除
curl -s localhost:8765/streams/$SID
curl -s -X POST localhost:8765/streams/$SID/reset
curl -s -X DELETE localhost:8765/streams/$SID
```

## 验证

```bash
python3 -m pytest
```

45 个测试，全部通过（本次运行：`45 passed in ~2s`）。要点：

- **独立参考**：`tests/reference.py` 为不依赖被测核心与 SciPy 的纯 Python
  差分方程实现；另与 `scipy.signal.sosfilt` 一次性路径交叉对照
  （容差 1e-10），并与解析直流增益对照（阶跃响应）。
- **切块一致性**：随机切块（含逐样本）与整段输出断言**逐位相等**。
- **高 Q**：8 阶 Butterworth（极点距单位圆 <0.1）2 万点脉冲响应保持有限、
  衰减且与 SciPy 参考误差 <1e-8。
- **失败类别**：每类错误（输入/未找到/版本冲突/资源耗尽/计算失败）都有
  独立测试断言状态码与类别，而非仅"接口能调用"。
- **可重放日志**：每个测试把 run_id、随机种子、关键中间量（最大误差、
  极点半径、块划分、版本号）与判定理由写入 `test_logs/run-<id>.jsonl`。

## 剩余限制

- 流状态保存在进程内存中：重启即丢失，无持久化/多副本共享。
- 单进程内的注册表有锁，但 FastAPI 同步路由下并发吞吐受 GIL 限制；
  大流量场景应部署多 worker 并按流 id 做粘性路由。
- 极点检查基于 `np.roots` 的浮点结果；极点模长与 1.0 相差在浮点噪声
  量级（~1e-15）内的边界情形，判定可能受求根误差影响。
- 分块上限 1e6 样本为单请求保护，不是背压机制；服务端无流式传输
  （chunked transfer）接口，超大信号需客户端自行切块。
