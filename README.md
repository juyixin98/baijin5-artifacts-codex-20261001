# 等位序列单倍型拼接服务（haplotype-phasing-service）

基于合成变异位点与读段支持的**二倍体单倍型推断**后端。技术栈：Python 3.12、FastAPI、NumPy、SQLite（标准库 `sqlite3`）。所有输入均为本地合成夹具，不依赖任何生产账号或真实业务数据。

## 核心机制

1. **解析与校验**（`app/parsing.py`）：把变异位点与读段等位观测解析为领域对象。未知等位（既不等于 ref 也不等于 alt）不臆造参考等位——从 MEC 目标中剔除并计入证据。
2. **连通块划分**（`app/phasing/blocks.py`）：读段同时覆盖两个位点则在位点图上连边，连通分量即相位块。块间相位关系**不做任何推断**。
3. **精确 MEC 求解**（`app/phasing/mec.py`）：对每个块枚举全部 `2^(n-1)` 个候选单倍型（相位整体翻转等价，固定 `h1[0]=0` 取规范代表），以加权最小错误修正（Minimum Error Correction）为目标求解；读段质量转代价规则固定为**代价 == Phred 质量值**。并列最优全部报告为不确定结论。
4. **结果溯源**（`app/provenance.py`）：每次请求（含失败）连同请求 ID、输入 SHA-256、版本与完整结果写入 SQLite。
5. **验证接口**（`app/api/routes.py`）：`POST /v1/phase` 执行推断，`GET /v1/runs/{request_id}` 回溯任意一次运行。

## 快速开始

```bash
pip install -r requirements.txt

# 运行测试（42 个，含手工计算的参考答案与独立枚举参考）
python3 -m pytest -q

# 启动服务（配置见 config/default.yaml，可用 HAPLO_CONFIG / HAPLO_DB_PATH 覆盖）
python3 -m uvicorn app.main:app --port 8000
```

## 示例调用

```bash
# 干净的双位点定相
curl -s -X POST http://127.0.0.1:8000/v1/phase \
  -H 'Content-Type: application/json' \
  -d @fixtures/clean_two_site.json | python3 -m json.tool

# 含一条错误读段（MEC 以代价 10 修正）
curl -s -X POST http://127.0.0.1:8000/v1/phase \
  -H 'Content-Type: application/json' -d @fixtures/error_read.json

# 证据对称 -> 多解，状态 AMBIGUOUS
curl -s -X POST http://127.0.0.1:8000/v1/phase \
  -H 'Content-Type: application/json' -d @fixtures/ambiguous.json

# 不连通块：两块分别定相，不编造跨块相位
curl -s -X POST http://127.0.0.1:8000/v1/phase \
  -H 'Content-Type: application/json' -d @fixtures/disconnected.json

# 回溯任意一次运行（含失败）
curl -s http://127.0.0.1:8000/v1/runs/<request_id>
```

响应要点：

- `status`: `OK` / `AMBIGUOUS` / `FAILED`；失败时 `failure.category` 给出稳定类别（`INVALID_INPUT`、`NO_OBSERVATIONS`、`BLOCK_TOO_LARGE`）。
- `uncertainties`: 不确定结论单列（多解、片段归属平局、未知等位、无覆盖位点）。
- `blocks[].evidence`: 每个位点的 ref/alt 支持权重与被修正观测（冲突证据）。
- `versions`: 应用版本、算法版本、生效配置，随结果与日志一同输出。

## 项目结构

```
app/
  parsing.py            合成序列/读段解析与校验（固定规则见 docs/usage.md）
  phasing/blocks.py     位点连通块划分
  phasing/matrix.py     片段×位点支持矩阵
  phasing/mec.py        精确加权 MEC 求解（NumPy 向量化枚举）
  phasing/pipeline.py   流水线编排与证据装配
  provenance.py         SQLite 溯源存储
  api/routes.py         验证接口
  config.py             配置加载
tests/                  独立测试（含手工参考答案与枚举参考生成器）
fixtures/               合成输入夹具
config/default.yaml     默认配置
docs/usage.md           规则、接口与失败类别详细文档
```

## 已知限制

- 精确枚举复杂度为每块 `2^(n-1)`，默认上限 16 个位点/块（`max_enum_sites` 可调）；超限以 `BLOCK_TOO_LARGE` 失败而非启发式近似。
- 仅支持二等位 SNP 式位点；未知等位被剔除并计数，不参与目标函数。
- 块间相位不推断（无配对端跨块链接、无统计定相模型）。
- SQLite 为单写者本地存储，未做并发写优化。
