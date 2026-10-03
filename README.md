# minimizer-seed-index

合成读段的 minimizer 种子索引与候选位置查询。技术栈：Python 3.12、FastAPI、
NumPy、SQLite。所有输入均为本地合成夹具，不依赖任何外部账号或真实业务数据。

## 算法约定（固定行为）

1. **k-mer 与窗口**：2-bit 编码 A=0/C=1/G=2/T=3，最左碱基为高位。窗口 = `window`
   个连续 k-mer，只发射完整窗口；序列长度 `k + window - 1` 恰好产生 1 个窗口，
   更短则没有窗口（不伪造末尾短窗）。
2. **哈希**：命名哈希固定可选 `mix64`（splitmix64 风格终结器，带固定种子，
   跨运行/平台确定）与 `identity`（恒等，供手算核验的测试使用）。
3. **同值平局**：窗口内哈希相同取**最右**位置；回文 k-mer（正向==反向互补）
   链方向记为 `+`。
4. **正反互补规范化保留链方向**：哈希作用于 `min(forward, revcomp)` 的规范整
   数，但每个种子同时记录链方向 `+`/`-`（正向 <= 反向互补为 `+`）。规范化只
   决定哈希归属，不抹掉方向。
5. **去重范围**：仅对**同一条序列内连续窗口**解析到同一位置的 minimizer 去重
   （保留首次）；不跨序列去重，非连续重复全部保留。
6. **低复杂度爆发上限**：建库时统计每个哈希在全库的出现次数，超过
   `max_hash_occurrences` 的哈希整体删除并计入 `stats.filtered_*`。
7. **候选命中不是完整比对结论**：查询结果只表示共享种子聚成的候选位置簇，
   所有响应带 `conclusion: "candidate_only"`。

## 候选语义

- 同向锚点（查询与库中种子链方向相同）按 `ref_pos - qpos` 聚簇；
- 换向锚点（读段为反向互补）按 `ref_pos + qpos` 聚簇；
- `estimated_ref_start`：同向 = offset；换向 = `offset + k - read_len`。
- 候选按命中数降序返回，仅供下游比对召回使用。

## 目录结构

```
app/
  config.py      配置层：k/窗口/哈希/上限的校验（不兼容参数 = 定义好的失败类别）
  sequence.py    合成序列解析：FASTA 夹具、ACGT 校验（报告出错位置）
  minimizer.py   领域算法：规范 k-mer、窗口 minimizer、去重（NumPy 向量化）
  index.py       SQLite 种子索引：建库、上限过滤、候选聚簇查询
  provenance.py  结果溯源：run_id、版本、输入指纹、JSONL 步骤日志
  service.py     FastAPI 验证接口
scripts/demo.py  本地演示：植入已知位置读段并判定召回
tests/           独立测试 + tests/reference.py（独立标量参考实现）
```

## 复现步骤

```bash
pip install -r requirements.txt

# 1. 测试（实际执行并报告结果；日志写入 logs/test_run_<run_id>.jsonl）
python3 -m pytest tests/ -q

# 2. 本地演示（退出码 0 仅当全部判定通过；日志 logs/demo_<run_id>.jsonl）
python3 scripts/demo.py

# 3. 服务入口
uvicorn app.service:app --port 8000
curl http://127.0.0.1:8000/health
```

### API 速览

```
GET  /health
GET  /meta                         # 版本与默认配置
POST /indexes                      # {"sequences":[{name,sequence}], "config":{k,window,...}} -> 201
GET  /indexes/{id}                 # 建库溯源与统计；未知 id -> 404
POST /indexes/{id}/queries         # {"name","sequence"} -> 候选列表
```

## 错误语义

异常或未知状态**不会**被统一返回为成功。错误响应形如
`{"error": {"category", "message", "detail"}}`：

| HTTP | category | 含义 |
|------|----------|------|
| 400 | `INVALID_PARAMETER` | k/窗口/哈希/上限不兼容（如 k<2、k>31、未知哈希名） |
| 400 | `INVALID_SEQUENCE` | 非 ACGT 字符（报告位置）、空序列、FASTA 格式错误 |
| 400 | `SEQUENCE_TOO_SHORT` | 读段长度 < k + window - 1，不存在完整窗口 |
| 404 | `INDEX_NOT_FOUND` | 未知的 index id |
| 500 | `INDEX_STATE_ERROR` | 索引文件损坏或缺元数据 |
| 422 | （FastAPI schema） | 请求体缺字段/类型错误 |

## 测试与溯源

- **参考答案独立性**：小用例的期望值是手算字面值（identity 哈希下可直接推
  导）；随机短序列用 `tests/reference.py` 的独立标量实现做全窗口枚举对照——
  两者都不由被测核心（`app/minimizer.py`）生成。
- **覆盖的行为**：同值长串（homopolymer）、末尾短窗（恰好/差 1 的边界）、反
  向互补读段召回、参数不兼容失败类别、种子位置与候选召回、去重范围、低复杂
  度上限、损坏索引文件。
- **日志关联**：每次测试/演示/服务运行生成 `run_id`，JSONL 记录版本
  （python/numpy/sqlite/fastapi）、逐步计算步骤（种子数、库命中数、聚簇数）
  与判定依据（`judgment_basis`），失败按实际 outcome 记录，不改写为成功。
- 覆盖率：`python3 -m pytest tests/ --cov=app`（当前约 98%）。
