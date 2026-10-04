# iblt-service — 可逆布隆查找表（IBLT）集合差异恢复教学服务

用 Rust + Axum + Serde 实现的集合差异协调（set reconciliation）教学服务。
双方各自把本地集合编码成 IBLT，交换表后相减、解码，即可恢复**两侧**的差集，
而无需传输完整集合。

## 核心语义

- 每个单元（cell）三元组：`(count, key_sum, hash_sum)`
  - `count`：有符号计数。插入 +1，删除 −1；差表中**负计数表示反向差**（仅在 B 侧出现的键）。
  - `key_sum`：落入该单元的所有键的 XOR。
  - `hash_sum`：落入该单元的所有键之校验和的 XOR。
- **纯单元判定（剥离前必须真实校验）**：`count == ±1` **且**
  `hash_sum == checksum(key_sum)` **且** 该单元下标属于 `key_sum` 的哈希位置集合。
  仅凭计数 ±1 猜测键是被明确禁止并由测试锁定的行为。
- **解码失败即要求扩表**：解码停滞时返回 `decode_incomplete`（HTTP 422，
  类别 `resource_exhausted`），携带剩余非空单元数与扩表提示，
  **绝不**把已剥出的部分结果当作完整集合返回。

## 工程结构

```
src/
  error.rs    统一错误分类：invalid_input / state_conflict / resource_exhausted / computation_failed
  hash.rs     splitmix64 确定性哈希：k 个互异下标 + 键校验和
  iblt.rs     编码/恢复内核：insert/remove/subtract/decode
  format.rs   版本化二进制线格式（见 docs/protocol.md）
  limits.rs   资源控制：max_cells / max_keys / max_body_bytes
  runlog.rs   运行编号分配（run-<启动时间>-<序号>）
  service.rs  Axum HTTP 边界与 JSON 契约
  main.rs     服务入口（PORT 环境变量，默认 8080）
tests/
  core_decode.rs     内核：小差集、重复输入、强制碰撞、超载、校验篡改
  format_interop.rs  互操作：提交的二进制夹具 + 外部参考答案（sort/comm）
  service_api.rs     端到端：状态码、错误类别、无部分结果泄漏
fixtures/    最小数据夹具（键列表、参数、二进制表、外部计算的期望差集）
scripts/     gen_expected.sh（生成参考答案）、run_checks.sh（一键复现）
docs/        protocol.md（格式规范）、examples-output/（真实运行采集）
```

## 构建、测试、运行

```bash
cargo build            # 依赖已锁定（Cargo.lock）；离线环境用 --offline
cargo test             # 36 个测试：单元 + 内核 + 互操作 + 端到端
cargo run              # 监听 :8080；PORT=9000 cargo run 换端口
```

一键复现（测试 + 真实服务正常/异常调用，输出落盘到 `docs/examples-output/`）：

```bash
bash scripts/run_checks.sh        # PORT=18234 bash scripts/run_checks.sh 换端口
```

## API 契约

表一律以二进制格式的 base64 编码传输。每个响应都带 `run_id`，
与服务器日志中的 `run_id` 一一对应，可据此重放单次请求的
关键中间状态（peeled、remaining_nonzero_cells）与判断理由（decision）。

### POST /v1/encode

```bash
curl -s -X POST localhost:8080/v1/encode -H 'content-type: application/json' \
  -d '{"keys":[1,2,3,4,5,6,7,8,9,10,11,12]}'
```
```json
{"run_id":"run-...-000000","params":{"cells":18,"k":3,"seed":5278865638494187777},
 "stats":{"keys":12,"nonzero_cells":16},"table_b64":"SUJMVAEA..."}
```
`params` 可省略（默认 `max(8, ceil(1.5*n))` 个单元、k=3、固定种子），
也可显式指定 `{"cells":32,"k":3,"seed":...}`。同一请求内键重复 → 400 `invalid_input`。

### POST /v1/decode

```bash
curl -s -X POST localhost:8080/v1/decode -H 'content-type: application/json' \
  -d '{"table_a_b64":"'$TA'","table_b_b64":"'$TB'"}'
```
成功（200）：
```json
{"run_id":"run-...-000002","complete":true,
 "only_a":[1,2,3,4,5,6],"only_b":[13,14,15,16,17,18],"stats":{"peeled":12}}
```
失败——表太小（422，**无** `only_a`/`only_b` 字段）：
```json
{"run_id":"run-...-000008","error":{"category":"resource_exhausted",
 "code":"decode_incomplete",
 "message":"decode stalled: 16 cells still hold unrecovered keys ...",
 "detail":{"remaining_nonzero_cells":16,"peeled":0,"hint":"table too small ..."}}}
```

### POST /v1/subtract

`{"table_a_b64","table_b_b64"}` → 差表（`{"params","table_b64"}`），
可用于教学演示与互操作验证。

### 错误契约

所有失败统一为 `{"run_id","error":{"category","code","message","detail?"}}`：

| category | HTTP | 含义 | 示例触发 |
|---|---|---|---|
| `invalid_input` | 400 | 输入错误 | JSON 畸形、base64 非法、二进制帧错误、重复键、非法参数 |
| `state_conflict` | 409 | 状态冲突 | 两表参数（cells/k/seed）不一致仍求差 |
| `resource_exhausted` | 422 | 资源耗尽 | 超过 max_keys/max_cells、请求体超限、**解码停滞需扩表** |
| `computation_failed` | 500 | 计算失败 | 内部剥离步数保护被触发（正常不会到达） |

## 测试设计要点

- **参考答案独立生成**：`fixtures/expected_only_*.txt` 由
  `scripts/gen_expected.sh` 用 `sort`/`comm` 从键列表算出，不经 Rust 内核；
  集成测试再用 `BTreeSet` 直接差集运算交叉验证。
- **具体断言**：每个测试断言具体结果集合、具体错误变体/类别/状态码，
  并断言失败响应中不存在 `only_a`/`only_b`（防止部分结果泄漏）。
- **异常覆盖**：重复输入（计数 2 永不纯）、强制哈希碰撞（cells=1）、
  超载表（40 键 / 16 单元）、校验和篡改、伪造纯单元（计数对但校验错、
  校验对但位置错）、二进制帧错误（魔数/版本/截断/超长）。
- **可重放日志**：`docs/examples-output/server.log` 保留真实运行的
  run_id、中间状态与决策理由。

## 夹具再生成

```bash
bash scripts/gen_expected.sh              # 期望差集（sort/comm，独立参考）
cargo run --example gen_fixtures          # 二进制表夹具（格式契约锚点）
```

修改 `fixtures/keys_*.txt` 或 `fixtures/params.json` 后两条都要重跑。
