# 验证结果记录 (verification run log)

本文件记录在交付环境上**实际执行**的命令与结果。环境：Linux
6.8.0-90-generic, rustc 1.98.1, cargo 1.98.1。

> 共享机器的全局 `~/.cargo` 缓存锁被多个并行 cargo 任务长时间争抢，构建一度
> 阻塞 45 分钟无法下载任何 crate。改用项目专属 `CARGO_HOME=$PWD/.cargo-home`
> 后正常。该目录已在 `.gitignore` 中；`Cargo.lock` 仍提交，常规机器上直接
> `cargo test --locked` 即可复现。

## 1. 构建 / 静态检查（实际通过）

| 命令 | 结果 |
|---|---|
| `cargo build --locked` | 通过，0 error / 0 warning |
| `cargo clippy --all-targets -- -D warnings` | 通过，0 warning |
| `cargo fmt --check` | 通过（无 diff） |

锁定的关键版本：arrow2 0.18.0, axum 0.7.9, tokio 1.53.1,
unicode-normalization 0.1.25。

## 2. 测试（实际通过，31 个）

`cargo test --locked`：

- 单元测试（src 内 `#[cfg(test)]`）：**11 passed**
  - batch Arrow 往返 / 空批次；v1 重音大小写与合成-分解等价；自然数字序；
    v2 二进制；排序/哈希/去重代表值策略；NULL 成组。
- HTTP 集成 `tests/api.rs`：**5 passed** — 健康检查、双执行器一致、
  未知规则分类拒绝、诊断含请求标识且不含原始值、敏感值脱敏。
- 契约 `tests/contract.rs`：**15 passed** — 含第三阶段三类夹具
  （重音大小写等价、数字序列、规范化字符）、排序 vs 哈希逐字节比较、
  分组数与独立 oracle 计数、规则切换拒绝、身份≠聚合键、代表值保留、
  数字溢出判“无法判定”、以及 50 组种子随机输入对照独立 oracle 的性质测试。
- doc-tests：0。

## 3. 离线 demo（实际通过）

`cargo run -- demo`：5/5 `PASS`，末行 `ALL DEMO CASES VERIFIED`。
覆盖：重音/大小写/规范化（2 组）、数字序列（3 组）、会话先 v1 后切 v2
被 `reject_rule_version_mismatch` 拒绝、未知版本 `reject_unknown_rule`。

## 4. 真实 HTTP 冒烟（实际通过）

启动 `serve` 后用 `examples/*.json` 与临时 body 实测：

- accent：合成 `é`(U+00E9) 与分解 `e`+U+0301 同组，`Café/CAFE/...` 合为
  1 类，`accepted`，groups=2，代表值保留首行原值 `Café`。
- numeric：`file2 < file10`，`item1` 与 `item01` 同类，groups=3。
- 会话：v1 批次 `accepted`（batches=1），同会话 v2 批次
  `rejected` / `reject_rule_version_mismatch`，且被拒批次未并入（仍 1 batch）。
- 脱敏：`sensitive=true` 时代表值渲染为
  `<str bytes=12 fp=45cf088479766cba>`，无明文。
- 数字溢出：30 位数字 → `undetermined` / `undetermined_numeric_overflow`，
  并列出溢出记录 id。
- v2 二进制：`Café / café / cafe` 保持 3 组。
- `/diagnostics`：记录含 request_id、reason_code、行数与解释，detail 中
  不出现任何原始字符串。

## 5. 失败项

最终状态：**无失败测试**。开发过程中出现并已修复的真实失败：

1. `is_combining_mark` 处字符区间字面量漏写一个闭合引号 → 编译错误，已修。
2. 排序执行器在 `matches!` 守卫中修改绑定（E0594/E0596）→ 重构为
   `pending: Option<(GroupKey, Group)>` 显式扫描。
3. arrow2 0.18.0 无 `record_batch` 模块（E0432）→ 直接以
   `Chunk<Box<dyn Array>>` + 自持 `Schema` 实现 `InputBatch`。
4. 示例 `accent.json` 的 r2 被误写成不同的词而非“分解形式”，导致多出一组
   （冒烟实测暴露，`reject_expected_count`）→ 改为 `e`+U+0301 后通过。
5. `DiagRecord` 未派生 `Serialize`、`tracing` 未直接声明、`fatal` 的 `!`
   协变推断等编译问题 → 均已修，clippy 归零。

## 6. 未执行 / 未覆盖项（如实列出）

- 未做覆盖率百分比采集（环境无 `cargo-llvm-cov`，未联网安装该工具）。
  关键路径均有断言式测试，但未产出 80% 覆盖率报告这一量化产物。
- 未运行 `cargo audit` / `cargo deny`（环境无该工具）。
- 未做压测/基准（Criterion 未引入）；执行器为教学清晰的内存实现。
- 未提供 Dockerfile / systemd 单元；本地启动以 `cargo run -- serve` 为准。
- 重音折叠仅处理 NFKD 后 U+0300–U+036F 组合记号；区域相关排序
  （德语 ß、土耳其语点线 ı、多级 tie-break）不在支持范围（见 README §7）。
