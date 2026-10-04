# Synthetic Protein Digest Enumeration Service

规则酶切片段枚举服务：对合成蛋白字符串按**显式切割规则与阻断上下文**进行
酶切，枚举允许漏切（missed cleavages）的全部片段，保留每个片段的**来源
位置**，并计算成熟肽段的**单同位素质量表**。全本地运行（FastAPI + NumPy +
SQLite），无外部账号或真实业务数据依赖。

---

## 1. 能力范围与关键取舍

### 支持范围

- **酶规则显式声明**：每个酶声明 `cut_after`（在哪些残基后切）、
  `not_before`（下一残基属于该集合则阻断，如胰酶的 K/R-P）、`not_after`，
  以及合成的 N 端 / C 端端基酶。规则来自本地夹具
  `app/domain/rules.py` 的合成酶目录（8 个酶），可用
  `data/enzymes.example.json` 结构的 JSON 表经 `DIGEST_ENZYME_TABLE` 覆盖。
- **漏切枚举**：`missed_cleavages=k` 枚举至多跨 k 个切点的所有连续初级
  片段并集；`k=0` 为完全酶切。每个片段记录实际漏切数与所跨切点 bond。
- **位置溯源**：残基位置 1-based；bond `i` 位于残基 `i` 与 `i+1` 之间；
  片段边界用半开偏移 `0..n` 表示。即使两个片段字符串完全相同，也因位置
  不同拥有不同 `fragment_id` 与溯源记录，绝不按序列去重。
- **N/C 端边界**：N 端边界为 0，C 端边界为 `n`；单残基序列没有内部 bond，
  恰好产生一个同时为 N 端、C 端的片段。
- **空片段统一处理**：零长度片段被统一抑制并计数
  （`empty_fragments_suppressed`），连续切点不会产生空串。
- **成熟质量表**：单同位素残基质量求和 **+ H₂O**（中性成熟质量），并给出
  [M+H]⁺ 的 m/z（+ 质子）。半胱氨酸按**还原态**处理，不含任何 PTM；
  NumPy 批量矩阵求和。
- **未知残基 ≠ 质量不确定**，两种状态严格区分：
  - `X`（任意/未知残基，以及表外合法字母 `U`/`O`）：质量状态 `UNKNOWN`，
    所有质量数值为 `null`，绝不给出虚假数字。
  - `B`（D/N）、`Z`（E/Q）、`J`（I/L）：质量**有界**，状态 `AMBIGUOUS`，
    给出 `min/max` 中性质量与 m/z（J 两异构体等质量，上下界相同但仍标记
    为歧义）。
  - 标准 20 种残基：`EXACT`。
- **分类错误**：空序列、非法符号（空白/数字/`*`/连字符）、未知酶、漏切
  越界、序列超长、运行不存在等各有稳定错误码，未知/异常状态绝不被统一
  包装成成功响应。
- **结果溯源**：每次运行（含失败运行）连同输入、版本上下文、逐 bond 判定
  依据、全部片段与质量状态持久化到本地 SQLite。
- **可关联日志**：JSON 行日志携带 `run_id`、版本、bond 扫描进度
  （`3/12`）与每条 CUT/BLOCKED 的判定理由。

### 关键取舍（明确的不支持项）

- 不模拟酶动力学/不完全酶切概率；漏切是**枚举上界**而非随机采样。
- 不支持翻译后修饰、二硫键/烷基化、同位素标记；半胱氨酸固定为还原态。
- 质量为单同位素中性质量与单电荷 m/z，不做多电荷同位素包络。
- 规则仅覆盖单残基 P1 / P1′ 上下文（及端基），不表达多残基 motif。
- `X/B/Z/J/U/O` 之外的字符视为非法符号；不做任何静默删除或大小写外的
  自动改写（内部空白会被拒绝而非被悄悄去掉）。

---

## 2. 工程结构

```
app/
  config.py                 # 环境变量配置层（本地默认值）
  errors.py                 # 分类错误码
  logging_setup.py          # JSON 行日志 + run_id 关联
  domain/
    parsing.py              # 合成序列解析、边界约定、符号/未知分类
    rules.py                # 切割/阻断规则、合成酶目录
    mass.py                 # 成熟质量表（标量 + NumPy 批量）
    digestion.py            # 酶切枚举、漏切合并、片段溯源模型
  persistence/repository.py# SQLite 运行溯源
  services/digest_service.py# 编排：校验→版本戳→计时→持久化
  api/
    schemas.py              # 请求模型（校验边界）
    main.py                 # FastAPI 路由 + 分类错误处理
tests/
  oracle.py                 # 独立朴素 oracle 与手录质量常数（不引用被测实现）
  test_parsing.py test_rules.py test_mass.py
  test_digestion.py test_service.py test_api.py
scripts/
  verify_digest.py          # 独立手算诊断（逐用例 PASS/FAIL + run_id）
  run_tests.sh              # 带运行身份的测试归档（log + JUnit XML）
data/enzymes.example.json   # 外部酶表示例
requirements.txt / requirements.lock.txt
run.sh / examples.sh
```

---

## 3. 本地启动

需要 Python ≥ 3.10。依赖已锁定于 `requirements.lock.txt`。

```bash
# 可选：创建虚拟环境
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.lock.txt   # 或 pip install -r requirements.txt

./run.sh                               # 默认 127.0.0.1:8000
# 自定义： DIGEST_PORT=8011 ./run.sh
```

服务启动后：

