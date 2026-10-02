# 证据留存

- `cargo-test-run.txt` — `cargo test --locked` 完整输出（31 个测试全部通过：
  18 单元 + 7 API 端到端 + 6 语义集成）。
- `live/` — 一次真实服务运行的留存：
  - `server.log` — 服务日志，每条归并日志含 `run_id` 与 `request_id`，
    与响应文件一一对应；
  - `01`–`09` — 正常调用：四个场景（删除后重建、不透明目录、文件/目录
    互换、恶意链接）的创建响应、完整报告、条目列表、诊断、运行列表；
  - `10`–`14` — 异常调用：越根层（403 `layer_outside_roots`）、缺失层
    （400 `layer_unavailable`）、空层列表（400 `no_layers`）、未知运行
    （404 `run_not_found`）、非法 run id（400 `invalid_run_id`）。

运行后已核验 `fixtures/outside/sentinel.txt` 的 SHA-256
（`a72ec0aaafd425d5075ca8ee9b828cd106f11abb1f68789bd4539263af43037c`）
与归并前一致；`tests/merge_semantics.rs` 中的
`merge_does_not_modify_layers_or_outside_dirs` 对全部夹具做同样的
逐字节 + mtime 断言。
