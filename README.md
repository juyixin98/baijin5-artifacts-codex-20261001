# Nussinov 最大配对数后端

合成短 RNA **最大非交叉碱基配对数**（Nussinov 模型）的多模块后端工程。

> **模型边界（重要）**：本服务是**教学组合模型**，只最大化规范碱基对
> （Watson–Crick `G-C`/`A-U` 加摆动对 `G-U`）的**非交叉配对数量**。
> 它**不**计算折叠自由能，也**不**预测真实 RNA 折叠的可靠性。
> **不支持假结**（任何交叉配对都会被拒绝并在响应中单列说明）。
> 允许碱基对集合与最短环长（`MIN_LOOP_LENGTH = 3`，即配对两端之间至少
> 3 个未配对碱基）是**固定**模型参数，不接受调用方修改。

## 1. 模块结构（各模块承担实际工作，无硬编码演示）

```
nussinov_backend/
├── config.py                 # 环境变量配置（均有本地默认值）
├── errors.py                 # 带 category 的类型化失败分类
├── logging_setup.py          # 关联 request_id 的结构化 JSON 日志
├── parser/                   # 合成序列解析与输入校验（纯文本/FASTA/T→U）
│   └── sequence.py
├── domain/                   # 领域算法
│   ├── rules.py              # 固定配对规则、最短环长、假结边界
│   ├── nussinov.py           # NumPy 向量化 DP 填表
│   ├── structures.py         # 确定性回溯 / 受限枚举 / 合法性检查 / 点括号渲染
│   ├── verification.py       # 独立纯 Python 标量递推（服务内自检）
│   └── service.py            # 编排：填表→自检→回溯→校验
├── trace/                    # 结果溯源（SQLite：请求、步骤、结构、告警）
│   ├── db.py
│   └── lineage.py
├── api/                      # FastAPI 验证接口
│   ├── app.py                # 应用工厂、请求ID中间件、异常分类
│   ├── routes.py             # /health /api/v1/fold /api/v1/lineage/{id}
│   └── schemas.py
└── main.py                   # uvicorn 入口
tests/
├── reference/brute_force.py  # 独立暴力枚举器（参考答案，不使用被测 DP）
├── test_brute_force_oracle.py
├── test_nussinov_core.py     # 短串全枚举核验、环长边界、多最优、嵌套合法
├── test_parser.py
└── test_api.py               # HTTP/SQLite 端到端，断言具体结果与失败类别
examples/                     # 请求样例与 curl 脚本
data/                         # SQLite 数据库目录（运行时生成，已 gitignore）
```

## 2. 环境与依赖版本

- Python **3.12**（在 3.12.3 上验证）
- 固定版本见 `requirements.txt`：

| 包       | 版本     |
|----------|----------|
| fastapi  | 0.141.1  |
| uvicorn  | 0.54.0   |
| pydantic | 2.13.5   |
| numpy    | 2.4.6    |
| pytest   | 9.1.1    |
| httpx    | 0.28.1   |

SQLite 使用 Python 标准库 `sqlite3`，无需额外服务。全部数据为本地
合成夹具，不需要任何生产账号或外部业务数据。

## 3. 从干净目录复现

```bash
# 1) （可选）虚拟环境
python3 -m venv .venv && source .venv/bin/activate

# 2) 安装固定依赖
pip install -r requirements.txt

# 3) 运行全部测试
python3 -m pytest tests/ -q

# 4) 启动服务（默认监听 127.0.0.1:8000）
uvicorn nussinov_backend.main:app --host 127.0.0.1 --port 8000

# 5) 另开终端，运行示例
./examples/curl_examples.sh
```

### 配置项（环境变量，均有默认值）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `NUSSINOV_DB_PATH` | `data/nussinov.db` | SQLite 文件路径 |
| `NUSSINOV_MAX_SEQUENCE_LENGTH` | `200` | 允许的最大序列长度 |
| `NUSSINOV_MAX_STRUCTURES_LIMIT` | `100` | 单次最多返回的最优结构数 |
| `NUSSINOV_LOG_LEVEL` | `INFO` | `DEBUG/INFO/WARNING/ERROR` |

## 4. 接口

### `GET /health`
返回算法名、版本、`model_scope=teaching-combinatorial`、数据库路径。

### `POST /api/v1/fold`
请求体：

```json
{
  "sequence": "GGAUCC",
  "enumerate_alternatives": true,
  "alternatives_limit": 50
}
```

