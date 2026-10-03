# 执行记录（RUN_REPORT）

本机：Linux 6.8.0-90-generic，Python 3.12.3。以下命令与输出均为实际执行所得。

## 1. 依赖版本

```
fastapi==0.141.1  uvicorn==0.54.0  pydantic==2.13.5  numpy==2.4.6
pytest==9.1.1     httpx==0.28.1
```

## 2. 测试结果

命令：

```bash
python3 -m pytest tests/ -q
```

结果：

```
80 passed, 1 warning in 94.95s (0:01:34)
```

唯一 warning 为 starlette TestClient 对 httpx 的弃用提示，与本工程代码无关
（来自测试依赖自身，已在 pytest.ini 中过滤 fastapi/starlette 的弃用告警）。

测试构成（80 个，均断言具体结果或具体失败类别，非"接口能调用"）：

- `test_brute_force_oracle.py`：独立参考答案自身的手算夹具钉测（空串/单碱基、
  环长边界、GU 摆动对、GGAUCC 三最优、3 对嵌套发夹、交叉对判定、点括号解析）。
- `test_nussinov_core.py`：长度 0–8 全部 4^n 序列的 DP 最优值 vs 暴力最优值；
  生产枚举器结构集合 == 暴力最优集合；截断标志；主结构确定性；环长边界；
  点括号/配对表/配对列表三方一致；各类非法结构的具体 violation code。
- `test_parser.py`：大小写、T→U 及转换位置、空白、FASTA 头/注释、
  empty_sequence / invalid_base(含 position,base) / sequence_too_long。
- `test_api.py`：HTTP+SQLite 端到端；长度 5 全部 1024 条序列走完整接口链路；
  请求 ID 关联（自提供/自动生成）、溯源持久化、失败类别与 HTTP 码、
  日志 request_id 关联、不确定性声明。

## 3. 真实服务冒烟

命令：

```bash
NUSSINOV_DB_PATH=data/smoke.db \
  python3 -m uvicorn nussinov_backend.main:app --host 127.0.0.1 --port 8011
```

### 3.1 GET /health → 200

```json
{"status":"ok","algorithm":"nussinov","version":"1.0.0",
 "model_scope":"teaching-combinatorial","db_path":"data/smoke.db"}
```

### 3.2 POST /api/v1/fold  GGGAAACCC（X-Request-ID: smoke-hairpin-001）→ 200

- `optimum = 3`；主结构点括号 `(((...)))`
- 配对（1-based）：(1,9) G-C、(2,8) G-C、(3,7) G-C
- `pair_table = [9, 8, 7, 0, 0, 0, 3, 2, 1]`
- `legal_structure = true`，无 violation；`pseudoknots_supported = false`

### 3.3 POST /api/v1/fold  GGAUCC（枚举全部并列最优，smoke-multi-001）→ 200

`optimum = 1`，`alternatives_truncated = false`，恰好 3 个最优结构，
与独立暴力枚举器结果逐一相同：

| rank | 角色 | 点括号 | pair_table (1-based) | 配对 |
|------|------|--------|----------------------|------|
| 1 | primary | `.(...)` | `[0,6,0,0,0,2]` | (2,6) G-C |
| 2 | alt | `(...).` | `[5,0,0,0,1,0]` | (1,5) G-C |
| 3 | alt | `(....)` | `[6,0,0,0,0,1]` | (1,6) G-C |

处理步骤 8 步全部 `ok`；溯源版本 `nussinov 1.0.0`，
`processing_location` 为本机主机名，`stored=true`。

### 3.4 非法碱基 ACGXU → HTTP 400

```json
{"success": false, "request_id": "req_f540…",
 "error": {"category": "invalid_base",
           "message": "invalid base 'X' at position 3 (1-based 4); allowed alphabet: ACGTU",
           "details": {"position": 3, "base": "X"}}}
```

该失败请求同样写入 SQLite：`status=failed`、`error_category=invalid_base`、
`parse_sequence` 步骤 `outcome=failed`。

### 3.5 FASTA 风格输入 → 200

输入 `">synthetic-fixture-1\nGGGAUCC\n"`：归一化为 `GGGAUCC`（长度 7），
`optimum = 2`，主结构 `((...))`（嵌套配对 (1,7)、(2,6)，内侧恰 3 碱基）。

### 3.6 GET /api/v1/lineage/smoke-hairpin-001 → 200

返回完整溯源：`status=success`、`optimum=3`、
`primary_dot_bracket="(((...)))"`、8 个有序处理步骤（未请求枚举时
`enumerate_alternatives=skipped`）、3 条模型告警。

### 3.7 失败类别其余路径

- 未知溯源 ID：`GET /api/v1/lineage/nope` → HTTP 404
  `error.category=result_not_found`。
- 缺少 sequence 字段：`POST` body `{}` → HTTP 422
  `error.category=request_schema_error`，details 给出 pydantic 错误
  （`loc=["body","sequence"]`）。

### 3.8 结构化日志的请求身份关联（stderr 实测）

```
{"level":"INFO","logger":"nussinov_backend.api.routes",
 "request_id":"smoke-hairpin-001",
 "message":"fold succeeded: length=9 optimum=3 alternatives=0 truncated=False"}
{"level":"WARNING","logger":"nussinov_backend.api.routes",
 "request_id":"req_f540…",
 "message":"fold rejected at parsing: invalid_base: invalid base 'X' at position 3 …"}
```

每条日志带 UTC 时间戳、级别、logger 名、`request_id`，可与响应头
`X-Request-ID` 及 SQLite 溯源行直接关联。

## 4. 结论

- 80 个测试全部通过；长度 0–8 全枚举核验 DP 最优值与最优结构集合，
  长度 5 全部 1024 条序列经真实 HTTP+SQLite 链路核验，均与独立
  暴力参考答案一致。
- 真实服务冒烟覆盖成功、多最优枚举、环长边界、输入失败、schema 失败、
  404、溯源回读、日志关联，结果与文档一致。
- 模型边界在代码（固定常量）、响应（`uncertainties`）、文档三处一致声明：
  仅教学组合模型、不支持假结、不预测真实折叠可靠性。
