# motifscan — PWM 模体扫描与显著性校准（合成 DNA）

对合成 DNA 序列执行 PWM（位置权重矩阵）模体扫描，并通过**枚举全部 k-mer 得分
分布**做精确显著性校准：每个命中附带在**声明的背景模型**下的精确 p 值与多次
扫描校正结果。统计显著 ≠ 生物功能，本服务只回答"该得分在声明背景下是否罕见"。

## 模块划分

| 模块 | 职责 |
|---|---|
| `app/sequence.py` | 合成序列解析：原始序列（容忍空白/大小写）与 FASTA，仅接受 A/C/G/T/N |
| `app/config.py` | 声明式背景模型：校验归一化、拒绝零概率碱基 |
| `app/pwm.py` | 计数矩阵 + 显式伪计数 → 概率 → log2 似然比得分 |
| `app/calibration.py` | 枚举全部 4^k k-mer 的精确零分布；p 值与阈值 |
| `app/scanning.py` | 双链滑窗扫描；未知碱基策略；重叠命中保留身份 |
| `app/correction.py` | 多次检验校正（Bonferroni、Benjamini–Hochberg） |
| `app/provenance.py` | SQLite 结果溯源：请求身份、配置、输入哈希、结果/失败 |
| `app/service.py` | 编排：解析→建模→校准→扫描→校正→落库，逐步记日志 |
| `app/main.py` | FastAPI 边界：请求身份中间件、错误分类映射 |
| `app/errors.py` | 声明式失败分类（稳定 `category` 字符串） |

## 本地启动

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # 锁定版本，见文件头注释
./run.sh                          # 或: uvicorn app.main:app --port 8000
```

环境变量：`MOTIFSCAN_DB_PATH`（溯源库路径，默认 `./motifscan.db`）、`PORT`。

## 运行测试

```bash
python3 -m pytest          # 55 个测试
```

## 示例请求

```bash
bash examples/run_examples.sh        # 需服务已启动；BASE=http://127.0.0.1:8000
```

扫描（核心示例）：

```bash
curl -X POST http://127.0.0.1:8000/v1/scan \
  -H 'content-type: application/json' -H 'x-request-id: my-run-1' \
  -d @examples/scan_request.json
```

响应包含：`request_id`、解析后的配置、阈值（对应声明背景）、命中（得分、
p 值、Bonferroni/BH 校正、坐标与链）、`uncertain_hit_ids`（原始显著但校正后
不显著，单列）、跳过窗口及原因、解释性 caveats。

```bash
curl -X POST .../v1/calibrate -d @examples/calibrate_request.json   # 精确分布+阈值
curl -X POST .../v1/validate  -d '{...}'                            # 干跑校验，收集全部失败类别
curl .../v1/requests/my-run-1                                       # 溯源记录（含失败记录）
```

## 关键设计取舍

- **精确枚举而非采样/渐近近似**：校准枚举全部 4^k k-mer，按声明背景加权。
  因此声明支持上限为模体长度 ≤ 10（约 1M k-mer）；更长模体返回声明式失败
  `motif_too_long_for_exact_calibration`，而不是悄悄换成近似方法。
- **背景模型是显式输入**：所有得分/p 值/阈值只对所声明的背景成立。零背景
  概率被拒绝（`zero_background_probability`），因为对数几率无定义——调用方
  需在背景中声明正的下限，而不是由实现静默截断。
- **伪计数显式**：PWM 概率 = (count + pseudocount) / 归一化，默认 1.0，
  必须为正。
- **双链与坐标**：负链通过对窗口取反向互补序列用同一 PWM 打分；所有坐标
  以正链 0-based 半开区间 `[start, end)` 报告，`matched_sequence` 记录实际
  被打分的方向序列。
- **未知碱基（N）两条显式规则**：`skip`（窗口不打分，记入 skipped_windows，
  不计入校正族大小）或 `marginalize`（该位置贡献
  log2(Σ_b bg(b)·PWM(b)/bg(b)) = log2(1) = 0）。无默认静默行为。
- **重叠命中保留身份**：每个窗口独立打分，命中以 `hit_id` +
  (seq_id, start, end, strand) 标识，不去重不合并。
- **多次扫描校正**：族大小 = 双链全部已打分窗口数；同时报告 Bonferroni 与
  BH；原始显著但 BH 不显著的命中单列为 `uncertain_hit_ids`。
- **阈值不可达是显式结论**：若零分布最小尾概率仍大于 alpha（离散分布常见），
  返回 `achievable: false` 与 `score: null`，而不是给出达不到声明 alpha 的阈值。
- **可解释性**：每请求一个 `request_id`（可用 `x-request-id` 头自带），日志逐
  步记录 parse/build_model/calibrate/scan/correct/done；成功与失败都写入
  SQLite 溯源库，失败记录含稳定 `category`。

## 失败类别（声明式错误分类）

`invalid_character`、`empty_sequence`、`fasta_format_error`、
`duplicate_sequence_id`、`background_not_normalized`、
`zero_background_probability`、`invalid_pseudocount`、`invalid_motif_matrix`、
`motif_too_long_for_exact_calibration`、`unknown_base_policy`、`invalid_alpha`、
`request_schema_error`、`request_not_found`。

## 支持范围

- 模体长度 1–10（精确枚举）；序列字符集 A/C/G/T/N；单请求多序列。
- 不做的：更长模体的近似校准、简并（IUPAC 多态）碱基、Gibbs/EM 模体发现、
  真实基因组规模扫描。统计命中不构成生物功能结论。

## 测试执行记录

- 最终：`55 passed`（Python 3.12.3，numpy 2.4.6，fastapi 0.141.1），
  行覆盖率 98%（`pytest --cov=app`，唯一未覆盖多为防御性分支）。
- 过程中保留的失败：首轮 `54 passed, 1 failed`——
  `test_threshold_corresponds_to_declared_background` 的手算参考值推导有误
  （误将 pos2 的 G 概率当作 1/8，实为 5/8，故 AG 唯一达到最高分，
  P = 0.4×0.1 = 0.04 而非 0.08）。**被测实现输出正确，参考值已按正确推导
  修正**（见 `tests/test_calibration.py` 注释）。这正是"参考答案不由被测
  实现生成"所要捕获的偏差类型。
- 已知告警（未处理项）：`StarletteDeprecationWarning: Using httpx with
  starlette.testclient is deprecated`——来自 fastapi 0.141.1 的 TestClient，
  不影响结果；后续版本可迁移 `httpx2`。
- 无跳过/未执行的测试项。
