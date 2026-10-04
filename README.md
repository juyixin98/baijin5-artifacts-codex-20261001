# cdc-service — 滚动哈希内容定义分块服务

基于 gear 滚动哈希的内容定义分块（CDC）服务，支持最小/最大块长与目标边界模式，
Rust + Axum + Serde 实现。所有数据均为本地合成夹具，不依赖任何生产账号或真实业务数据。

## 快速开始

```bash
cargo build
cargo test                                   # 全部单元 + 集成测试
cargo run -- config/default.toml             # 监听 127.0.0.1:8080
curl -s http://127.0.0.1:8080/v1/health      # {"status":"ok"}
```

重新生成交互测试向量（可选，已提交在 `fixtures/`）：

```bash
python3 tools/reference_chunker.py --generate-vectors fixtures
```

## 算法 `gear64-cdc-v1`

- 每块起始时 `h = 0`；逐字节 `h = (h << 1) + GEAR[b]`（64 位回绕），有效窗口 64 字节。
- 当当前块长 `>= min_size` 且满足以下任一条件即切分：
  - 块长达到 `max_size`（强制切分）；或
  - `(h & mask) == pattern`，其中 `mask = (1 << mask_bits) - 1`（目标边界模式）。
- 输入结束时冲刷尾部不足 `min_size` 的残块。
- **空输入规则：空输入产生 0 个块**（不是 1 个空块）。
- 因为 `h` 在每次切分处重置，边界是内容的纯函数：**同一内容以任意分段方式喂入，
  边界完全相同**（`chunker::tests::segmentation_invariance` 覆盖 1/3/7/64/1024 字节分段）。
- 已知特性：长重复字节序列会使哈希低位置于不动点，内容切分不再触发，
  此时由 `max_size` 强制切分兜底（`long_repeat_is_cut_by_max_size_only_and_is_stable`
  断言该行为）。

## 模块划分（各有真实职责）

| 模块 | 职责 |
|---|---|
| `src/gear.rs` | 由固定种子经 splitmix64 确定性派生的 256 项 gear 表（编译期常量） |
| `src/chunker.rs` | 流式分块内核：O(1) 状态跨 `feed` 保留，max_size 强制切分 |
| `src/manifest.rs` | 算法标识 + 全部参数写入清单；清单摘要标识分块体制而非数据 |
| `src/format.rs` | `CDCB/1` 二进制容器（清单头 + 块索引 + 载荷）编解码 |
| `src/recovery.rs` | 恢复内核：结构→拼接→逐块摘要→整体摘要→**逐字节**比对五层校验 |
| `src/limits.rs` | 资源控制：输入字节数 / 请求体 / 块数上限 |
| `src/diagnostics.rs` | 请求标识与 ACCEPT/REJECT/UNDETERMINED 决策记录，只记脱敏信息 |
| `src/config.rs` | TOML 启动配置加载与参数校验 |
| `src/api.rs` | Axum HTTP 表面（薄层，不含核心逻辑） |

测试与配置独立组织：`tests/`（互操作、边界稳定性、API 集成）、`config/default.toml`、
`fixtures/`（样例数据 + 向量）、`tools/reference_chunker.py`（独立 Python 参考实现）。

## HTTP API

| 方法/路径 | 说明 |
|---|---|
| `GET /v1/health` | 存活探针 |
| `GET /v1/manifest` | 当前分块体制清单 |
| `POST /v1/chunk` | 请求体为原始字节，返回 JSON 块表（offset/len/sha256） |
| `POST /v1/chunk-container` | 同上，返回 `application/x-cdcb` 二进制容器 |
| `POST /v1/verify` | 请求体为 CDCB 容器，返回验证决策 |

- 请求头 `x-request-id` 会被透传进响应与日志；缺省时服务端生成 `req-<毫秒>-<计数>`。
- 错误响应为 `{"request_id", "error": {"category", "message"}}`，类别取值：
  `invalid_params` / `limit_exceeded`(413) / `malformed_container`(400) /
  `digest_mismatch`(422) / `payload_mismatch`(422) / `undetermined`。
- `/v1/verify` 在容器内部一致但清单与本服务体制不同时返回 `undetermined`，
  并说明“边界不可比”的原因。

