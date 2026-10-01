# collate_agg — 字符串排序聚合 / 哈希聚合比较契约

Rust + [Axum](https://crates.io/crates/axum) + [arrow2](https://crates.io/crates/arrow2)
后端项目。对同一批字符串，使用**自定义 collation** 分别跑「排序聚合」和「哈希聚合」
两套分组/去重执行器，并用一个**独立预言机（oracle）**交叉核验，最终给出带请求标识
和关键状态的接受 / 拒绝判定。

所有数据均为仓库内的本地合成夹具（`tests/fixtures/`），无任何生产账号或真实业务数据，
可完全离线运行。

---

## 1. 契约行为（对应验收第二阶段）

1. **相等语义与排序键一致**：相等性与全序都来自同一个 `SortKey`
   （`equivalent` 与 `compare` 同一定义），保证 `a == b ⇔ cmp(a,b) == Equal`。
2. **同排序等价类保留代表值策略**：分组后保留的是**原始字符串身份**之一
   （`first_wins` / `last_wins` / `shortest_wins`），而不是折叠后的键；
   代表值选择不影响分组与哈希。
3. **哈希分组对等价值产生相容哈希**：排序键有唯一的长度分隔字节编码
   （`canonical_bytes`），再做确定性 FNV-1a；等价键 → 相同字节 → 相同哈希 →
   同一哈希桶。哈希桶碰撞时用完整键相等性裁决。
4. **不同规则版本不可混组**：版本字节参与键编码；请求里若逐行标注了不同版本，
   一次聚合操作会被拒绝（`rule_version_mixed`，HTTP 409）。
5. **原字符串身份 ≠ 聚合键**：`Group` 同时给出
   - `key_hex` / `hash`：聚合键（归一化折叠后，非任何原文）；
   - `representative` / `distinct_identities` / `rows`：原始身份与行号。
   去重按「键」计数，但类内保留各原始身份。

## 2. 模块职责（真实职责分层，非单文件脚本）

```
src/
  collation/      规则版本、排序键、归一化/折叠、数字序列、规范字节编码、FNV 哈希
    mod.rs        sort_key / normalize / 相等与全序
    rules.rs      RuleVersion(2026R1/2026R2)、CollationRule、KeyElement、canonical_bytes
    fnv.rs        确定性 FNV-1a 64
  batch/          类型化列式批次：arrow2 Utf8Array + Chunk/Schema，边界解析与校验
  exec/           查询算子
    sort_agg.rs   排序聚合：算键→排序→合并相邻等键
    hash_agg.rs   哈希聚合：key_hash 分桶 + 完整键裁决碰撞
    group.rs      与执行器无关的逻辑结果 GroupSet / Group
  oracle.rs       独立参考预言机（不复用被测 sort_key/key_hash/exec，
                  独立折叠 + O(n²) 两两并查集）
  service.rs      验证入口：输入→版本解析→混组拒绝→双执行器→分区/哈希/代表值比对
                  →oracle 交叉核验→Verdict + 诊断链（纯逻辑，可直接单测）
  state.rs        资源与状态：AppConfig（TOML + 环境变量）、AppState
  diag.rs         诊断：请求标识、Decision、FailureCategory、脱敏（redact）
  api/            Axum HTTP 传输层（/health、/api/v1/group、/api/v1/validate）
  main.rs         启动：加载配置、tracing、绑定监听
tests/            独立集成测试 + tests/fixtures/strings.json（手工期望值）
config/           collate_agg.toml
examples/         可直接 curl 的示例请求
```

## 3. 支持范围与关键取舍

- 规则版本（显式版本化，切换语义不静默漂移）：
  - **2026R1**：NFC 归一化、**大小写不敏感**、**重音不敏感**（剥离 U+0300–U+036F
    组合重音记号）、数字序列自然排序。
  - **2026R2**：NFC、**大小写敏感**、**重音敏感**、数字序列自然排序。
- 数字序列：仅 **ASCII 数字串**按数值比较（`item2 < item10 < item100`，
  `ITEM02 == item2`）；超长数字串回退为去前导零后的字典序（夹具范围内与数值序一致）。
- 大小写折叠用 Rust `char::to_lowercase`；重音剥离基于 NFD + 组合记号区间。
- **明确不做（已在测试中固化为期望行为）**：不做 `ß ↔ ss`、土耳其语点 i、全角数字、
  locale 特定排序、CJK 部首/拼音排序。`straße` 与 `STRASSE` 在两个版本下都不合并。
- 哈希选用 FNV-1a：分组桶只需要**跨构建/平台确定性**，不需要抗碰撞密码学强度；
  桶碰撞由完整键相等性兜底，正确性不依赖哈希无碰撞。
- 参考答案来源独立：手编夹具的期望值 + `oracle`（独立实现的两两并查集），
  **不**由被测核心生成。

## 4. 本地启动

需要 Rust 工具链（在 1.98 上验证）。依赖已锁定在 `Cargo.lock`。

```bash
cargo build --release
# 默认读 config/collate_agg.toml，可用环境变量覆盖：
#   COLLATE_CONFIG=/path/collate_agg.toml
#   COLLATE_BIND_ADDR=127.0.0.1:9090
#   COLLATE_DEFAULT_RULE=2026R1
#   COLLATE_MAX_ROWS=100000
cargo run
```

健康检查：

```bash
curl -s http://127.0.0.1:8080/health
```

一键发示例请求（另开终端，服务先启动）：

```bash
./examples/requests.sh
```

## 5. 示例请求

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/group \
  -H 'content-type: application/json' \
  --data-binary @examples/equivalence.json
```

请求字段：

| 字段 | 说明 |
|------|------|
| `column` | 列名（非空） |
| `rule_version` | `"2026R1"` 或 `"2026R2"`，缺省用配置默认值 |
| `row_rule_versions` | 可选，逐行版本标注；与 `rule_version` 不一致即拒绝混组 |
| `representative_policy` | 可选：`first_wins`(默认)/`last_wins`/`shortest_wins` |
| `values` | 字符串数组（边界限制 `max_rows`） |

响应（节选）含：`decision`、`failure`(类型化失败类别)、`comparison`
（三种分组数 + 三个一致性布尔）、`sort_result`/`hash_result`（每个类的键、代表值、
原始身份、行号、哈希）、`deduplicated`（每类一个代表值）以及带 `request_id` 的
`diagnostics` 链。

失败类别（测试断言具体类别，而不仅是「能调用」）：

| kind | HTTP | 含义 |
|------|------|------|
| `unknown_rule_version` | 400 | 版本不在注册表 |
| `rule_version_mixed` | 409 | 一次操作混入不同版本 |
| `empty_column` / `invalid_row` | 422 | 输入边界不合法 / 超行数上限 |
| `executor_mismatch` / `oracle_mismatch` | 500 | 内部契约不一致（正常永不发生） |

## 6. 诊断与脱敏

每条 `DiagRecord` 带：`request_id`、`stage`（input/rule/version_mix/exec/oracle/
compare）、`decision`（accepted/rejected/undetermined）、关键 `state`
（行数、版本、各分组数等）与 `reason`，说明**为何接受、拒绝或无法判定**。
列名等潜在敏感值只输出 `len=<字符数>,fp=<8位指纹>` 的不可逆脱敏令牌，
不回显原文（见 `diag::redact`，有专门测试保证脱敏结果不含输入子串）。

## 7. 测试

```bash
cargo test                 # 全部单元 + 集成测试
cargo test -- --nocapture  # 含输出
```

测试覆盖（断言具体结果与失败类别）：

- `tests/contract.rs`：相等/排序一致性、重音大小写等价、数字自然序、代表值策略、
  相容哈希、版本字节隔离、身份与键区别、NFD→同组、混组拒绝。
- `tests/fixture_validation.rs`：用手编夹具断言**确切分组数(8)**、每类行号、
  代表值、数字序、归一化、`ß` 范围行为；排序/哈希两执行器都与独立 oracle 一致；
  端到端 `accepted` 且三个一致性标志为真；R2 下分组变多。
- `tests/rule_switch.rs`：混组 409、全行同版本接受、未知版本归类、R1/R2 分组数不同、
  空列与超大行数拒绝。
- `tests/api.rs`：真实 axum router 进程内 oneshot：200/409/400/坏 JSON 400。

## 8. 验证记录（实际运行）

见仓库根目录 `RUN_REPORT.md`（记录实际执行的命令、通过/失败与未执行项）。
