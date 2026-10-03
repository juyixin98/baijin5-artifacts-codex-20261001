# MSA Backend — 合成多序列比对的加权位点熵 / 信息量 / 共识后端

从合成 FASTA 比对出发，计算**按近重复去放大加权**的逐位点残基分布、Shannon 熵、
信息量（information content）与共识序列，把每次运行的输入哈希、配置快照、组件版本、
逐列结果与坐标映射全部落入 SQLite 溯源库，并通过 FastAPI 提供验证接口。
所有数据均为本地合成夹具（`data/fixtures/`），无任何外部账号或真实业务数据。

## 快速开始

```bash
# 1. 安装（Python 3.11+；依赖：fastapi / uvicorn / numpy / pydantic / PyYAML / pytest / httpx）
pip install -e ".[test]"
#   或不安装直接运行：export PYTHONPATH=src

# 2. 跑测试（单元 + 集成，共 61 个）
python3 -m pytest

# 3. 启动验证 API（默认 127.0.0.1:8000，溯源库 data/provenance.sqlite3）
./scripts/run_server.sh
#   等价于：PYTHONPATH=src python3 -m uvicorn msa_backend.api.app:app --host 127.0.0.1 --port 8000

# 4. 提交一次运行并查询
curl -s -X POST http://127.0.0.1:8000/v1/runs \
  -H 'Content-Type: application/json' \
  -d "$(python3 -c "import json,pathlib; print(json.dumps({'fasta': pathlib.Path('data/fixtures/gappy.fa').read_text(), 'label': 'demo'}))")"
curl -s http://127.0.0.1:8000/v1/runs            # 运行列表（含失败记录）
curl -s http://127.0.0.1:8000/v1/runs/<run_id>   # 详情：列结果 + 权重 + 坐标映射
```

配置在 `config/settings.yaml`（可用 `MSA_BACKEND_CONFIG` 覆盖路径），每次运行的
配置快照随结果入库，保证可复现。

## 算法契约（固定策略，写入每次运行的溯源记录）

| 主题 | 策略 |
|---|---|
| 近重复加权 | 双序列在**双方均非缺口**的位点上计算同一性；≥ `identity_threshold`(0.95) 聚为一簇；每簇总权重 1.0 均分给成员。**权重总量 = 簇数**，复制多少份都不放大证据 |
| 权重总量与缺口处理 | 相互独立：权重由整条序列一次性确定；列分布只按**该列非缺口权重**归一化 |
| 模糊字符 | 固定 `uniform_split`：IUPAC 简并符号把权重**均匀分摊**到其代表的具体碱基（N→各 1/4，R→A/G 各 1/2） |
| 缺口 | 不进入分布；以 `effective_coverage`（非缺口权重和）与 `gap_fraction` 单独报告 |
| 有效覆盖不足 | `effective_coverage < min_effective_coverage`(2.0) 的列一律判为 `insufficient_coverage`、共识 `?`——**即使分布 100% 偏斜也不得出强保守结论** |
| 熵 / 信息量 | H = −Σ p·log2(p)（比特）；IC = log2(4) − H，DNA 上限 2 比特 |
| 共识 | 覆盖达标且最高频 ≥ 0.6 → `conserved`（大写）；覆盖达标但不占优 → `variable`（小写）；否则 `insufficient_coverage`（`?`） |
| 坐标映射 | 每条序列输出「比对列 → 原始未加缺口坐标（1 起始）」，缺口列为 `null`，插入列不丢失原始位置 |

## 目录结构

```
config/settings.yaml        # 运行配置（算法契约参数）
data/fixtures/*.fa          # 合成夹具：全保守 / 高多样 / 缺口密集 / 重复序列 / 模糊字符
src/msa_backend/
  parsing/fasta.py          # 严格 FASTA 比对解析（类型化错误）
  domain/                   # weights / columns / entropy / consensus / coordinates
  provenance/db.py          # SQLite 溯源库（runs / columns / coordinate_maps / sequence_weights）
  pipeline.py               # parse → weight → 列统计 → 共识 → 落库，全程带 run id 日志
  api/app.py                # FastAPI 验证接口
tests/
  unit/  integration/       # 单元与集成测试
  reference/                # 静态手算期望 JSON + 独立 stdlib 参考实现
scripts/run_server.sh       # 启动脚本
```

## 验证设计（如何证明算得对）

- **参考答案独立生成**：`tests/reference/expected_*.json` 是静态手算期望值；
  `tests/reference/independent_calc.py` 是**不导入被测包**的纯 stdlib 参考实现。
  核心实现必须同时与两者一致（容差 1e-9），而非「自己跟自己比」。
- **夹具与手算要点**：
  - `conserved.fa`：1–10 列全保守 → H=0、IC=2 bits、`conserved`；
  - `diverse.fa`：每列均匀 A/C/G/T → H=2 bits、IC=0、`variable`；
  - `gappy.fa`：3–4 列非缺口权重仅 1.0（< 2.0）→ 即使残基 100% 一致也判
    `insufficient_coverage`；第 8 列三种碱基各 1/3 → H=log2(3)≈1.584963；
  - `duplicates.fa`（3 份相同拷贝 + 1 变异）第 8 列加权后 H=1.0，
    **与 `duplicates_pair.fa`（1+1）完全相等**，而朴素未加权答案 ≈0.811 bits——
    测试显式断言复制近重复不按条数放大证据；
  - `ambiguous.fa`：N/R 按 uniform_split 分摊，第 2 列分布 {A 1/4, C 5/12, G 1/4, T 1/12}。
- **失败类别断言**：解析错误、非矩形比对、非法字符、未知 run id 分别断言
  `fasta_parse_error` / `alignment_shape_error` / `invalid_residue_error` / `run_not_found`
  及对应 HTTP 状态（422/404）；异常路径在溯源库留下 `status=failed` + 错误类别，
  不会统一返回成功。
- **日志可关联**：每次运行/每个测试都带 run id 或夹具名，输出版本、计算步骤
  （parse → weights → 每列统计 → persist）与判定依据（`judgement ... basis=...`）。

## 真实测试结论（本仓库最近一次运行）

```
$ python3 -m pytest          # 61 passed in ~1s
$ python3 -m pytest --cov=src/msa_backend --cov-report=term-missing
TOTAL  500 stmts  12 miss  98%   # 远高于 80% 门槛
```

冒烟（真实服务，端口 8077）：`POST /v1/runs` 提交 `gappy.fa` 返回第 3 列
`{"effective_coverage": 1.0, "gap_fraction": 0.6667, "consensus": "?", "status":
"insufficient_coverage"}`；未知 run 返回 404 `run_not_found`；非法输入返回 422
`fasta_parse_error`。

## API 一览

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/health` | 存活 + 组件版本 |
| GET | `/v1/runs` | 运行列表（含 failed 记录） |
| POST | `/v1/runs` | 提交 FASTA 文本执行运行 → 201 RunDetail |
| GET | `/v1/runs/{run_id}` | 运行详情（列结果、权重、坐标映射） |
| GET | `/v1/runs/{run_id}/columns` | 仅逐列结果 |

错误统一为 `{"error": {"category": ..., "message": ...}}`；未知异常为 500
`internal_error`，绝不包装成成功。
