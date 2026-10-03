# txmap — 转录本 ↔ 参考基因组坐标双向映射服务

合成夹具上的最小完整实现：**0-based、半开区间固定**；正确处理正/负链、多外显子
拆分、相邻边界与内含子拒绝。技术栈：Python 3.10+ · FastAPI · NumPy · SQLite。

## 目录结构

```
src/txmap/          领域核心 + HTTP 服务（见 docs/DESIGN.md 的职责表）
data/fixtures/      合成转录本夹具 transcripts.json（手算可验）
tests/              60 个独立测试（unit / integration / api 三类）
scripts/            CLI 客户端 + 真实服务冒烟脚本
examples/           库调用示例
docs/DESIGN.md      坐标制、算法推导、失败类别
requirements.txt    直接依赖（钉版本）
requirements.lock   已验证环境的完整传递依赖锁（118 条）
```

## 快速开始

```bash
pip install -r requirements.txt          # 或 pip install -r requirements.lock
export PYTHONPATH=src

# 1) 跑全部测试（覆盖率 99%）
python -m pytest tests/ -q --cov=txmap --cov-report=term-missing

# 2) 库用法示例
python examples/mapping_examples.py

# 3) 命令行直接映射（无需起服务）
python scripts/txmap_cli.py list
python scripts/txmap_cli.py t2g T1_PLUS --interval 25 55
python scripts/txmap_cli.py g2t T2_MINUS --point 639
python scripts/txmap_cli.py g2t T1_PLUS --point 145      # 内含子 → 错误类别

# 4) 起 HTTP 服务
uvicorn txmap.api.app:app --host 127.0.0.1 --port 8137
#    另一个终端执行真实请求冒烟（正常+异常，含审计查询），输出可重定向留存：
bash scripts/smoke.sh | tee docs/run-output.txt
```

环境变量：`TXMAP_FIXTURE`（夹具路径）、`TXMAP_DB`（SQLite 路径）、
`TXMAP_LOG_LEVEL`、`TXMAP_DB_ECHO=1`。

## 接口

| 方法/路径 | 说明 |
|---|---|
| `GET /health` | 健康检查，回 `x-request-id` |
| `GET /transcripts` · `GET /transcripts/{id}` | 转录本元数据 |
| `POST /map/tx-to-genomic/point` | 转录本点 → 基因组点 |
| `POST /map/genomic-to-tx/point` | 基因组点 → 转录本点（内含子 422） |
| `POST /map/tx-to-genomic/interval` | 转录本区间 → 有序片段（可跨多外显子） |
| `POST /map/genomic-to-tx/interval` | 基因组区间 → 转录本区间（覆盖内含子则 422） |
| `GET /audit/{request_id}` | 按请求标识取接受/拒绝审计记录 |

请求体示例：`{"transcript_id":"T1_PLUS","start":25,"end":55,"request_id":"r1"}`。
点请求用 `position`。错误响应统一为
`{"error":{"code","detail","key_state"},"request_id"}`。

## 夹具（全部坐标可手算）

* `T1_PLUS`（syn1, `+`）：外显子 `[100,130) [160,180) [210,240)`，成熟长 80；
  内含子 `[130,160)`、`[180,210)`。
* `T2_MINUS`（syn2, `-`）：外显子 `[500,530) [560,585) [620,640)`，成熟长 75；
  转录本顺序为基因组降序，`tx0 ↔ g639`，`tx74 ↔ g500`。
* `T3_ADJACENT`（syn1, `+`）：`[40,50) [50,65)` 相邻外显子（0 碱基间隙）。

合成碱基 `base(g)=pattern[g mod 4]`（pattern=ACGT），负链转录本碱基为对应参考
碱基的互补。

## 验证到的关键性质

* **半开**：130/180/210 等外显子端点位点是内含子而非外显子；相邻外显子的 g50 可映射。
* **内含子不硬映射**：点 → `intronic_position`（附上下游外显子）；
  区间 → `region_not_mappable`（附 `intronic_gaps` 与覆盖/请求长度）。
* **跨外显子拆分保序**：tx[25,55) → `[125,130) [160,180) [210,215)`，3 片段共 30 nt。
* **负链方向**：tx[15,50) → 基因组片段 `[620,625) [560,585) [525,530)`（随 tx 递增而
  基因组递减），碱基为互补。
* **往返与长度守恒**：点双向往返；全部外显子区间拆分后逐片段映射回原 tx 区间，
  `Σfragment.length == end-start`。
* **身份隔离**：每个转录本独立 mapper/SQLite 按 id 过滤；越界与未知 id 分别归类。
* **独立性**：`tests/oracle.py` 按问题语义逐碱基构造参考答案（不用被测核心、不用
  numpy），对两条转录本的**每一个**成熟碱基与全部覆盖位点交叉验证。
