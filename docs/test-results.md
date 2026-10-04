# 测试结果记录（真实运行，可复核）

运行方式：`bash scripts/run_checks.sh`（PORT=18234）。
原始采集保存在 `docs/examples-output/`，本文件为索引与结论。

## 自动化测试（cargo test）

共 36 个测试，全部通过（逐条输出见 `examples-output/test-summary.txt`）：

| 测试目标 | 文件 | 数量 | 覆盖 |
|---|---|---|---|
| 库单元测试 | src/*.rs | 13 | 哈希确定性/互异下标、参数校验、insert/remove 互逆、格式往返、限制执行、run_id 单调 |
| 内核恢复 | tests/core_decode.rs | 10 | 已知小差集双侧恢复、空差、负计数反向差、重复输入、强制碰撞（cells=1）、超载表、伪造纯单元（校验错/位置错）、校验篡改、参数冲突 |
| 互操作 | tests/format_interop.rs | 4 | 夹具解码对拍外部参考答案（sort/comm）、重编码逐字节一致、头字段、帧错误分类 |
| 端到端 API | tests/service_api.rs | 9 | 编码→解码往返、重复键 400、键数超限 422、解码停滞 422 且无部分结果、参数冲突 409、坏 base64/坏 JSON 400、subtract 往返 |

关键断言样例（均为具体结果，非"接口能调"）：

- `recovers_known_small_diff_both_sides`：`only_a == [1..6]`、`only_b == [13..18]`、`peeled == 12`。
- `pure_cell_requires_checksum_match_not_just_count`：伪造单元（count=1、校验和错误）不被剥离 → `DecodeIncomplete{peeled:1, remaining:1}`。
- `checksum_match_at_wrong_position_is_not_peeled`：校验和正确但不在键的哈希位置 → 同样拒绝剥离。
- `decode_incomplete_is_422_with_hint_and_no_partial_sets`：响应体**不存在** `only_a`/`only_b` 字段。
- `fixture_tables_decode_to_externally_computed_diff`：内核结果 == `sort/comm` 外部参考。

## 真实服务运行（curl 采集于 docs/examples-output/）

| 文件 | 结果 | 说明 |
|---|---|---|
| `health.json` | 200 `{"status":"ok"}` | 存活检查 |
| `encode_a.json` / `encode_b.json` | 200 | A=1..12、B=7..18，默认参数 cells=18 |
| `decode_ok.json` | 200，`only_a=[1..6]`、`only_b=[13..18]`、`peeled=12` | 正常双侧恢复 |
| `err_duplicate_keys.json` | 400 `invalid_input` | 输入错误类别 |
| `err_bad_json.json` | 400 `invalid_input` | JSON 畸形 |
| `err_bad_base64.json` | 400 `invalid_input` | 表字段非法 base64 |
| `err_decode_incomplete.json` | 422 `resource_exhausted` / `decode_incomplete`，含 `remaining_nonzero_cells=16` 与扩表 hint，**无部分结果** | 资源耗尽类别（40 键塞 16 单元） |
| `err_state_conflict.json` | 409 `state_conflict` | 状态冲突类别（cells 18 vs 32） |
| `server.log` | — | 每个请求的 run_id、中间状态（peeled、remaining_nonzero_cells）与决策（`decision="complete"` / `"refuse_partial_result"`） |

## 测试过程中发现并修复的真实缺陷

`count_and_checksum` 正向伪造测试首次运行时触发内核死循环，被剥离步数
上限兜住（`ComputationFailed`）。根因：伪造单元若不在键的哈希位置上，
剥离会污染无关单元且自身永不排空。修复：纯单元判定增加"当前下标 ∈
indices(key_sum)"的位置校验（`src/iblt.rs`），腐坏表现在收敛为
`DecodeIncomplete` 而非依赖兜底。该回归由
`checksum_match_at_wrong_position_is_not_peeled` 锁定。