## 证据：测试命令与结论

### 1. 全部测试

```bash
cargo test
```

实测输出（2026-10-04，本仓库）：lib 单元测试 20 通过；`tests/api_integration.rs` 6 通过；
`tests/boundary_stability.rs` 4 通过；`tests/interop_vectors.rs` 3 通过。共 33 项，0 失败。

### 2. 与独立参考实现逐字节核验

`tests/interop_vectors.rs` 将 Rust 内核的输出与 `fixtures/vectors.json` 逐项比对
（边界、每块 sha256、整体 sha256）。向量由 `tools/reference_chunker.py` 生成——
一个与 Rust 内核**不共享任何代码**的 Python 实现（哈希以显式 fold 重算），
因此参考答案并非由被测核心自身产生。8 个用例：empty / tiny / text / repeated /
random / patterned（非零 pattern）/ insert_base / insert_modified。

`tests/boundary_stability.rs` 还内置第二个独立谕言机：朴素参考实现逐位置
重算窗口哈希（O(n·w) 不同代码路径），在 empty/tiny/random/long-repeat/mixed
五类输入上与流式内核逐一比对边界，全部一致。

### 3. 局部修改造成的块变化范围

```bash
cargo test --test boundary_stability small_insert -- --nocapture
```

实测输出：

```
insert of 100 bytes at 30000 disturbed base range [28952, 30506) — 1554 bytes of 60000 total (46 -> 46 chunks)
```

结论：在 60000 字节随机输入中部插入 100 字节，受扰动的块仅覆盖 1554 字节
（含插入点的那个块及其延伸），插入点之前的边界逐一相同，之后第一个内容切分处
重新同步，后续边界以恒定 +100 位移对齐。测试同时断言扰动范围不早于
`insert_at - max_size`、不晚于 `insert_at + max_size`。

长重复输入（100000 个相同字节）实测全部由 `max_size` 强制切分且逐字节喂入结果一致；
随机输入实测产生内容定义切分（非等长块），且每块满足 `min_size <= len <= max_size`
（尾块除外）。

### 4. 拒绝与失败类别（实测）

```bash
cargo run -- config/default.toml &
curl -s -X POST --data-binary @fixtures/random.bin -o /tmp/random.cdcb http://127.0.0.1:8080/v1/chunk-container
python3 -c "d=bytearray(open('/tmp/random.cdcb','rb').read()); d[-1]^=1; open('/tmp/t.cdcb','wb').write(d)"
curl -s -X POST --data-binary @/tmp/t.cdcb http://127.0.0.1:8080/v1/verify
# => HTTP 422  digest_mismatch: "chunk 6 (offset 88954, len 11046) digest mismatch: recorded 638cac83..., computed b05f4ce5..."
curl -s -X POST --data-binary 'not a container' http://127.0.0.1:8080/v1/verify
# => HTTP 400  malformed_container: "bad magic: not a CDCB container"
```

空输入实测：`POST /v1/chunk` 空请求体 → 200，`"payload_len": 0, "chunks": []`。

### 5. 诊断日志（脱敏）

每个请求产生一条决策记录，含请求标识、操作、决策、原因与关键状态；
只记录长度与摘要前 16 位十六进制，绝不记录载荷字节。实测：

```
request decision request_id=demo-1 operation=chunk_json decision=Accept reason="chunked within limits" input_bytes=Some(18000) chunk_count=Some(1) payload_digest_prefix=Some("3491396f336c9b03")
request decision request_id=req-...-3 operation=verify decision=Reject reason="chunk 6 (offset 88954, len 11046) digest mismatch: ..." input_bytes=Some(100623)
```

### 6. 摘要不是字节校验的替代品

恢复内核五层校验的最后一层：当调用方提供原始字节时，即使全部摘要匹配，
仍逐字节比对恢复结果与原始数据，不一致则报 `payload_mismatch`
（`recovery::tests::wrong_original_is_payload_mismatch_not_digest_mismatch`）。

## 配置

见 `config/default.toml`：`listen`、`[limits]`（max_input_bytes / max_body_bytes /
max_chunks）、`[chunker]`（min_size / max_size / mask_bits / pattern）。
启动时校验参数合法性，非法配置拒绝启动并说明原因。
