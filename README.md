# cfim — 闭合频繁项集挖掘后端

在事务集合上挖掘**闭合频繁项集**（closed frequent itemsets）的后端服务。
技术栈：Python 3.12 · FastAPI · SQLite，零外部账号、零网络依赖，全部数据
来自本地合成夹具。

## 语义定义（验收规则对应）

1. **同事务去重、重复事务保留身份**
   事务 `["a","a","b"]` 规范化为集合 `{"a","b"}`，重复出现只计一次；
   两条完全相同的事务得到两个不同 tid，各自独立贡献支持度。空事务合法，
   保留为一行并计入事务总数（影响阈值边界），但不支持任何项集。
2. **最小支持是整数阈值**
   `min_support` 表示“包含该项集的**事务条数**”的绝对整数，取值
   `1 <= min_support <= transaction_count`；不接受分数、0、布尔值。
3. **闭合 ≠ 极大**
   - 闭合：不存在**同支持度**的严格超集（即 `cl(I)=I`，
     `cl(I)={j : T(j)⊇T(I)}`）；
   - 极大：不存在任何**频繁**严格超集（更强的条件）。
   结果中 `maximal` 字段单独标注；未跑完时所有 `maximal=false`，
   因为极大性只能对照完整闭合结果判定。
4. **枚举预算到期返回部分结果且可续跑**
   预算单位是一次 DFS 节点访问（一次闭包判定）。预算耗尽时任务停在
   `RUNNING`，已返回的结果都是有效闭合项集；对同一 job 调
   `/advance` 从序列化的续跑栈继续，既不重复也不遗漏任何节点，
   最终结果与一次性跑完**逐项一致**（由测试断言）。

不挖掘空项集（标准约定）。

## 模块划分

| 文件 | 职责 |
|---|---|
| `cfim/config.py` | 独立环境配置（路径、预算上限、规模上限、内核版本） |
| `cfim/errors.py` | 传输无关的错误码枚举与异常类型 |
| `cfim/corpus.py` | 语料规范化与校验（规则 1、2） |
| `cfim/kernel.py` | 挖掘内核：垂直 tid 集合 + 前缀 DFS + 闭包判定，预算化、可序列化续跑 |
| `cfim/store.py` | SQLite 持久化、垂直索引、**独立于内核**的 SQL 支持度/闭包查询 |
| `cfim/models.py` | Pydantic 请求/响应模型 |
| `cfim/observability.py` | 请求 ID 关联与单行 JSON 结构化日志 |
| `cfim/service.py` | FastAPI 路由、错误信封、预算切片编排 |
| `run.py` | 可运行服务入口（uvicorn） |
| `scripts/demo.py` | 本地演示脚本（不经网络，直跑全流程） |
| `tests/oracle.py` | **独立参考实现**：frozenset 朴素计数 + itertools 全子集枚举，与内核零共享代码 |

## HTTP 接口

| 方法 路径 | 作用 |
|---|---|
| `POST /corpora` | 上传统一语料 `{name, transactions}` |
| `GET /corpora` / `GET /corpora/{id}` | 列出/查看（含空事务数、去重命中数） |
| `POST /corpora/{id}/jobs` | 创建挖掘任务，body `{min_support, budget?}`，立即跑第一片 |
| `GET /jobs/{id}` | 任务快照（状态、计数、已得闭合项集） |
| `POST /jobs/{id}/advance` | 追加预算继续，body `{budget?}` |
| `POST /corpora/{id}/query` | **独立验证**任意项集的支持度、闭包、是否闭合 |
| `GET /health` | 存活、服务版本、内核版本、预算单位 |

每个响应都带 `X-Request-ID`（可由请求头传入，否则自动生成）。
交互文档：服务启动后 `http://127.0.0.1:8000/docs`。

### 错误语义

所有失败共用信封：

```json
{"success": false,
 "error": {"code": "MIN_SUPPORT_INVALID", "message": "...", "details": {...}},
 "request_id": "…"}
```

