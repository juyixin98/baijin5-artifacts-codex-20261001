# 规则酶切片段枚举服务 (Rule-based Enzymatic Digest Service)

对合成蛋白字符串执行**规则驱动的酶切片段枚举**：显式的切割位点、阻断上下文、
允许漏切数；统一的 N 端/C 端边界与空片段处理；片段保留父序列位置溯源；
并严格区分**未知残基**与**质量不确定状态**。

技术栈：Python 3.12 · FastAPI · NumPy · SQLite（标准库 `sqlite3`）· Pydantic v2 · pytest。

---

## 1. 支持范围

### 1.1 残基字母表

| 类别 | 字母 | 处理 |
|---|---|---|
| 确定残基 | `A C D E F G H I K L M N P Q R S T V W Y` + 硒代半胱氨酸 `U` | 单一化学身份，质量确定 |
| 模糊残基 | `B`(N/D)、`Z`(Q/E)、`J`(I/L)、`X`(任意标准残基，不含 U) | 可解析；质量给出 `[min,max]` 区间 |
| 非法字符 | 其它任何符号 | 返回 `UNSUPPORTED_RESIDUE`（含 1-based 位置），**不计算、不伪装成功** |

关键区分：`J`（I/L）残基**身份未知但质量确定**（二者等质量），此时
`has_ambiguous=true` 而质量状态仍为 `DETERMINATE`——身份模糊与质量不确定是两回事。

### 1.2 内置酶 / 化学裂解规则

| key | 规则 | 阻断上下文 |
|---|---|---|
| `trypsin` | K/R 之后切 | 后随 P 不切 |
| `arg_c` | R 之后切 | 后随 P 不切 |
| `lys_c` | K 之后切 | 后随 P 不切 |
| `chymotrypsin` | F/Y/W 之后切（严格版） | 后随 P 不切 |
| `glu_c` | E/D 之后切 | 后随 P 不切 |
| `asp_n` | D 之前切 | 无 |
| `pepsin` | F/L/W/Y 之前切（pH1.3 简化） | 前导 P 不切 |
| `cnbr` | M 之后切（化学裂解） | 无 |

也支持请求内联的**自定义规则**（`enzyme="custom"` + `cleave_after`/`cleave_before`
及方向性阻断集合）。`GET /enzymes` 返回全部规则的可溯源回显。

### 1.3 片段枚举语义

- **键（bond）编号**：键 `0` 为蛋白 N 端边界，键 `n` 为 C 端边界，内部键 `1..n-1`。
  边界键本身不能“切出蛋白之外”，仅用于空片段记账。
- **漏切（missed cleavages, mc）**：枚举 1..mc+1 个连续“完全酶解片段”的所有
  连续跨度，每个片段恰好一次。每个片段报告其内部跨过的切点键与漏切数。
  mc 超过实际切点数时自然封顶为整蛋白，不产生幻影片段。
- **连续切点**：如 `AKK`，两个切点各自成立，中间的 `K` 作为单残基片段保留。
- **位置溯源**：坐标为父序列 **0-based 半开区间 `[start,end)`**，响应另给
  1-based 闭区间标签 `position`。序列相同但位置不同的片段**绝不合并**
  （`AMAM` 的两个 `AM` 分别保留 N 端/C 端来源）。
- **修饰**：固定修饰作用于每个目标残基；可变修饰在每个目标位点独立取/不取
  （子集枚举）；N/C 端修饰仅在片段真正携带对应蛋白末端时生效。修饰形式数量受
  `max_modification_forms` 上限保护，超限返回 `MOD_FORM_LIMIT`。
  对可能落在模糊残基候选上的修饰返回 `MOD_TARGET_UNKNOWN_RESIDUE`，不静默少修饰。

### 1.4 质量

- 中性单同位素质量 = Σ残基质量 + H₂O；残基质量由**整数分子式**（游离氨基酸 − H₂O）
  严格导出。
- 含 `B/Z/X` 的片段质量为 `[min,max]` 区间，`status=UNCERTAIN`，`nominal` 取中值；
  确定片段三点相等，`status=DETERMINATE`。
- 电荷态 m/z 单独计算：`m/z = (M + z·m_proton)/z`（正离子）。
- **空片段不计 H₂O**，质量为 0，由 `empty=true` 单独标记。

---

## 2. 关键取舍（请先阅读）

