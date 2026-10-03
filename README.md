# LMS / NLMS 自适应滤波后端

基于 Python + FastAPI + NumPy + SciPy 的有参考噪声合成信号 LMS/NLMS 处理后端。
所有数据均为本地合成夹具（确定性种子），无外部账号与真实业务数据依赖。

## 工程结构

```
app/
  config.py            # 容量限制、学习率上限等运行配置（LMS_LOG_DIR 可覆盖日志目录）
  errors.py            # 四类错误契约：input_validation / state_conflict /
                       #   resource_exhausted / computation_failure
  contracts.py         # Pydantic 请求/响应模型（样本契约）
  logging_utils.py     # 按 run_id 落盘的 JSONL 运行日志
  algorithms/lms.py    # LMS/NLMS 核心：固定更新顺序、NLMS 能量正则、原子块处理
  algorithms/metrics.py# 以已知干净信号为准的评估指标
  streams/session.py   # 流注册表：多通道状态隔离、序号连续性、冻结区间
  fixtures/synth.py    # 合成场景：可控噪声、参考失相关、静音、植物突变
  main.py              # FastAPI 应用工厂与路由
scripts/run_acceptance.py  # 验收脚本（经 HTTP API 全链路）
tests/                 # pytest 套件，含独立纯 Python 参考实现 reference_impl.py
```

## 关键契约

### 滤波器状态与更新顺序（固定，不得重排）

缓冲区保存最近 L 个参考样本，最旧在前、最新在后；系数向量同序存放。
相对 `scipy.signal.lfilter` 的卷积核约定，学得的系数是**时间反转**的冲激响应
（`weights[L-1-k]` 乘以 `x(n-k)`），与已知植物比较时须用 `plant[::-1]`。

每个样本严格按序：

1. `x(n)` 移入缓冲区（丢最旧、追加最新）
2. `y(n) = w · buffer`
3. `e(n) = d(n) − y(n)`（用更新前权重）
4. 未冻结时更新：LMS `w += μ·e·buffer`；NLMS `w += μ·e·buffer/(ε + buffer·buffer)`

NLMS 的 `ε > 0` 能量正则保证分母严格为正，静音（全零参考）不会除零。
块处理是原子的：在状态副本上计算，出现非有限值时整体回滚并抛
`computation_failure`，调用方可以同一序号重试。

### 学习率与冻结

- LMS：`0 < μ ≤ 1.0`（配置上限；实际稳定性还依赖输入功率，文档不承诺收敛）
- NLMS：`0 < μ < 2`（排他上界，越界拒绝而非截断）
- 冻结：流级 `frozen_until_index`（绝对序号小于该值的样本只滤波不适配）
  与块级 `freeze_adaptation`（整块冻结）；冻结样本仍推进缓冲区与序号

### 评估准则

成功只以**已知干净信号**判定：`noise_reduction_db =
10·log10(mean((d−s)²)/mean((e−s)²))`，以及已知植物时的系数失配
`coefficient_error_db`。输出能量下降**不是**去噪成功——冻结在零权重的滤波器
输出能量最小，但降噪为 0 dB（`tests/test_scenarios.py` 中有专门用例钉死这一点）。

### 错误契约

所有错误返回 `{"error": {kind, reason, message, detail, run_id}}`：

| kind | HTTP | 示例 reason |
|---|---|---|
| `input_validation` | 422 | `mu_range`、`epsilon_range`、`length_mismatch`、`non_finite_input`、`schema_violation`、`no_noise_present` |
| `state_conflict` | 409 / 404 | `index_mismatch`、`stream_exists`、`unknown_stream`、`unknown_channel` |
| `resource_exhausted` | 413 | `block_too_long`、`too_many_streams`、`too_many_channels` |
| `computation_failure` | 500 | `non_finite_state`（状态已回滚，可重试） |

### 多通道隔离

每个 `(stream_id, channel_id)` 拥有独立的 `AdaptiveFilter` 实例，缓冲区与权重
不共享；块必须按 `start_index == next_index` 连续到达，否则 `state_conflict`。

## 复现（从干净目录）

依赖版本（`requirements.txt` 锁定，开发验证环境 Python 3.12.3）：

```
numpy==2.4.6  scipy==1.15.3  fastapi==0.141.1  pydantic==2.13.5
uvicorn==0.54.0  httpx==0.28.1  pytest==9.1.1
```

