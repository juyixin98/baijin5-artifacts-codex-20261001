# coverage-depth

合成比对区间的逐段覆盖深度与质量过滤后端。Python + FastAPI + NumPy + SQLite，
全部输入为本地合成夹具，无外部账号与真实业务数据。

## 坐标与计数语义（固定，不可配置）

- **坐标基制**：全程 0-based 半开区间 `[start, end)`。位置 `p` 被区间
  `[s, e)` 覆盖当且仅当 `s <= p < e`。
- **缺口操作不计覆盖**：CIGAR 的 `D`/`N` 只推进参考游标，不产出覆盖块；
  `10M5N10M` 产生两个块，中间 5 个碱基深度为 0。
- **同读段重叠去重（显式）**：同一 `read_id` 的所有块（一条记录的多个
  CIGAR 块，或一对双端 mates 的两条记录）先按读段做并集合并再计数。
  配对重叠区深度为 1，绝不双计；不同 `read_id` 永远独立计数。
- **边界相接不重叠**：`[0,10)` 与 `[10,20)` 不共享任何碱基，相接处不会
  出现深度 2。
- **守恒不变量**：`sum(depth × length)`（加权长度）== 去重后读段块的总
  碱基数。流水线在持久化前强制校验，不满足则本次运行失败（500
  `ConservationError`），不输出任何结果。

## 模块划分

```
src/coverage_depth/
  parsing.py       合成 TSV 夹具与 CIGAR 子集解析（M/=/X 覆盖，D/N 缺口，I/S/H 不计参考）
  filtering.py     质量过滤：每条记录产出 ACCEPTED / REJECTED / UNDECIDABLE 判定
  dedup.py         半开区间并集合并、按 read_id 分组去重
  sweep.py         NumPy 向量化扫描线分段、深度直方图、总覆盖量
  externalsort.py  超内存输入的外排序：分块排序 → SQLite 溢出 → k 路归并
  pipeline.py      编排：解析→过滤→展开→越界检查→去重→扫描线→守恒校验
  provenance.py    SQLite 溯源库：运行配置、输入摘要、逐条判定、分段与直方图
  diagnostics.py   结构化 JSON 日志；request_id 贯穿；read_id 只记脱敏摘要
  api.py           FastAPI 校验接口（create_app 工厂）
  asgi.py          服务入口（uvicorn coverage_depth.asgi:app）
tests/             独立测试与合成夹具（期望值手工计算，非由被测实现生成）
scripts/demo.py    本地演示脚本
```

## 运行

```bash
pip install -r requirements.txt

# 服务
PYTHONPATH=src uvicorn coverage_depth.asgi:app --port 8000
# 溯源库路径可用 COVERAGE_DB 覆盖（默认 ./coverage_provenance.db）

# 本地演示（不依赖服务）
python scripts/demo.py
python scripts/demo.py --chunk-size 2   # 强制走 SQLite 外排序路径，结果应完全一致
```

## API 与错误语义

记录级问题**不会**让请求失败：它们进入判定审计（decisions），HTTP 语义只
表达请求级失败。

| 状态码 | 含义 |
|--------|------|
| 201 | 运行完成；响应含 `run_id`、直方图、计数器、配置与输入 SHA-256 |
| 400 | 请求级校验失败（如非法的 decisions `status` 过滤值），`error` 字段给出类别 |
| 404 | 未知 `run_id`（`RunNotFound`） |
| 413 | 单请求记录数超过 `MAX_RECORDS_PER_REQUEST` |
| 422 | 请求体不符合 schema（空 alignments、非正参考长度、负坐标等） |
| 500 | 未预期失败或守恒校验失败（`ConservationError`）；响应与日志可用 `X-Request-Id` 关联 |

端点：

- `POST /v1/coverage/runs` — 提交 `{reference, alignments[], filter?, sort_chunk_size?}`
- `GET /v1/coverage/runs/{run_id}` — 汇总 + 分段 + 溯源（配置、输入摘要、计数器）
- `GET /v1/coverage/runs/{run_id}/decisions?status=REJECTED` — 逐条判定审计
- `GET /healthz`

### 记录级判定类别（reason code）

`ACCEPTED`；`REJECTED` 细分：`UNMAPPED` / `SECONDARY` / `SUPPLEMENTARY` /
`QC_FAIL` / `DUPLICATE` / `LOW_MAPQ` / `CIGAR_ERROR` / `EMPTY_INTERVAL` /
`OUT_OF_BOUNDS`；`UNDECIDABLE`：`MAPQ_UNKNOWN`（质量未知而非已知不合格，
不计入覆盖但单列报告）。每条判定带 `record_index`、脱敏的
`read_id_digest`（SHA-256 前 16 位）与解释性 `detail`。

## 诊断与脱敏

日志为结构化 JSON，每行带 `request_id`；记录级日志只出现
`read_id` 的脱敏形式（首字符 + `***` + 摘要前 8 位），溯源库中只存摘要，
不存原始读段名。

## 复现与测试

```bash
python -m pytest          # 69 个测试
```

测试组织（期望值均为手工计算或独立 oracle，非由被测核心生成）：

- `test_parsing.py` — 含缺口 CIGAR、剪辑、畸形 CIGAR/行的失败类别
- `test_filtering.py` — 各 flag、MAPQ 阈值边界、未知 MAPQ 的 UNDECIDABLE
- `test_dedup.py` — 相接/重叠/包含区间合并、配对重叠并集、不同读段不合并
- `test_sweep.py` — 手工计算的分段与直方图、边界相接、加权长度守恒，
  以及 200 组随机输入对照测试内独立编写的逐碱基朴素 oracle
- `test_external_sort.py` — 溢出排序与内置排序等价、单块快路径、临时文件清理
- `test_pipeline.py` — 端到端对照手工期望值、判定审计、强制溢出与内存结果一致
- `test_api.py` — HTTP 语义、溯源读取、判定过滤、404/400/422 类别
