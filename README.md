# seqdist — 合成对齐序列的 p 距离与受限替换模型校正距离

对**已对齐**的合成序列对计算：

| 模型 | 公式 | 有效域 | 假设 |
|------|------|--------|------|
| `p` | d = p（观察错配比例） | 0 ≤ p ≤ 1 | 不做多重替换校正；只统计双序列均为 A/C/G/T 的位点 |
| `jc69` | d = −¾·ln(1 − 4p/3) | p < 0.75 | 等平衡碱基频率（各 0.25）；12 类替换速率相等；每位点替换为齐次泊松过程 |
| `k80` | d = −½·ln(1 − 2P − Q) − ¼·ln(1 − 2Q) | 1−2P−Q > 0 且 1−2Q > 0 | 等平衡碱基频率；替换分转换（A↔G、C↔T）与颠换两个速率类；齐次泊松过程 |

P、Q 分别为转换、颠换位点占有效位点的比例。

## 行为契约

1. **有效比较位点**：仅当某一列两条序列都是无歧义碱基（A/C/G/T）时计入；
   含缺口（`-`/`.`）或 IUPAC 简并碱基（R/Y/W/S/K/M/B/D/H/V/N）的列按
   成对删除排除，并按原因分别计数（`n_excluded_gap` / `n_excluded_ambiguous`）。
   全部位点被排除时抛出 `NoValidSitesError`（类别 `computation_failure`）。
2. **超有效域**：对数真数 ≤ 0 时返回 `status="saturated"`、`distance=null`，
   并给出真数值的理由串；**绝不对负对数取绝对值**。p 距离在同一输入上仍然
   有定义，可对照。
3. **置信区间**：对有效位点做有放回重采样（bootstrap）。随机源为每次调用
   新建的 `np.random.default_rng(seed)`，第 i 个重复以
   `rng.integers(0, n, size=n)` 抽索引，顺序循环——结果完全由
   `(位点编码, 模型, n_replicates, alpha, seed)` 决定，可精确复现。
   落在模型有效域外的重复计入 `n_saturated` 并从分位数中剔除（不截断、
   不取绝对值）；有效重复 < 2 时区间报告 `non_estimable`。
4. **模型假设分别说明**：`/v1/models` 端点与每次运行结果的
   `model_assumptions` / `model_valid_domain` 字段逐模型列出。

## 模块关系

```
seqdist/
  errors.py      错误分类：input_error / state_conflict / resource_exhausted
                 / computation_failure / not_found（跨模块统一契约）
  parsing.py     合成序列与 FASTA 夹具解析、字母表校验（只校验，不改写）
  models.py      模型注册表：描述、假设、有效域（数据而非代码分支）
  distance.py    领域算法：位点分类、p 距离、JC69/K80 校正（纯函数）
  bootstrap.py   位点重采样置信区间（固定随机源契约）
  provenance.py  SQLite 运行记录：run_id、输入 SHA-256、参数、中间状态、结论
  service.py     编排：解析→分类→校正→重采样→记录，输出带 run_id 的诊断日志
  api.py         FastAPI 验证接口（薄层，只做 HTTP 映射）
```

数据契约：`parsing.ParsedSequences` → `distance.SiteStats`（含每位点编码）
→ `distance.DistanceEstimate` / `bootstrap.BootstrapResult` →
`provenance.RunRecord`。错误契约：所有模块抛出 `errors.SeqDistError`
子类，API 层按类别映射为 400 / 404 / 409 / 413 / 422。

## 依赖版本

Python 3.12；fastapi 0.141.1、uvicorn 0.54.0、numpy 2.4.6、pydantic 2.13.5、
httpx 0.28.1、pytest 9.1.1、pytest-cov 7.1.0（见 `requirements.txt`，全部本地
固定版本，无外部服务与真实业务数据）。

## 本地验证命令与预期判断

```bash
pip install -r requirements.txt

# 1. 单元/接口测试：预期 42 passed，覆盖率 TOTAL ≥ 95%
python3 -m pytest tests/ -q --cov=seqdist --cov-report=term-missing

# 2. 启动验证接口（SQLite 落库路径可用 SEQDIST_DB 指定）
SEQDIST_DB=/tmp/seqdist.sqlite3 python3 -m uvicorn seqdist.api:app --port 8137

# 3. 已知错配比例（20 位点 1 错配，p=0.05）：
#    预期 distance ≈ 0.0517447（手算 -0.75·ln(1-4·0.05/3)），status=ok，
#    95% 区间含点估计，sites.n_valid=20、n_match=19
curl -s -X POST localhost:8137/v1/distance -H 'Content-Type: application/json' \
  -d '{"seq1":"ACGTACGTACGTACGTACGT","seq2":"ACGTACGTACGTACGAACGT","model":"jc69","n_replicates":500,"seed":42}'

# 4. 饱和（p=0.75）：预期 status=saturated、distance=null、reason 含 "1-4p/3"
curl -s -X POST localhost:8137/v1/distance -H 'Content-Type: application/json' \
  -d '{"seq1":"AAAA","seq2":"AGCT","model":"jc69"}'

# 5. 全缺失：预期 HTTP 422，error.category=computation_failure
curl -s -X POST localhost:8137/v1/distance -H 'Content-Type: application/json' \
  -d '{"seq1":"NNNN","seq2":"N-NN"}'

# 6. 溯源回放：用上一步返回的 run_id 查询，预期 intermediates 含
#    n_valid/p/bootstrap_n_ok 等中间状态，result 与原响应一致
curl -s localhost:8137/v1/runs/<run_id>
```

错误类别与 HTTP 状态对应：`input_error`→400（非法字符、长度不齐、未知模型）、
`not_found`→404、`state_conflict`→409（重复 run_id）、
`resource_exhausted`→413（序列长度 > 1 000 000 或重复数 > 100 000）、
`computation_failure`→422（无有效位点等）。

## 测试与诊断说明

- 参考值来源：`tests/test_distance.py` 中的 JC69/K80 期望值由 `math.log`
  按公开公式在测试内独立手算（含硬编码常数锚点）；`tests/test_bootstrap.py`
  的参考区间由测试文件内独立编写的重采样循环生成，**不由被测核心实现产生**。
- 覆盖场景：已知错配比例、完全相同序列、饱和（p=0.75 与 p=0.8、Q≥0.5）、
  全缺失/全简并、缺口与简并碱基排除计数、固定种子可复现性、饱和重复计数、
  溯源回写与冲突、API 各错误类别。
- 诊断：每次运行生成 `run_id`（uuid4），`seqdist.service` 日志以该 id 标注
  关键中间状态（n_valid、p、排除计数、bootstrap ok/saturated 数）与判断理由；
  同一批中间状态持久化于 SQLite `runs` 表，可凭 run_id 重放。
- 当前状态：42 个测试全部通过，行覆盖率 98%。无未运行或被跳过的测试。