| code | HTTP | 触发场景 |
|---|---|---|
| `VALIDATION_ERROR` | 400/422 | 名称空白、结构不是事务列表、schema 不合法、语料名重复 |
| `INVALID_TRANSACTION` | 400 | 某条事务不是列表、超过单事务条目上限（details 带事务下标） |
| `INVALID_ITEM` | 400 | 非字符串条目、空白条目、超长条目 |
| `CORPUS_TOO_LARGE` | 400 | 事务条数超过配置上限 |
| `MIN_SUPPORT_INVALID` | 400 | 非正整数、超过事务总数 |
| `BUDGET_INVALID` | 400 | 非正整数或超过单次预算上限 |
| `ITEM_NOT_IN_DOMAIN` | 400 | 查询引用了语料中不存在的条目 |
| `CORPUS_NOT_FOUND` / `JOB_NOT_FOUND` | 404 | id 不存在 |
| `STATE_VERSION_MISMATCH` | 400 | 序列化状态/内核版本不兼容 |

不确定结论单列：任务未完成时 `partial=true` 且 `maximal` 全部为
false；日志中这类情况出现在 `uncertainty` 字段，失败原因出现在独立的
`failure` 字段，二者不与正常进度混写。

## 可解释性（日志）

日志为单行 JSON，包含 `request_id`、`step`（如 `corpus.normalized`、
`job.slice_start`、`job.slice_end`、`query.evaluated`）、`location`
（模块:函数）、`version`（内核版本）、`context`（关键计数），失败另有
`failure.code/details`。响应头与日志通过同一 request id 关联。

## 复现步骤

```bash
# 1) 依赖（环境已具备则可跳过安装）
python3 -m pip install -r requirements.txt

# 2) 执行全部测试并报告覆盖率（要求 >= 80%；当前 95%）
python3 -m pytest --cov=cfim --cov-report=term-missing

# 3) 本地演示脚本：规范化 -> 小预算分片续跑 -> 闭合/极大 -> 独立验证 -> 错误类别
python3 scripts/demo.py

# 4) 启动服务
python3 run.py            # 默认 127.0.0.1:8000，SQLite 于 data/cfim.db

# 5) 调用示例
curl -s -X POST localhost:8000/corpora -H 'Content-Type: application/json' \
  -d '{"name":"demo","transactions":[["a","b"],["a","b","c"],["c"],[]]}'
# 取返回的 corpus_id 后：
curl -s -X POST localhost:8000/corpora/<CID>/jobs \
  -H 'Content-Type: application/json' -d '{"min_support":2,"budget":2}'
curl -s -X POST localhost:8000/jobs/<JOB_ID>/advance \
  -H 'Content-Type: application/json' -d '{"budget":100}'
curl -s -X POST localhost:8000/corpora/<CID>/query \
  -H 'Content-Type: application/json' -d '{"items":["a"],"min_support":2}'
```

## 配置（环境变量，均有默认值）

| 变量 | 默认 | 含义 |
|---|---|---|
| `CFIM_DB_PATH` | `data/cfim.db` | SQLite 文件路径 |
| `CFIM_HOST` / `CFIM_PORT` | `127.0.0.1` / `8000` | 监听地址 |
| `CFIM_LOG_LEVEL` | `INFO` | 日志级别 |
| `CFIM_DEFAULT_BUDGET` | `1000` | 未显式给预算时的单片节点数 |
| `CFIM_MAX_ADVANCE_BUDGET` | `100000` | 单次请求预算上限（强制分片） |
| `CFIM_MAX_TRANSACTIONS` | `10000` | 单语料事务条数上限 |
| `CFIM_MAX_ITEMS_PER_TX` | `256` | 单事务条目数上限 |
| `CFIM_MAX_ITEM_LENGTH` | `128` | 条目字符串最大长度 |
| `CFIM_MAX_NAME_LENGTH` | `200` | 语料名最大长度 |

## 测试如何保证正确性

- 3 件域上**穷举所有可能语料**（8 种事务取可重复排列，长度 1–4）×多个
  阈值，内核输出必须与独立 oracle 的闭合集完全相等，并断言无重复枚举；
- 40 个确定性合成语料（LCG 生成，含空事务/重复行）对拍 oracle；
- 同支持包含集链（`{a}`→`{a,b,x}`）逐集验证“非闭合必有同支持超集”；
- 预算按 1/2/3/7/1000 切片续跑，结果与一次性运行逐项一致；
- 跨进程重启后从 SQLite 恢复任务并续跑完成；
- 独立 SQL 索引对每个频繁项集重算支持度；API 测试断言具体载荷和
  具体错误码，而非“接口能调用”。
