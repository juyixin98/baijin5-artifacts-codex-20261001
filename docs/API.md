# API 说明

所有响应均为 JSON。错误响应统一为信封：

```json
{ "error": { "code": "layer_outside_roots", "message": "...", "request_id": "demo-req-900" } }
```

`request_id` 在提供时回显（请求体 `request_id` 字段优先，其次
`X-Request-Id` 头），并出现在服务日志中，三方可互相对照。

## GET /v1/health

```bash
curl -s http://127.0.0.1:8080/v1/health
# {"status":"ok"}
```

## GET /v1/version

```bash
curl -s http://127.0.0.1:8080/v1/version
# {"engine_version":"0.1.0","report_schema_version":1}
```

## POST /v1/merges

执行一次归并。层按数组顺序自低向高应用；每层可以是路径字符串，
也可以是 `{"id": ..., "path": ...}`（id 用于来源标注与日志）。

```bash
curl -s -X POST http://127.0.0.1:8080/v1/merges \
  -H 'content-type: application/json' \
  -H 'x-request-id: demo-req-001' \
  -d '{
    "layers": [
      {"id": "base",   "path": "fixtures/delete-recreate/layer1"},
      {"id": "update", "path": "fixtures/delete-recreate/layer2"}
    ]
  }'
```

201 响应（摘要；完整报告已持久化，可用 `run_id` 取回）：

```json
{
  "run_id": "88f65ab4-1a2e-4502-bf3d-05b9f7520d13",
  "request_id": "demo-req-001",
  "engine_version": "0.1.0",
  "entries": 2, "failures": 0, "uncertainties": 0,
  "stats": { "files": 1, "dirs": 1, "symlinks": 0, "total_file_bytes": 10,
             "entries_replaced": 0, "entries_removed_by_whiteout": 2,
             "entries_removed_by_opaque": 0 }
}
```

失败类别（HTTP 状态 + `error.code`）：

| 状态 | code | 含义 |
|---|---|---|
| 400 | `no_layers` | 请求未给出任何层 |
| 400 | `layer_unavailable` | 层目录不存在或不可读 |
| 403 | `layer_outside_roots` | 层路径解析后在配置的层根白名单之外 |
| 422 | `entry_limit_exceeded` | 扫描条目数超过 `max_entries` |
| 422 | `layer_io_error` | 扫描层时发生 I/O 错误 |

## GET /v1/merges

列出所有已持久化运行的摘要（run_id、request_id、完成时间、计数）。

## GET /v1/merges/{run_id}

完整报告：层处理记录（每层扫描数、白化数、不透明数）、最终树
（每项含 `source_layer`/`layer_id` 来源、文件含 `size`+`sha256`、
符号链接含 `link_target`+`link_status`）、分列的 `failures` 与
`uncertainties`、聚合统计、起止时间与引擎/schema 版本。

## GET /v1/merges/{run_id}/entries

最终树条目的数组形式。

## GET /v1/merges/{run_id}/diagnostics

只取诊断部分，失败与不确定结论分列。每条诊断含 `severity`、
`category`（机器可读词汇表：`escapes_root`、`absolute_target`、
`link_loop`、`unsupported_type`、`non_utf8_name`、`layer_unavailable`、
`inconsistent_layer`、`dangling_link`）、所属 `layer`、相关 `path`
和人类可读 `message`。

## 运行 id 相关错误

| 状态 | code | 含义 |
|---|---|---|
| 400 | `invalid_run_id` | id 字符集非法（防路径穿越） |
| 404 | `run_not_found` | 无此运行记录 |
