# mmap-model

受限共享与私有映射的页面模型（纯内存模型，不改系统内核），带 Axum 诊断接口。

- **共享映射**：写入进入共享页缓存并置脏，其他共享映射立即可见；`sync` 回写持久镜像。
- **私有映射**：写入触发 COW，永不回写底层文件。
- **截断**：整页超出 EOF 的访问返回 `access_out_of_range`（SIGBUS 类比），
  **绝不**用伪造零填充替代非法访问；部分页尾部规则见 `docs/semantics.md`。
- **同步失败**：失败页保留脏标记，错误类别 `sync_failed`，可重试。

## 结构

```
src/
  config.rs      配置层（TOML + 环境变量，校验）
  error.rs       稳定错误类别（access_out_of_range / sync_failed / ...）
  telemetry.rs   tracing 初始化、run id、版本
  store/         持久镜像：BackingStore trait + MemStore / FsStore / FaultyStore（故障注入夹具）
  model/         运行模型：FileObject（页缓存）、Mapping（COW）、Vm（fault/sync/truncate/unmap）
  diag/          Axum 诊断接口（router + handlers + DTO）
  main.rs        服务器二进制
tests/
  common/        可复用夹具：TestRun 结构化日志、oracle 辅助
  interleaved_writes.rs   两个映射交错写：COW 分离、脏页集合、持久镜像
  cross_page_truncate.rs  跨页截断：SIGBUS 类比、部分页、扩大后可见性
  sync_failure.rs         同步故障：脏标记保留、重试、flush 失败、私有 sync
  unmap_semantics.rs      解除映射：共享脏页驻留、私有页销毁
  api_end_to_end.rs       HTTP 端到端（含注入故障）
docs/semantics.md  边界语义与“未执行检查”清单
config/default.toml  示例配置
scripts/verify.sh    一键验证（fmt/clippy/test/release/HTTP 冒烟）
```

## 运行

```bash
cargo run -- config/default.toml          # 或 MMAP_MODEL_BIND=... MMAP_MODEL_STORAGE_DIR=... cargo run
```

## 验证

```bash
scripts/verify.sh
```

测试日志带测试名、run id、crate 版本、步骤编号与“期望值 vs 实际值”判定依据，
随 `cargo test -- --nocapture` 输出并保存到 `target/test-logs.txt`。

## 诊断 API（摘要）

| 方法/路径 | 说明 |
|---|---|
| `GET /healthz` `GET /version` `GET /stats` | 存活、版本+run id、累计计数器 |
| `POST /files` `{path,size}` | 创建文件（零填充） |
| `POST /files/open` `{path}` | 打开已存在文件 |
| `GET /files/state?path=` | 页缓存状态：脏页集合、各页有效字节 |
| `GET /files/content?path=&offset=&length=` | 直接读持久镜像（绕过缓存） |
| `POST /files/write` `{path,offset,data_hex}` | 直接写持久镜像（外部写入者夹具） |
| `POST /files/truncate` `{path,size}` | 截断，返回丢弃页/清零尾部报告 |
| `POST /mappings` `{path,offset,length,kind}` | 建立映射（`shared`/`private`） |
| `POST /mappings/{id}/read` `{offset,length}` | 读，返回 hex 或错误类别 |
| `POST /mappings/{id}/write` `{offset,data_hex}` | 写 |
| `POST /mappings/{id}/sync` | 回写脏页；失败返回 `sync_failed` + `failed_pages` |
| `GET /mappings/{id}/state` `DELETE /mappings/{id}` | 映射状态 / 解除映射 |

错误统一为 `{"error":{"category","message","failed_pages?"}}`，HTTP 状态码：
`invalid_argument`→400，`not_found`→404，`access_out_of_range`/`conflict`→409，
`sync_failed`→500，`store_unavailable`→503。

## 依赖

全部固定在 `Cargo.toml`（`=` 精确版本）并锁定于 `Cargo.lock`。