1. **内部相邻切点不产生内部空肽段**。单向规则（“在 X 之后切”）下，`KK` 之间的
   切点切出的是真实单残基 `K`，而不是空串；空串只在**蛋白边界键也匹配规则**时出现
   （Asp-N 切在 N 端首位 D 之前 → 显式空 N 段；规则残基落在 C 端，如尾部 K/R/M
   → 显式空 C 段）。这些边界空片段被**显式返回而非丢弃**，但不参与漏切合并
   （没有可合并的实体）。这是一个明确的、可测试的边界约定。
2. **身份模糊 ≠ 质量不确定**。`J` 身份未知但 I/L 等质量，故质量仍为确定点。
3. **参考答案独立于被测核心**。片段边界/序列由人工根据规则手算后冻结到
   `tests/expected/expected_cases.json`；质量期望值由独立脚本
   `scripts/derive_expected_masses.py`（只用分子式与原子量，不调用引擎质量函数）
   生成，运行期再由第二套 **NumPy 分子式 oracle** 交叉核算。测试不是“接口能调通”，
   而是断言**具体边界、具体键、具体质量数值与失败类别**。
4. **失败不伪装成功**。域错误带稳定 `ErrorCode`（4xx/404/5xx）；验证账本逐例给
   `PASS/FAIL` 与文字失配原因，任一失败则总状态 `FAIL`。
5. 质量为中性单同位素、正离子质子化模型；不做同位素峰型、保留时间、碎裂离子
   （b/y）预测，也不含实验操作建议。

---

## 3. 本地启动

```bash
cd /home/admin/Downloads/xinbiaozhul/opp516/a
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.lock        # 完全锁定的依赖
# 或 pip install -r requirements.txt    # 直接依赖
PYTHONPATH=. python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

启动后：
- 健康检查： http://127.0.0.1:8000/health
- 交互式文档（Swagger）： http://127.0.0.1:8000/docs
- 规则与修饰清单： http://127.0.0.1:8000/enzymes

所有配置可用 `DIGEST_<NAME>` 环境变量覆盖（见 `app/config.py`），例如
`DIGEST_DB_PATH=/tmp/digest.db`、`DIGEST_LOG_LEVEL=DEBUG`。默认 SQLite 库位于
`data/digest.db`，JSON 结构化日志写入 `logs/digest.log`。

---

## 4. 示例请求

```bash
# 基础：胰蛋白酶，允许 1 个漏切，报告 z=1,2 的 m/z
curl -s -X POST http://127.0.0.1:8000/digest \
  -H 'Content-Type: application/json' \
  -d @examples/digest_basic.json

# 阻断位点（K-P 不切）
curl -s -X POST http://127.0.0.1:8000/digest \
  -H 'Content-Type: application/json' \
  -d @examples/digest_blocked.json

# N 端边界空片段（Asp-N 切在首位 D 之前）
curl -s -X POST http://127.0.0.1:8000/digest \
  -H 'Content-Type: application/json' \
  -d @examples/digest_empty_segment.json

# 模糊残基 -> 质量区间
curl -s -X POST http://127.0.0.1:8000/digest \
  -H 'Content-Type: application/json' \
  -d @examples/digest_ambiguous.json

# 自定义规则（A 之后切，P 阻断）
curl -s -X POST http://127.0.0.1:8000/digest \
  -H 'Content-Type: application/json' \
  -d @examples/digest_custom_rule.json
```

最小请求体：

```json
{ "sequence": "AAKAAAKAA", "enzyme": "trypsin", "missed_cleavages": 1 }
```

带修饰与电荷：

```json
{
  "sequence": "MMAC",
  "enzyme": "trypsin",
  "missed_cleavages": 0,
  "fixed_modifications": [{"key": "carbamidomethyl_c", "kind": "fixed"}],
  "variable_modifications": [{"key": "oxidation_m", "kind": "variable"}],
  "charges": [1, 2, 3]
}
```

结果溯源：每个成功响应带 `run_id`，可用 `GET /runs/{run_id}` 取回当时的完整输入、
酶规则回显与逐片段记录。

### 独立验证接口

```bash
curl -s -X POST http://127.0.0.1:8000/validate \
  -H 'Content-Type: application/json' \
  -d '{
    "expectations": [
      {"case_name":"hand_check","sequence":"AAKAAAKAA","enzyme":"trypsin",
       "missed_cleavages":0,"fragment_count":3,"cleavage_bonds":[3,7],
       "fragments":[
         {"start":0,"end":3,"sequence":"AAK","empty":false,"missed_cleavages":0,"n_terminal":true,"c_terminal":false},
         {"start":3,"end":7,"sequence":"AAAK","empty":false,"missed_cleavages":0,"n_terminal":false,"c_terminal":false},
         {"start":7,"end":9,"sequence":"AA","empty":false,"missed_cleavages":0,"n_terminal":false,"c_terminal":true}
       ],
       "fragment_masses":{"AAK":288.179755262,"AAAK":359.216869045,"AA":160.084792251}}
    ]
  }'