- `sequence`：纯字符串或 FASTA 风格文本；接受 `ACGU`，DNA 的 `T` 会
  转换为 `U` 并记录转换位置；空白与换行被移除。
- 可通过请求头 `X-Request-ID` 提供请求身份（否则自动生成），响应头与
  所有日志、溯源行都带同一 ID。

响应包含：
- `optimum`：最大配对数；`structure.dot_bracket` 点括号；
  `structure.pairs` 配对表（1-based，含两端碱基）；`pair_table[i]`
  为 1-based 位置 i 的配对位置（0 表示未配对）。**三种表示同源派生，
  保证一致**。
- `alternatives`：与最优值并列的其它结构（受限枚举，超限时
  `alternatives_truncated=true`）。
- `legal_structure` / `legality_violations`：嵌套合法性独立校验结果。
- `processing_steps`：解析 → 参数校验 → DP 填表 → **独立最优值核验** →
  回溯 → 合法性检查 → 枚举 → 落库，每步带顺序、结果、细节、时间戳。
- `provenance`：请求 ID、算法名/版本、`model_scope`、处理位置（主机名）、
  起止时间、耗时、是否已持久化。
- `uncertainties`：**不确定性单列**——教学模型声明、假结不支持声明、
  多最优时主结构的确定性选择规则；明确不做真实折叠可靠性预测。

**确定性回溯规则**（多个结构并列最优时稳定主结构）：只要把位置 `i`
留空仍能保持最优就留空；否则在所有保持最优的可配对 `k` 中取最左者。
枚举顺序与之一致，故枚举结果的第一个恒等于主结构。

### `GET /api/v1/lineage/{request_id}`
从 SQLite 重建该请求的完整溯源记录（状态、参数、逐步处理、全部结构、
失败类别与消息、版本、处理位置、模型告警）。

### 失败类别（HTTP 4xx body 的 `error.category`）

| category | 含义 | HTTP |
|----------|------|------|
| `empty_sequence` | 归一化后序列为空 | 400 |
| `invalid_base` | 非法碱基（details 带 0-based position 和 base） | 400 |
| `sequence_too_long` | 超长度上限 | 400 |
| `invalid_parameter` | 参数超界（如 alternatives_limit） | 400 |
| `request_schema_error` | 请求体不符合 schema | 422 |
| `result_not_found` | 溯源 ID 不存在 | 404 |
| `traceback_error` / `structure_error` | 回溯值≠DP 最优 / 结构非法（内部完整性门，正常不出现） | 422 |

## 5. 验收点如何被满足

- **短串枚举所有合法非交叉配对核验**：`tests/reference/brute_force.py`
  是独立递归暴力枚举器，按区间分裂、记忆化只用于该独立实现内部；它
  **不导入被测 DP/回溯**，只共享"问题规格"（配对表与环长常量）。
  `test_nussinov_core.py` 对长度 0–8 的全部 `4^n` 个序列断言：
  NumPy DP 最优值 == 暴力最优值；生产枚举器返回的结构集合 == 暴力枚举
  的最优结构集合（不多不少）；每个输出经独立 `is_nested_and_legal`
  （规范配对/环长/无交叉/碱基不重复）校验。
- **最短环边界**：`GAAAC`（恰含 3 个内侧碱基）可配对；`GAAC`（2 个）
  不可配对；长度 ≤ 4 的所有序列最优值必为 0。
- **无配对**：`AAAAAAAA` 等返回唯一零配对结构、全点号、全零表。
- **多个最优结构**：`GGAUCC` 手算恰有 3 个 1 对最优结构，测试逐一断言；
  主结构跨 20 次重复稳定；截断标志按上限正确翻转。
- **参考答案不由被测核心生成**：参考答案是独立算法 + 手工夹具
  （见 `test_brute_force_oracle.py` 对参考答案自身行为的钉测）。
- **点括号与配对表一致**：用独立栈式解析器把点括号解析回配对集合，
  与配对列表、1-based 配对表三方互查。
- **可解释**：请求 ID 关联响应/SQLite/JSON 日志；步骤、版本、处理位置
  齐备；失败原因与不确定性分别单列。
- **服务内自检**：每次折叠用另一份纯 Python 标量递推复算最优值，
  不一致直接抛 `traceback_error`，回溯结果若配对数≠最优值或非法同样拒绝。

## 6. 执行记录（本机如实运行结果）

见 [RUN_REPORT.md](RUN_REPORT.md)。
