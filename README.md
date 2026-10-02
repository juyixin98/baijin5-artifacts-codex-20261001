# oci-layer-merge-checker

本地 OCI 风格层归并检查后端。按内容把一组层目录归并成最终树（不执行任何镜像内容），
逐项保留来源层，把失败原因和不确定结论分类单列，并通过 HTTP 诊断接口暴露可解释的运行记录。

范围限定：**普通文件、目录、白化标记（whiteout / opaque）、受限符号链接**。

## 模块划分（`src/`）

| 模块 | 职责 |
|---|---|
| `model.rs` | 运行模型：`MergeRun`、条目来源（provenance）、状态、步骤时间线 |
| `merge.rs` | 资源算法：层扫描、白化/不透明语义、最终树物化与内容哈希 |
| `paths.rs` | 路径规范化、符号链接目标解析、隔离根包含检查 |
| `store.rs` | 持久状态：JSONL 追加式运行记录（`data/runs.jsonl`） |
| `diag.rs` | 诊断渲染：结构化运行记录 → 人读摘要（日志与 API 共用） |
| `routes.rs` | Axum 诊断接口，请求身份关联（`x-request-id`） |
| `config.rs` | TOML 配置（监听地址、隔离根、存储路径） |
| `error.rs` | 失败类别枚举，测试据此断言失败*类别* |

## 归并语义（固定）

- 层按给定顺序自下而上应用，上层覆盖下层。
- 目录 D 中的 `.wh.<name>` 删除下层的 `D/<name>`（含子树）；上层可随后重建（删除后重建）。
- 目录 D 中的 `.wh..wh..opq` 隐藏下层在 D 之下的全部内容，D 本身保留。
- 白化文件自身绝不出现在最终树中。
- 类型变化（文件↔目录）时新节点替换旧节点；目录被替换时其子树被丢弃。
- 仍为目录的目录跨层合并，保留最底层来源。
- 最终树按内容产生：普通文件复制并计算 sha256；不执行任何内容。

## 路径与链接支持范围

- 树内路径一律为相对、`/` 分隔、UTF-8、无 NUL；`..` 词法解析，禁止弹出隔离根。
- 符号链接仅支持**相对目标且词法解析后仍位于归并根内**；逃逸目标与绝对目标记为
  *不确定结论*（`symlink_target_outside_root` / `absolute_symlink_unsupported`），绝不跟随。
- 硬链接超出支持范围，记为失败类别 `unsupported_hardlink`（保留首次出现的文件，
  按字典序扫描，后出现的同名 inode 被判为硬链接）。
- 服务端另设工作区隔离根（`workspace_root`）：请求中的层路径与输出目录必须解析到其内部，
  否则以 `outside_workspace` 拒绝（HTTP 400）。
- 引擎只写输出目录；输出目录必须不存在或为空，否则致命失败 `output_dir_not_empty`。

## 复现

```bash
cargo test            # 26 个测试：单元 + 归并语义 + 路径安全 + API
./scripts/run_demo.sh # 真实运行：测试 + 起服务 + 正常/异常调用，证据写入 docs/evidence/
```

手动起服务：

```bash
cargo build
MERGE_CHECKER_CONFIG=config/default.toml ./target/debug/merge-checker-server
# 监听 127.0.0.1:8487，工作区根为仓库根，运行记录写入 data/runs.jsonl
```

## 数据夹具（`fixtures/`，由 `scripts/make_fixtures.sh` 确定性重建）

- `layers/layer1..3` — 正常层栈，覆盖：删除后重建（`etc/app.conf`）、不透明目录
  （`data/`）、同名文件↔目录互换（`swap`）、跨层覆盖、根内符号链接（`etc/current`）。
- `expected/final-tree/` — **手工编写的参考树**（脚本中以字面内容写死，
  不由被测实现生成），测试与演示都与它做逐文件比对。
- `malicious/` — 逃逸符号链接、绝对符号链接、硬链接对、非法白化名（`.wh.`）。
- `external/sentinel.txt` — 归并绝不可触碰的外部哨兵文件，测试断言其逐字节不变。

## 证据（`docs/evidence/`，由 `run_demo.sh` 真实生成）

- `test-run.txt` — `cargo test` 完整输出（正常与异常用例全部通过）。
- `server-session.txt` — 服务调用实录：健康检查、正常归并（与参考树 `diff -r` 一致）、
  恶意层栈（失败与不确定结论分类单列）、外部目录未改动、400/404 类型化错误。
- `server.log` — 带 `request_id`/`run_id` 关联的结构化日志，含关键步骤与版本。
- `runs.jsonl` — 持久化的运行记录样本。

调用示例见 [docs/examples.md](docs/examples.md)。依赖锁定见 `Cargo.lock`。