```

---

## 5. 测试与诊断

```bash
source .venv/bin/activate

# 全量测试（单元 + 集成）
PYTHONPATH=. python -m pytest

# 覆盖率（阈值参考：项目要求 ≥80%，当前约 97%）
PYTHONPATH=. python -m pytest --cov=app --cov-report=term-missing

# 仅单元 / 仅集成
PYTHONPATH=. python -m pytest -m unit
PYTHONPATH=. python -m pytest -m integration

# 重新独立推导质量期望值（不经过被测引擎）
PYTHONPATH=. python scripts/derive_expected_masses.py
```

### 测试组织

- `tests/test_parse.py`：序列归一化、确定/模糊/非法残基、失败类别与位置。
- `tests/test_mass.py`：手算常量、H₂O、不确定区间、J 等质量、空片段、m/z，
  并用独立 NumPy oracle 交叉核算。
- `tests/test_digest.py`：手算短序列、连续切点、P 阻断、pepsin N 侧阻断、
  漏切组合、位置溯源、空片段、自定义规则、固定/可变修饰枚举。
- `tests/test_modifications.py`：N/C 端修饰、模糊残基拒修、修饰上限、参数校验。
- `tests/test_validation.py`：冻结手算用例全通过；**刻意错误期望必须判 FAIL**；
  oracle 与手算值冲突也要判 FAIL（防错误夹具）。
- `tests/test_storage.py`：SQLite 运行/片段/验证账本的持久化与检索。
- `tests/test_api.py`：FastAPI 全栈——具体片段、m/z、各错误类别与状态码、溯源。

每个测试使用独立临时 SQLite 库与日志目录（`tests/conftest.py`），并在
`reports/test_report.json` 写入机器可读报告（含服务版本、逐条用例结果、耗时、
失败长文）。测试**实际运行**；失败的用例会在报告中保留 `failed` 计数与原因，
不会被吞掉。

### 日志可关联性

每条日志为单行 JSON，含 `service_version`、`run_id`、`input_ref`
（`enzyme=..;len=..;mc=..`）、计算步骤 `step`、`elapsed_ms` 与 `verdict`
（`OK`/`ERROR`）。因此任一日志行都能回溯到具体输入与运行身份，异常以
`step_failed`/`domain_error`/`unhandled_error` 明确记录，绝不统一记为成功。

---

## 6. 工程结构

```
app/
  config.py              # 配置层（env 覆盖、限值）
  logging_config.py      # 结构化 JSON 日志 + 步骤计时/判定
  domain/
    constants.py         # 原子量、分子式→残基、酶规则、修饰预设
    models.py            # 领域模型（位点/片段/质量区间/枚举结果）
    errors.py            # 稳定错误类别 ErrorCode
  services/
    parse.py             # 合成序列解析与边界校验
    digest.py            # 领域算法：位点/阻断/漏切/空片段/修饰枚举
    mass.py              # m/z 离子化
    orchestrator.py      # 解析→酶切→视图→持久化 编排
    validation.py        # 独立验证账本 + NumPy 质量 oracle
  storage/store.py       # SQLite 溯源（runs/fragments/validations）
  api/
    schemas.py           # 请求/响应 Pydantic 模型（与领域模型分离）
    deps.py routes.py    # 依赖注入与薄路由
  main.py                # create_app() 工厂与本地入口
tests/                   # 独立测试层（含冻结手算期望 expected_cases.json）
scripts/                 # 独立质量推导
examples/                # 示例请求体
reports/                 # 测试运行报告（test_report.json）
data/ logs/              # 本地运行期 SQLite / 日志
```

设计遵循分层（解析 / 领域算法 / 结果溯源 / 验证接口）、不可变数据结构、
显式错误处理与小文件高内聚；算法核心不做任何 I/O，可脱离 Web 独立调用与测试。
