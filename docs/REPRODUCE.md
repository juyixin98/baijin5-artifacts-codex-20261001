# 复现指南

环境：Rust stable（开发使用 1.98.1），Linux。依赖由 `Cargo.lock` 锁定，
`cargo build --locked` 可逐位复现依赖图。

## 1. 构建与测试

```bash
cargo build --locked
cargo test --locked
```

预期：31 个测试全部通过（18 个单元测试 + 7 个 API 端到端 + 6 个语义
集成测试）。本次交付的实际输出留存于 `evidence/cargo-test-run.txt`。

测试覆盖的核验点：

- `tests/merge_semantics.rs` — 对四个夹具场景（删除后重建、不透明目录、
  同名文件/目录互换、恶意链接）逐条对照**手写**期望树
  （`fixtures/*/expected.json`；其中文件哈希由独立工具 `sha256sum`
  计算，不是被测实现生成），并断言归并前后 `fixtures/`（含
  `fixtures/outside/` 哨兵目录）逐字节、mtime 不变。
- `tests/api_tests.rs` — 真实端口起服务、真实 HTTP 客户端验证：
  请求关联（`x-request-id` 回显于响应与报告）、来源层标注、诊断
  分列，以及全部异常类别（越根层 403、缺失层 400、空层列表 400、
  未知 run 404、非法 run id 400）。
- 单元测试 — 路径规范化（`.`/`..`/越根/绝对路径）、白化标记解析、
  层内与跨层语义、链接环、存储往返与 run id 防穿越。

## 2. 启动服务

```bash
cargo run            # 读取 config/default.toml，默认 127.0.0.1:8080
# 或覆盖：
MERGE_CHECK_BIND=127.0.0.1:18099 MERGE_CHECK_STATE_DIR=./state \
  ./target/debug/merge-checkd
```

配置项（均有默认值，环境变量优先）：`bind`、`state_dir`、
`layer_roots`（层路径白名单，请求中的层必须解析到其内）、
`max_entries`、`max_symlink_depth`。对应环境变量为
`MERGE_CHECK_BIND` / `MERGE_CHECK_STATE_DIR` /
`MERGE_CHECK_LAYER_ROOTS`（冒号分隔）/ `MERGE_CHECK_MAX_ENTRIES`。

## 3. 调用示例

见 [API.md](API.md)。`evidence/live/` 留存了一次真实运行的全部
请求响应（正常 4 个场景 + 5 类异常）与服务日志，日志中
`run_id`/`request_id` 与响应一一对应。

## 4. 夹具说明

```
fixtures/
  delete-recreate/   layer2 用 .wh.config.txt 删除后又重建同名文件
  opaque-dir/        layer2 用 .wh..wh..opq 隐藏 data/ 的低层子项
  file-dir-swap/     x 由目录变文件、y 由文件变目录（跨层）
  malicious/         越根链接、绝对链接、链接环、悬空链接、合法相对链接
  outside/           哨兵目录，归并后必须保持原样
```

每个场景目录下的 `expected.json` 是手工书写的参考答案；测试断言
最终树路径集合、每项类型/来源层/大小/内容哈希/链接状态，以及失败
与不确定诊断的（类别, 路径）集合完全一致。
