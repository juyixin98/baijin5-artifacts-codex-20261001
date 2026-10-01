# 运行报告 RUN_REPORT.md

记录在本机对 `collate_agg` 的实际验证：执行过的命令、结果、过程中出现并已修复的
失败，以及**未执行项**。日期：2026-09-29，工具链 `cargo 1.98.1 / rustc 1.98.1`，
Linux x86_64。

## 1. 最终结果

| 检查 | 命令（简写） | 结果 |
|------|--------------|------|
| 格式化 | `cargo fmt` | 已应用，无残留格式差异 |
| Lint | `cargo clippy --all-targets` | **通过，0 warning / 0 error** |
| 全部测试 | `cargo test` | **40 通过 / 0 失败 / 0 忽略** |
| 发布构建 | `cargo build --release` | 通过（见第 4 节） |
| 真实 HTTP 端到端 | 启动服务 + `examples/requests.sh` | 5 个场景全部符合预期 |

测试分布：

- 单元测试（`src/` 内 `#[cfg(test)]`）：**10**
  - `collation::fnv` 2、`collation::rules` 2、`batch` 2、`diag` 2、
    `exec::sort_agg` 1、`exec::hash_agg` 1
- 集成测试（`tests/`）：**30**
  - `tests/contract.rs`：12
  - `tests/fixture_validation.rs`：6
  - `tests/rule_switch.rs`：6
  - `tests/api.rs`：6（真实 axum router 进程内 oneshot）

## 2. 关键行为的实测断言（不是“接口能调用”）

- 重音/大小写等价：R1 下 `Café(NFC) / café(NFC) / CAFE / cafe / Cafe+U+0301(NFD)`
  归为 **1 类（5 行，行号 [0,1,2,3,4]）**；R2 下大小写、重音敏感，分成更多类。
- 数字序列：`item2 / ITEM02` 同类（2 行），`item10`、`item100` 各自独立，
  排序为 `item2 < item10 < item100`；断言了自然序与朴素字典序 **相反**。
- 规范化：NFD 形式（`e` + 组合重音 U+0301）经 NFC 折叠后与 NFC 形式同组。
- 排序聚合 == 哈希聚合 == 独立 oracle：三方分组数、行号分区、逐键哈希、代表值全部一致。
- 夹具总分组数断言为确切值 **8**（见 `tests/fixtures/strings.json` 的手编期望）。
- 规则切换拒绝：一次操作混入 `2026R1/2026R2` 行标签 → `rule_version_mixed`，HTTP **409**；
  未知版本 → `unknown_rule_version`，HTTP **400**；空列 → 422；超行数上限 → 422。
- 身份 vs 聚合键：`key_hex`/`hash` 与原始 `representative`/`distinct_identities` 分离，
  且测试断言聚合键不等于任何原文字面量。
- 脱敏：`diag::redact` 输出 `len=..,fp=........`，测试保证结果不含输入子串；
  HTTP 响应中列名只以脱敏令牌出现（实测 `column_redacted":"len=5,fp=8fcb623d"`）。

## 3. 过程中出现并已修复的失败（如实保留）

实现缺陷 / 环境问题，均已修复后重跑通过：

1. **构建失败**：`src/batch/mod.rs` 单元测试用了含非 ASCII 的原始字节串
   `br#"..."#`（`error: non-ASCII character in raw byte string literal`）。
   改为 `\u{e9}` 转义的 ASCII 字节串。
2. **构建失败**：`tracing_subscriber::fmt().json()` 缺少 `json` feature。
   在 `Cargo.toml` 为 `tracing-subscriber` 启用 `json`。
3. **测试编译失败**：`tests/fixture_validation.rs` 首个测试函数漏写 `fn`
   （`error: missing fn for function definition`）。已补。
4. **测试编译失败**：闭包中 `serde_json::json!({ fx.column: ... })` 试图 move
   被捕获变量（`error[E0507]`）。改为先 `let column = fx.column.as_str();` 借用。
5. **两条测试断言自身写错（被测实现行为正确）**：
   - `numeric_sequences_order_naturally`：最初用错操作数方向比较自然序与字典序；
     修正为同方向比较，并显式断言 `a2.cmp(a10)=Greater`（字典序）对比
     `key(a2).compare(key(a10))=Less`（自然序）。
   - `representative_is_an_original_identity_not_a_key`：原数据三个串等长，
     `shortest_wins` 平局回退首个，期望写错；改用含组合重音的 5 字符 NFD 形态，
     制造真实长度差异，断言最短代表为 4 字符的 `"Cafe"`。

环境侧问题（非代码缺陷）：

- 首次 `cargo build` 长时间无输出，排查发现同机其它并行会话遗留的 `cargo fetch`
  长期持有全局 registry 锁，导致本项目构建在“下载阶段”阻塞（0 个 rustc、
  `target/debug/deps` 为空）。终止僵死进程后，使用**项目内隔离的
  `CARGO_HOME=.cargo-isolated`** + sparse 索引协议完成下载与编译
  （`/.cargo-isolated` 已加入 `.gitignore`）。依赖版本锁定在 `Cargo.lock`。

## 4. 真实 HTTP 端到端实测

启动：

```bash
COLLATE_BIND_ADDR=127.0.0.1:18080 ./target/debug/collate_agg
./examples/requests.sh http://127.0.0.1:18080
```

实测结果：

- `GET /health` → `{"service":"collate_agg","status":"ok"}`
- 重音/大小写/数字/规范化请求 → `decision=accepted`，sort/hash/oracle 三方计数均为 5，
  三个一致性布尔均为 true；每个 group 带 `key_hex`、`hash`、`representative`、
  `distinct_identities`、`rows`。
- 混版本 → HTTP **409**，`failure.kind=rule_version_mixed`，
  诊断含 `distinct_versions=2026R1,2026R2`。
- 未知版本 → HTTP **400**，`failure.kind=unknown_rule_version`，`supplied=1999R0`。
- R2 敏感规则 → `Cafe / cafe / café` 分成 3 类（R1 下为 1 类）。
- 每条诊断都带同一 `request_id`(UUID v4)、`stage`、`decision`、关键 `state`、`reason`。

## 5. 未执行项（明确说明，不假装跑过）

- **覆盖率（80% 目标）未量化**：本机未安装 `cargo-llvm-cov`，未生成覆盖率百分比
  报告。虽然核心行为由 40 个测试广泛覆盖（含正/负路径与失败类别），但**没有**
  产出覆盖率数字，此项标记为“未执行”。如需：
  `cargo install cargo-llvm-cov && cargo llvm-cov --fail-under-lines 80`。
- **`cargo audit` / `cargo deny` 未执行**：未安装相应工具，且漏洞库需要联网拉取；
  未做已知 CVE / 许可证合规扫描。
- 未做性能基准（Criterion）与压测：本项目定位为契约正确性验证，未包含基准套件。
- 未在 Windows/macOS 上验证；仅在 Linux x86_64 实测（路径与代码本身跨平台）。

## 6. 复现方式

```bash
# 常规（依赖已在 Cargo.lock 锁定；如下载受阻可加 sparse 协议）
CARGO_REGISTRIES_CRATES_IO_PROTOCOL=sparse cargo test
cargo clippy --all-targets
cargo run                     # 默认 127.0.0.1:8080
./examples/requests.sh        # 另开终端
```
