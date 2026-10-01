# 真实运行结果（live verification）

服务以生产代码启动（非 mock）：

```bash
PORT=3100 DATA_DIR=.data/live TEMP_DIR=.data/live/tmp \
  RUN_LOG=.data/live/runs.jsonl node --import tsx src/index.ts
```

## 正常与异常场景（真实 HTTP）

| # | 场景 | HTTP | 判定 errorClass / code |
|---|------|------|------------------------|
| 1 | 正常上传：1 字段 + 1 文本文件（curl `-F`） | 201 | committed |
| 2 | 正常上传：7 字节跨块分片（`examples/upload-client.mjs`） | 201 | committed，totalSize=34 |
| 3 | `Content-Type: application/json` | 400 | INPUT_ERROR / NOT_MULTIPART |
| 4 | 缺少结束边界（手工 body，无 `--b--`） | 400 | INPUT_ERROR / MISSING_TERMINATOR |
| 5 | 文件名 `../../etc/passwd` | 400 | INPUT_ERROR / FILENAME_REJECTED（reason=separator） |
| 6 | 字段 200000 字节（> 64 KiB） | 413 | RESOURCE_LIMIT / FIELD_TOO_LARGE |
| 7 | 文件 7,000,000 字节（> 5 MiB） | 413 | RESOURCE_LIMIT / PART_TOO_LARGE |
| 8 | 上传中途 `socket.destroy()` 取消 | 连接重置 | failed：STATE_CONFLICT / ALREADY_ABORTED |

## 关键不变量

- **提交可见性**：场景 4/5/6/7/8 后 `GET /submissions` 计数不增加——只有终止边界
  校验通过才落库（单 SQLite 事务）。
- **临时资源回收**：取消与全部失败后
  `find .data/live/tmp -mindepth | wc -l` → **0**；每个请求使用独立目录
  `${tempRoot}/${runId}/`，清理互不影响。
- **诊断可重放**：`.data/live/runs.jsonl` 每行一个运行记录，含 `runId`、
  相对毫秒时间戳的事件序列（envelope / part_begin / progress / boundary /
  commit|fail）。
  - 成功样本事件名：`envelope → part_begin → progress → boundary(terminal) → commit`
  - 取消样本：`envelope → part_begin → progress → fail`，
    `errorClass=STATE_CONFLICT, errorCode=ALREADY_ABORTED`
  - 可用 `GET /diagnostics/runs/:runId` 取回任意运行的完整中间状态。

## 自动化测试

`npm test` → **104 passed / 0 failed**，其中解析器测试对 5 个黄金向量遍历了
**1000+ 个二分段切分点**与多种 1–7 字节不规则分片。覆盖率
（`npm run test:coverage`）：行 97% / 分支 88% / 函数 95%。