- 健康检查： `GET /health`
- 交互文档： `http://127.0.0.1:8000/docs`
- 元信息（版本、质量表、约束）： `GET /api/v1/meta`

### 配置项（环境变量，均有本地默认值）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DIGEST_DB_PATH` | `data/digest.db` | SQLite 路径 |
| `DIGEST_ENZYME_TABLE` | 内置目录 | 外部酶表 JSON |
| `DIGEST_LOG_DIR` | `logs/` | JSON 日志目录 |
| `DIGEST_LOG_LEVEL` | `INFO` | 日志级别 |
| `DIGEST_MAX_SEQUENCE_LENGTH` | `10000` | 序列长度上限 |
| `DIGEST_MAX_MISSED_CLEAVAGES` | `10` | 漏切上限 |
| `DIGEST_MASS_DECIMALS` | `6` | 质量小数位 |

---

## 4. 示例请求

```bash
./examples.sh                 # 一键演示全部场景
```

核心请求：

```bash
curl -s -X POST http://127.0.0.1:8000/api/v1/digest \
  -H 'content-type: application/json' \
  -d '{"sequence":"AAKRPA","enzyme":"trypsin_syn",
       "missed_cleavages":0,"run_id":"example-1"}'
```

`AAKRPA`（胰酶，切 K/R 但 P 跟随阻断）：bond 3 = `K|R` 切，
bond 4 = `R|P` 阻断 → 片段 `AAK[1,3]`、`RPA[4,6]`。

响应要点（节选）：

```json
{
  "success": true,
  "run_id": "example-1",
  "versions": {"app_version": "1.0.0",
               "mass_table_version": "mono-residue-v1",
               "enzyme_catalog_version": "synthetic-enzymes-v1"},
  "result": {
    "cut_bonds": [3],
    "blocked_bonds": [4],
    "bond_decisions": [
      {"bond": 4, "p1": "R", "p1_prime": "P", "decision": "BLOCKED",
       "reason": "P1' P blocks cleavage (not_before)", "matched_rule": "not_before"}
    ],
    "fragments": [
      {"fragment_id": "F0001", "sequence": "AAK", "start": 1, "end": 3,
       "is_nterminal": true, "missed_cleavages": 0,
       "mass": {"status": "EXACT", "neutral_mass": 288.179755,
                "mhplus_mz": 289.187032}}
    ]
  }
}
```

未知 / 歧义质量示例：

```bash
# X -> UNKNOWN（质量数值全为 null）
curl -s -X POST .../api/v1/digest -d '{"sequence":"AAKXAA",
  "enzyme":"trypsin_syn","missed_cleavages":0}'
# B -> AMBIGUOUS（min/max 有界）
curl -s -X POST .../api/v1/digest -d '{"sequence":"AKBAA",
  "enzyme":"trypsin_no_proline_rule","missed_cleavages":0}'
```

分类失败示例（HTTP 422，`success:false`）：

```json
{"success": false,
 "error": {"code": "ILLEGAL_SYMBOL",
           "message": "illegal symbol ' ' in sequence; ...",
           "details": {"symbol": " "}}}
```

溯源查询：`GET /api/v1/runs/example-1`；列表：`GET /api/v1/runs`。

错误码：`EMPTY_SEQUENCE` / `ILLEGAL_SYMBOL` / `UNKNOWN_RESIDUE` /
`SEQUENCE_TOO_LONG` / `ENZYME_NOT_FOUND`(404) /
`INVALID_MISSED_CLEAVAGE` / `INVALID_RULE` / `RUN_NOT_FOUND`(404) /
`VALIDATION_ERROR` / `PERSISTENCE_ERROR` / `INTERNAL_ERROR`。

---

## 5. 测试与诊断

测试**独立于被测核心实现**给出答案：`tests/oracle.py` 用朴素字符串扫描
重新实现切点/片段枚举，并手工录入残基质量常数；手算短序列、连续切点、
阻断位点、漏切组合均断言**具体数值与失败类别**，而非仅检查接口可调。

```bash
# 完整测试套件（58 个用例），含覆盖率
python3 -m pytest tests/ --cov=app --cov-report=term-missing

# 带运行身份并归档日志 / JUnit XML 到 logs/test-runs/
./scripts/run_tests.sh

# 独立手算诊断：逐用例输出 PASS/FAIL、run_id 与判定依据
python3 scripts/verify_digest.py --db data/verify.db
```

最近一次实际运行结果（已执行，非未执行项）：

- `pytest`：**58 passed**，总覆盖率 **94%**（各模块 85–100%）。
- `scripts/verify_digest.py`：**8/8 PASS，0 failed，0 unexecuted**。
- 归档：`logs/test-runs/test-<时间戳>-*.log` 与同名 `.junit.xml`；
  诊断与服务日志在 `logs/digest.log`，均可用 `run_id` 关联输入、版本、
  bond 扫描进度与判定理由。

测试覆盖：解析与三类符号错误、规则 CUT/BLOCKED/NO_MATCH/端基酶、连续切点
无空片段、漏切 0..3 与独立 oracle 全量比对、相同序列位置保留、拼接还原、
手算成熟质量（含水与质子）、X→UNKNOWN、U→UNKNOWN、B/J→有界歧义、NumPy
批量与标量一致、服务层失败持久化、HTTP 具体结果与各错误码、SQLite 溯源
回读。

---

## 6. 版本

质量表 `mono-residue-v1`（还原态半胱氨酸、无 PTM）、酶目录
`synthetic-enzymes-v1`、应用 `1.0.0`。版本随每次响应与溯源记录返回。
