# txmap — 转录本 ↔ 基因组坐标双向映射服务

从合成参考序列与外显子注释出发，提供转录本坐标与基因组坐标之间的双向映射
（点与区间）、拼接序列提取、往返一致性验证与决策溯源。全部数据为本地合成
夹具，无任何外部账号或真实业务数据。

## 坐标约定（固定）

- 所有坐标为 **0 基、半开区间 `[start, end)`**（BED 风格）。
- 基因组坐标：参考叠连群（contig）正链上的偏移。
- 转录本坐标：拼接后外显子序列沿转录本方向（5'→3'）的偏移。
- **负链**：转录本位置 0 对应基因组坐标最大的外显子的最后一个碱基；
  转录本坐标递增 = 基因组坐标递减；拼接序列取基因组序列的反向互补。
- 落在**内含子**的位置不做硬映射：点查询返回 `REJECTED/INTRONIC`，
  区间查询把内含子子段显式列为 `gaps`。
- 跨外显子区间拆分为多个片段，**片段顺序按转录本 5'→3'**（负链时即基因
  组降序），且 `Σ片段长度 == 可映射碱基数`（长度守恒）。
- 转录本身份严格隔离：同一基因组位置在不同转录本下独立判定。

## 模块划分

| 模块 | 职责 |
|---|---|
| `txmap/models.py` | 领域模型：外显子、转录本、叠连群、映射结果、状态/原因枚举 |
| `txmap/reference.py` | 合成序列生成（`ACGT` 周期）与 FASTA 解析/写出 |
| `txmap/intervals.py` | 基于 NumPy 的外显子索引（`searchsorted` 二分定位） |
| `txmap/mapping.py` | 核心双向映射算法（点、区间、拼接序列） |
| `txmap/provenance.py` | 溯源记录构建、内容哈希、敏感字段脱敏 |
| `txmap/diagnostics.py` | 带 request_id 的结构化决策日志 |
| `txmap/store.py` | SQLite：转录本目录 + 溯源日志 |
| `txmap/service.py` | FastAPI 接口层（映射、验证、溯源查询） |
| `txmap/config.py` | 环境变量配置（夹具目录、数据库路径） |
| `txmap/main.py` | 应用装配入口 |

## 状态与原因码

- `OK`：完全映射；`PARTIAL`：区间部分映射（含内含子/界外 gap）；
  `REJECTED`：确定性拒绝；`INDETERMINATE`：参考数据缺失，无法判定。
- 原因码：`INTRONIC`、`OUT_OF_TRANSCRIPT`、`OUT_OF_CONTIG`、
  `UNKNOWN_TRANSCRIPT`、`UNKNOWN_CONTIG`、`INVALID_INTERVAL`、
  `INVALID_POSITION`、`SEQUENCE_UNAVAILABLE`。

每次判定都带 `request_id`（可用 `X-Request-ID` 头指定）写入日志与 SQLite
溯源表，说明接受/拒绝/无法判定的原因；序列等敏感内容只记录长度与哈希。

## 数据夹具

- `fixtures/reference.fa`：`chrSyn1`，120 bp，`base(i) = "ACGT"[i % 4]`，
  因此任何位置的碱基都可手算。
- `fixtures/transcripts.json`：
  - `txA`（+）：外显子 `[10,20) [30,45) [60,70)`，转录本长 35
  - `txB`（−）：外显子 `[15,25) [40,50) [80,100)`，转录本长 40
  - `txC`（+）：外显子 `[12,18) [65,75)`，转录本长 16（与 txA 区域重叠，
    用于身份隔离测试）
- 重新生成：`python3 scripts/make_fixtures.py`（确定性，输出逐字节一致）。

## 复现步骤

```bash
# 1. 安装依赖（锁定版本见 requirements-lock.txt）
pip3 install -r requirements.txt        # 或 requirements-lock.txt

# 2. 运行测试（含覆盖率门禁 80%）
python3 -m pytest

# 3. 启动服务
uvicorn txmap.main:app --port 8000

# 4. 运行示例调用（自动起服务、调用、留存输出到 examples/outputs/）
bash examples/curl_examples.sh
```

## 接口一览

| 方法/路径 | 说明 |
|---|---|
| `GET /health` | 健康检查与已加载转录本 |
| `GET /transcripts` | 转录本目录（外显子、链向、长度） |
| `GET /transcripts/{id}/sequence` | 拼接序列（负链为反向互补） |
| `POST /map/genomic-to-transcript` | 基因组点 → 转录本点 |
| `POST /map/transcript-to-genomic` | 转录本点 → 基因组点 |
| `POST /map/genomic-interval` | 基因组区间 → 片段 + 内含子 gap |
| `POST /map/transcript-interval` | 转录本区间 → 基因组片段 |
| `POST /validate/roundtrip` | 正反向映射往返一致性验证 |
| `GET /provenance/{request_id}` | 按请求标识查询溯源记录 |

示例请求/响应见 `examples/curl_examples.sh` 与 `examples/outputs/*.json`
（真实运行留存）。

## 测试组织与独立参考答案

- `tests/reference_answers.py`：**全部手算**的期望值（含推导注释），不由
  被测实现生成。覆盖：正/负链点映射、相邻外显子边界（半开区间端点）、
  多外显子区间拆分与片段顺序、长度守恒、内含子/界外/不存在位置、身份
  隔离、负链碱基方向（整条拼接序列逐碱基手算）。
- `tests/test_mapping_plus_strand.py` / `test_mapping_minus_strand.py`：
  点映射与链向语义。
- `tests/test_intervals.py`：区间拆分、片段顺序、长度守恒定律。
- `tests/test_roundtrip_and_isolation.py`：全部外显子位置的往返恒等
  （g→t→g 与 t→g→t）、转录本身份隔离、未知转录本、缺参考时
  `INDETERMINATE`。
- `tests/test_api.py`：HTTP 接口的具体响应、request_id 透传、溯源落库。
- `tests/test_fixtures.py`：夹具结构与可再生成性。

最近一次运行结果：`43 passed`，覆盖率 93%（`pytest.ini` 门禁 80%）。
