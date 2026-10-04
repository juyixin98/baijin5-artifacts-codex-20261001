# cdc-service — 滚动哈希内容定义分块服务

基于 Gear 滚动哈希的内容定义分块（Content-Defined Chunking, CDC）服务。
相同内容无论按何种 I/O 切块方式输入，都得到相同的分块边界；每次分块产出
一份自描述的清单（manifest），可用于恢复与逐字节核验。

技术栈：Rust + Axum + Serde。所有测试数据均为本地合成夹具，无需任何外部账号。

## 快速开始

```bash
cargo build --bins                 # 构建 cdc-server / cdc-report / cdc-gen-fixtures
cargo run --bin cdc-server         # 默认读取 config/default.toml，监听 127.0.0.1:8080
# 或显式指定配置：
cargo run --bin cdc-server -- --config config/default.toml
```

启动配置见 `config/default.toml`（监听地址、分块参数、资源限制）。环境变量
`CDC_LISTEN`、`CDC_MAX_BODY_BYTES`、`CDC_MAX_CONCURRENT` 可覆盖文件值；
`RUST_LOG` 控制日志级别。

试调用：

```bash
curl -s http://127.0.0.1:8080/healthz
curl -s http://127.0.0.1:8080/v1/params
curl -s -X POST --data-binary @tests/fixtures/random_64k.bin http://127.0.0.1:8080/v1/chunk
```

## 算法规格（gear-cdc v1）

- 齿轮表：`GEAR[i]` 为 SplitMix64 序列第 i 项，初始状态 `0x9E3779B97F4A7C15`。
- 滚动更新：`h = (h << 1) + GEAR[b]`（mod 2^64），有效窗口 64 字节；
  每个块起点处哈希状态清零。
- 边界判定（逐字节，按序）：
  1. 当前块达到 `max_size` → 强制切分；
  2. 当前块 ≥ `min_size` 且 `h & ((1<<avg_bits)-1) == 0` → 内容定义切分；
  3. 输入结束时冲刷不足一块的尾部。
- 目标平均块长为 `2^avg_bits` 字节。
- **空输入规则**：空输入产生 0 个块、`total_len = 0`、内容摘要为空串的
  SHA-256（`e3b0c442…b855`）。小于 `min_size` 的输入产生恰好 1 个块。

每份清单都写入上述算法名、版本与全部参数（`algorithm` 字段），读者无需带外
信息即可复现或拒绝该分块。清单中的 SHA-256 摘要只是完整性**提示**；权威性
核验永远是对原始字节的逐字节比较（`recovery::verify_against`，API 中通过
`reference_base64` 触发）。

## HTTP API

| 端点 | 说明 |
|---|---|
| `GET /healthz` | 存活探针 |
| `GET /v1/params` | 当前算法规格与资源限制 |
| `POST /v1/chunk` | 原始字节 → JSON 清单；`?format=binary` 返回二进制 "CDCM" 清单 |
| `POST /v1/manifest/parse` | 二进制 "CDCM" 清单 → JSON 清单 |
| `POST /v1/verify` | `{manifest, content_base64, reference_base64?}` → 核验报告 |

错误响应统一为 `{"error": {"category", "message"}, "request_id"}`，类别稳定
可比（如 `body_too_large`、`too_many_chunks`、`malformed_manifest`、
`chunk_digest_mismatch`、`byte_mismatch`、`timeout`）。每个响应带
`x-request-id` 头。

## 模块划分（真实职责）

| 模块 | 职责 |
|---|---|
| `src/gear.rs` | 滚动哈希原语（规格常量、64 字节窗口性质） |
| `src/params.rs` | 分块参数与校验（min < avg ≤ max、窗口下界） |
| `src/chunker.rs` | 流式分块核心：跨输入缓冲保持滚动状态，max 强制切分 |
| `src/manifest.rs` | 自描述清单模型（算法+参数+块表） |
| `src/format.rs` | 二进制清单格式 "CDCM" 编解码（魔数/版本/记录/SHA-256 尾校验） |
| `src/recovery.rs` | 恢复内核：覆盖性检查、摘要提示、逐字节权威核验 |
| `src/analysis.rs` | 边界稳定性分析（局部修改的影响范围计算） |
| `src/limits.rs` | 资源控制（体积/块数/并发/超时） |
| `src/config.rs` | 启动配置（TOML 文件 + 环境变量覆盖，启动即校验） |
| `src/diagnostics.rs` | 决策记录与脱敏 |
| `src/api.rs` | HTTP 表面、请求 ID 中间件、错误映射 |
| `src/bin/cdc_report.rs` | 证据报告生成器（断言式，失败即非零退出） |
| `src/bin/gen_fixtures.rs` | 样例数据与黄金文件再生成 |

## 诊断与脱敏

每个请求结束时输出一条结构化决策记录：请求 ID、端点、结论
（`accept` / `reject` / `undetermined`）、原因与关键状态（长度、块数、
摘要前 8 位）。**内容字节永不落日志**；摘要只打印截断前缀（如
`7c4b887b…`），足以关联、不足以还原数据。示例（真实输出）：

```text
INFO cdc_service::diagnostics: request decision request_id=req-4 endpoint=verify
  decision="accept" reason=manifest verified against supplied bytes
  state={"byte_compare": "true", "chunks": "3", "total_len": "65536"}
INFO cdc_service::diagnostics: request decision request_id=req-5 endpoint=manifest/parse
  decision="undetermined" reason=binary manifest not decodable
  state={"cause": "input too short: 1 bytes, need at least 91"}
```

## 测试与证据

```bash
cargo test                                    # 全部单元 + 集成测试
cargo run --bin cdc-report                    # 重新生成边界稳定性证据
python3 scripts/reference_chunker.py --check  # 独立 Python 参考实现核验黄金文件
cargo run --bin cdc-gen-fixtures              # （仅需要时）重新生成夹具与黄金文件
```

最近一次运行的真实结论：

- `cargo test`：**71 个测试全部通过**（42 单元 + 29 集成，0 失败）。
- `scripts/reference_chunker.py --check`：4 个夹具（empty / random_64k /
  repeat_64k / text_sample）的边界与逐块摘要与 Rust 核心**完全一致**——黄金
  答案由独立的 Python 实现复核，并非只由被测核心自产自证。
- `docs/evidence.md`（由 `cdc-report` 生成，内含断言）：1 MiB 随机数据按
  1/7/64/4096/65536 字节五种喂入方式得到**完全相同的 208 条边界**；在
  25%/50%/75% 处插入 100 字节，受影响块范围分别为 10414 / 9833 / 3724 字节，
  均在 `2·max_size + 窗口` 局部上界内；1 MiB 长重复字节全部按 max_size
  强制切分（64/64 块）。

测试组织：

- `tests/chunker_reference.rs` — 流式核心 vs 独立非滚动参考实现（随机/重复/
  文本/边界尺寸），逐块摘要独立重算。
- `tests/golden.rs` — 提交在库的黄金文件钉死边界与摘要。
- `tests/format_recovery.rs` — 二进制格式往返、损坏类别、伪造摘要被逐字节
  比较识破。
- `tests/api_integration.rs` — HTTP 端到端：空输入清单、核验接受/拒绝类别、
  超限 413、二进制清单往返、请求 ID。
- `tests/boundary_stability.rs` — 插入/删除/长重复/随机数据的边界稳定性硬断言。

## 目录

```
config/default.toml      启动配置
docs/evidence.md         边界稳定性证据（cdc-report 生成）
scripts/reference_chunker.py  独立 Python 参考实现（黄金文件交叉核验）
tests/fixtures/          样例输入与期望边界（黄金文件）
```
