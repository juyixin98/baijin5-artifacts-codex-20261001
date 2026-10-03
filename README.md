# LMS/NLMS 参考噪声自适应滤波后端

基于 Python + FastAPI + NumPy/SciPy 的合成信号自适应噪声对消后端。所有数据均由
本地带种子的合成夹具生成，不依赖任何生产账号或真实业务数据。

## 功能边界

- **信号算法**：LMS 与 NLMS 单通道自适应 FIR 滤波器，固定更新顺序
  （移位参考样本 → 用*更新前*权重计算输出与误差 → 权重更新）；
  NLMS 分母为 `eps + ||x||²`（`eps ≥ 1e-12`），静音参考不会除零。
- **学习率**：`mu ∈ (0, 2)`（开区间，见 `app/config.py`）。NLMS 该区间即经典稳定域；
  LMS 的稳定性还依赖参考信号功率（约 `mu < 2 / (L·E[x²])`），后端只强制绝对边界。
- **冻结适配区间**：按样本冻结（半开区间 `[start, end)`），冻结期间仍用当前权重
  计算输出/误差，但跳过权重更新，权重在冻结区间内逐位不变。
- **评估准则**：所有指标对已知干净信号与真实系数计算（SNR 改善 dB、
  残差 vs 干净信号 MSE、系数误差范数），**绝不**以输出能量下降当作去噪成功。
- **多通道**：每个通道持有独立滤波器状态，互不可见；容量与块长上限可配置。

## 目录结构

```
app/
  config.py          # 学习率范围、eps 下限、容量上限（frozen dataclass）
  errors.py          # 四类可区分错误（见下）
  logging_utils.py   # run_id + JSON 结构化运行日志
  dsp/lms.py         # LMS/NLMS 核心状态机与冻结区间
  dsp/fixtures.py    # 合成夹具：相关噪声 / 参考失相关 / 静音 / 突变
  dsp/metrics.py     # 对已知干净信号的评估指标
  state/channels.py  # 多通道状态存储与隔离
  schemas.py         # 请求/响应契约（Pydantic）
  main.py            # FastAPI 路由与错误映射
tests/               # 数值测试（手算常量 + SciPy 独立参考，非自证）
```

## 错误契约（可区分）

| category             | HTTP | 含义示例 |
|----------------------|------|----------|
| `input_error`        | 400  | mu 越界、NaN 样本、块长不一致、冻结区间越界/重叠 |
| `state_conflict`     | 409  | 通道不存在/已删除、通道 ID 重复 |
| `resource_exhausted` | 429  | 通道数超上限、单块样本数超上限 |
| `computation_failed` | 500  | 权重更新产生非有限值（数值发散） |

错误响应包络：`{"error": {"category", "message", "detail", "run_id"}}`。

## 复现（从干净目录）

```bash
cd opp506/b
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # 版本已锁定，见文件
python -m pytest                   # 40 个测试，覆盖率门槛 80%
python -m uvicorn app.main:app --port 8506
```

依赖版本（`requirements.txt`）：fastapi 0.141.1、uvicorn 0.54.0、numpy 2.4.6、
scipy 1.15.3、pydantic 2.13.5、httpx 0.28.1、pytest 9.1.1、pytest-cov 7.1.0；
Python 3.12。

## 请求样例

```bash
# 建通道
curl -X POST localhost:8506/channels -H 'content-type: application/json' \
  -d '{"algorithm":"nlms","filter_len":4,"mu":0.05,"channel_id":"demo"}'

# 流式处理一个块，样本 [4,6) 冻结适配
curl -X POST localhost:8506/channels/demo/process -H 'content-type: application/json' \
  -d '{"reference":[0.5,-0.25,1.0,0.75,-0.5,0.25,0.0,0.125],
       "primary":[1.0,0.5,-0.75,0.25,0.5,-0.125,0.375,0.625],
       "freeze_intervals":[{"start":4,"end":6}]}'

# 查询/删除通道状态
curl localhost:8506/channels/demo/state
curl -X DELETE localhost:8506/channels/demo

# 一键场景评估（指标对已知干净信号计算）
curl -X POST localhost:8506/evaluate -H 'content-type: application/json' \
  -d '{"scenario":"correlated_noise","n_samples":8000,"seed":42,"mu":0.02}'
```

场景：`correlated_noise`（可解）、`decorrelated_reference`（预期失败：
参考与主通道噪声统计独立）、`silence`（静音窗）、`abrupt_change`（噪声路径突变）。

## 测试与判断依据

```bash
python -m pytest                 # 全部测试 + 覆盖率
python -m pytest -k failure      # 只看失败情形（失相关/静音/突变/冻结）
```

测试日志（`log_cli` 已开启）保留可重放信息：run_id、种子、关键中间状态
（权重范数、最小分母、分段 MSE）与判断理由。参考答案来源独立：

- 单步 LMS/NLMS 系数为**手算常量**（`tests/test_lms_core.py`）；
- 块级处理由测试内**反向缓冲区约定的独立循环**复核；
- 夹具正确性由 **SciPy `lfilter`** 交叉验证（夹具自身用 `np.convolve`）。

关键数值事实（测试阈值依据，本机实测）：

- NLMS 稳态失调约 `mu/(2-mu)`；本夹具中干净信号泄漏进更新项形成
  平稳权重噪声底（均值 ≈0.07），因此系数误差断言用尾部均值而非单点。
- `correlated_noise`、mu=0.02、n=8000：SNR 改善 ≈ 18.5 dB（阈值 >15 dB）。
- `decorrelated_reference`：SNR 改善 ≈ -0.2 dB（|增益| < 3 dB 即判定为
  预期失败类别 `reference_not_correlated`）。
- 突变后误差尖峰用 100 样本窗测量（滤波器数百样本内再收敛，长窗会
  把尖峰平均掉）。

## 实测记录（2026-10-03，本机 Python 3.12.3）

- `python -m pytest`：**40 passed**，覆盖率 **98.48%**（门槛 80%）。
- 冒烟（uvicorn + curl）：建通道/带冻结处理/状态查询/删除/四类错误
  类别均符合契约；`correlated_noise` 评估 SNR 改善 18.55 dB、
  系数误差 0.0287；`decorrelated_reference` 评估 -0.21 dB（预期失败）。
