# 可解释日志与失败分类

服务使用 Go 1.21+ 标准库 `log/slog` 输出 JSON。每条与 HPACK 相关的日志
都带三个关联标识：

- `conn_id`：一个 TCP/TLS 连接的稳定 ID（如 `conn-12-3f9a2c10`）。
- `request_id`：一个完整 HEADERS/CONTINUATION 序列（一个头块）的 ID。
- `stream_id`：该块所在的 HTTP/2 流。

## 步骤事件（debug 级，来自 hpack.Observer）

每一步都发出一条 debug 记录，字段包括 `step`、`offset`、`table_size`、
`block_bytes`：

| `step` | 含义 | 额外字段 |
|---|---|---|
| `block-start` | 开始解码一个头块 | — |
| `dynamic-table-size-update` | §6.3 表大小更新 | `old_max`, `new_max`, `evicted` |
| `indexed` | §6.1 索引表示 | `index` |
| `literal-incremental` | §6.2.1 增量索引字面值 | `name`（echo 时） |
| `literal-without-indexing` | 不索引字面值 | `name` |
| `literal-never-indexed` | 绝不索引（敏感） | `name`, `sensitive=true`, `value=<redacted>` |
| `eviction` | 字节成本驱逐 | `evicted`（条数） |
| `block-end` | 块解码完成 | — |

## 成功与失败（info/error 级）

成功：

```json
{"level":"INFO","msg":"decoded header block","conn_id":"conn-1-...",
 "request_id":"req-2-...","stream_id":1,"block_bytes":9,"field_count":4,
 "emitted_bytes":30,"status":"ok"}
```

失败（原因单列、并明确失步结论）：

```json
{"level":"ERROR","msg":"header block decode failed","request_id":"...",
 "stream_id":3,"block_bytes":1,"error_kind":"index-zero","error_detail":"...",
 "status":"error","fatal":"connection-closing; hpack state desynchronized"}
```

- **失败原因**通过稳定机器名 `error_kind` 单列（见下表），消息细节放
  `error_detail`。
- **不确定/致命结论**通过单独的 `fatal` 字段点明，避免与普通警告混淆。

`hpackd list` 会把失败显示为 `error/<kind>`（如 `error/index-zero`）。

## 错误类别（稳定名）

权威来源是 `modules/hpack/errors.go` 的 `Kind.String()`；测试直接对这些
字符串断言。

| 机器名 (`error_kind`) | 触发 |
|---|---|
| `index-zero` | §6.1 引用索引 0 |
| `index-out-of-range` | 索引超过静态+动态表范围 |
| `integer-truncated` | §5.1 整数续接字节缺失 |
| `integer-overflow` | 上界 `2^62-1` 被突破 |
| `string-truncated` | 字符串超过块尾 |
| `string-too-long` | 解压后单串超预算 |
| `huffman-invalid` | 未知前缀 / 流中 EOS / 错误填充 |
| `size-update-illegal-position` | 大小更新出现在字段之后 |
| `size-update-too-large` | 更新超过 SETTINGS 通告上限 |
| `header-list-too-large` | 单块总字节或条数超预算 |
| `block-truncated` | 块在一个完整表示中间结束 |
| `decoder-poisoned` | 前序块已失败仍继续使用 |
| `illegal-pseudo-header` | 伪首部出现在常规字段之后或重复 |

HTTP/2 帧层的连接级错误不走 HPACK Kind，而是通过 GOAWAY 错误码表达：
帧顺序/流号错误为 `PROTOCOL_ERROR(0x1)`，HPACK 解压失败为
`COMPRESSION_ERROR(0x9)`。