```bash
pip install -r requirements.txt

# 单元 / 数值 / 契约测试（60 项）
python -m pytest tests/ -q

# 验收脚本（经 HTTP API 全链路，日志写入 logs/）
python scripts/run_acceptance.py --log-dir logs

# 启动服务
uvicorn app.main:app --port 8000
```

### 请求样例

```bash
# 建流：NLMS，64 阶，μ=0.1，前 1000 样本冻结适配
curl -X POST localhost:8000/v1/streams -H 'content-type: application/json' -d '{
  "stream_id": "demo", "algorithm": "nlms", "filter_length": 64,
  "mu": 0.1, "channels": ["ch0"], "frozen_until_index": 1000
}'

# 送块：start_index 必须等于通道的 next_index
curl -X POST localhost:8000/v1/streams/demo/channels/ch0/blocks \
  -H 'content-type: application/json' -d '{
    "start_index": 0, "reference": [0.1, -0.2, 0.3], "desired": [0.5, 0.4, 0.3]
  }'

# 查状态（权重、缓冲区、next_index）
curl localhost:8000/v1/streams/demo/channels/ch0/state

# 评估：以已知干净信号为准
curl -X POST localhost:8000/v1/evaluate -H 'content-type: application/json' -d '{
  "desired": [...], "residual": [...], "clean": [...],
  "plant_weights": [...], "estimated_weights": [...]
}'
```

## 测试设计要点

- **独立参考实现**：`tests/reference_impl.py` 用纯 Python（无 NumPy）按教科书
  公式另写一遍 LMS/NLMS，`test_against_reference.py` 对 500 步轨迹（权重、
  缓冲区、输出、误差）做到 1e-12 一致；单步系数另有手工字面值断言
  （`test_lms_core.py`），参考答案不由被测核心生成。
- **可控噪声通道**：`correlated_noise_scenario` 噪声 = 参考经已知植物滤波，
  可识别；`decorrelated_reference_scenario` 噪声与参考独立，是文档化的失败
  情形（降噪 ≈ 0 dB，评估如实判失败）。
- **静音**：全零参考验证 NLMS 能量正则，权重不变、无 NaN。
- **突变**：植物在中点切换，验证再收敛且最终系数对齐第二个植物。
- **分块等价**：一整块与两块连续处理结果逐位一致（流式一致性）。

## 验收结果（2026-10-03 实测记录）

`python -m pytest tests/ -q`：**60 passed**（约 1.2 s）。

`python scripts/run_acceptance.py --log-dir logs`：**8/8 通过**：

```
[PASS] correlated_noise_nlms: noise_reduction=16.21 dB (>12 expected), coefficient_error=-17.08 dB (<-12 expected) (run_id=58af4eff8e2e)
[PASS] correlated_noise_lms: noise_reduction=11.89 dB (>10 expected) (run_id=4bcd8e86232d)
[PASS] silence_no_divide_by_zero: zero reference: all outputs finite, weights unchanged (weight_norm=0.0) (run_id=0c33ea321d4f)
[PASS] decorrelated_reference_fails_honestly: documented failure case: noise_reduction=-1.38 dB (~0 expected), success flag=False (run_id=02b7eecd70a6)
[PASS] abrupt_plant_change_reconverges: post-change noise_reduction=18.45 dB (>15 expected, change at index 4000) (run_id=8ac38309f908)
[PASS] freeze_interval_respected: frozen_samples=1000 (1000 expected), adaptation active afterwards (weight_norm=1.0312) (run_id=63f623034b16)
[PASS] multi_channel_isolation: channel a adapted (norm=0.8238), channel b untouched (index=0)
[PASS] error_kinds_distinguishable: input_validation=True, state_conflict=True, computation_failure+rollback=True (run_id=dadc389ddb07)
```

每次运行生成 `logs/run-<run_id>.jsonl`（含时间戳、run_id、事件、关键中间量
如 weight_norm / error_rms / frozen_samples、评估判定理由），汇总于
`logs/acceptance-summary.json`，可按 run_id 重放定位问题。

注：NLMS 稳态失调约为 `μ/(2−μ)`，μ=0.5 时降噪上限约 13 dB；场景阈值据此
（及实测）标定，μ=0.1 时实测 16.2 dB。
