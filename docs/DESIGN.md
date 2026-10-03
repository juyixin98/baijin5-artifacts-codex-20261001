# txmap 设计说明

## 1. 固定坐标制（不可协商的约束）

| 系统 | 基制 | 区间 | 方向 |
|---|---|---|---|
| 基因组 | **0-based** | **半开 `[start, end)`** | 沿参考染色体坐标递增 |
| 转录本 | **0-based** | **半开 `[start, end)`**，域为 `[0, mature_length)` | 沿成熟转录本 5'→3' 递增 |

半开区间推论（测试显式断言）：

* 外显子 `[100,130)` 包含碱基 100…129；碱基 130 属于下一个区间（内含子起点）。
* 点位置 `p` 表示索引为 `p` 的那一个碱基。
* 区间长度恒为 `end - start`，相邻外显子 `[40,50), [50,65)` 之间是 **0 碱基间隙**，
  碱基 50 是第二个外显子的第一个碱基，不是内含子。

## 2. 链方向

外显子在夹具中始终按**基因组升序**存储为 `E0 < E1 < …`。

* `+` 链：转录本顺序 = `E0, E1, …`，转录本碱基 = 参考碱基。
  `tx p → g = E.start + (p - cum[i])`。
* `-` 链：转录本顺序 = `…, E1, E0`（基因组降序），每个外显子内部碱基也逐位递减，
  转录本碱基 = 参考碱基的**反向互补**。

设外显子升序长度为 `L0..Ln-1`：

* `suf[i] = Σ_{k>i} Lk`（外显子 i 之后、基因组更高处的碱基数）。
* 负链下，基因组外显子 `i` 内碱基 `g` 的转录本坐标为
  **`tx = suf[i] + (Ei.end - 1 - g)`**。
  即 `tx=0` 是最高外显子的最后一个碱基 `En-1.end - 1`。

> 实现注记：曾误用升序前缀和 `cum[n-1-i]` 代替后缀和 `suf[i]`，在长度不等的
> 外显子上给出错误坐标；现以独立 oracle（`tests/oracle.py`，逐碱基 `range` 构造、
> 不依赖 numpy/被测核心）对全部碱基交叉验证。

## 3. 映射算法（`src/txmap/mapping.py`）

`CoordinateMapper` 是**每个转录本一个不可变实例**，状态不跨转录本共享 → 身份隔离。

* 点映射：`numpy.searchsorted` 在外显子边界数组上二分。
* 区间映射（tx→genomic）：与转录本有序的各段求交，跨内含子时**按外显子边界切成
  多个 Fragment**，Fragment 始终按转录本顺序返回；`genomic_order` 标注其基因组升序
  名次（负链下 Fragment 列表的 genomic 坐标递减，名次为 n-1…0）。
* 区间映射（genomic→tx）：先求与全部外显子的覆盖；若覆盖长度 ≠ 请求长度，说明区间
  触及内含子/基因间区，直接拒绝并在 `intronic_gaps` 中给出未覆盖的半开区间。
  **绝不把内含子位置硬吸附到最近外显子。**
* 批量：`tx_points_to_genomic` / `genomic_points_to_tx` 向量化，逐项状态
  （`mapped` / `invalid` / `coordinate_out_of_range` / `intronic_position`）。

### 片段长度守恒

每个 Fragment 满足 `tx_end - tx_start == genomic_end - genomic_start`；
`Σ fragment.length == 请求区间长度`。负链下基因组端写作半开区间后，
因 `g2(end)>g2(start)` 仍以低坐标为 start（如 tx[15,20) ↔ g[620,625)）。

## 4. 失败类别（API 契约的一部分）

| code | HTTP | 含义 |
|---|---|---|
| `transcript_not_found` | 404 | 转录本 id 不存在 |
| `invalid_interval` | 400 | 非整数、start>end、零长度区间、负坐标 |
| `coordinate_out_of_range` | 422 | 超出转录本/染色体域（含半开端点本身） |
| `intronic_position` | 422 | 点落在内含子，拒绝硬映射，附上下游外显子 |
| `region_not_mappable` | 422 | 区间覆盖内含子，附 `intronic_gaps` 与覆盖统计 |
| `validation_error` | 500 | 夹具数据完整性问题（启动即失败） |

错误信封：`{"error": {"code", "detail", "key_state"}, "request_id"}`。

## 5. 溯源与诊断

* 每次映射尝试（接受或拒绝）都写入 SQLite `map_audit`：`request_id`、`audit_id`、
  转录本、方向、输入、结果或错误码、UTC 时间戳；`GET /audit/{request_id}` 可复核。
* `request_id` 可由请求体或 `X-Request-ID` 头提供，否则服务端生成并回写响应头。
* 日志带 request id 与关键状态；`diagnostics.redact` 对 token/secret/password/
  authorization 类字段一律脱敏，长序列截断（测试断言明文不出现）。

## 6. 模块职责

```
parsing.py    合成夹具解析 + 严格完整性校验
models.py     不可变领域值对象（Exon/Transcript/Fragment/...）
mapping.py    坐标算法（点/区间/批量，numpy）
sequence.py   合成参考序列、互补/反向互补、链方向序列
storage.py    SQLite：参考数据 + map_audit 溯源
service.py    编排：身份隔离、审计落库、结果 DTO
diagnostics.py 结构化日志与脱敏
api/          FastAPI 路由、Pydantic 契约、错误信封
```
