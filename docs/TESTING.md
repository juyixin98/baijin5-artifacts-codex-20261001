# 测试与验证说明

本工程的测试分三层，全部在本机完成，**不连接任何真实 CoAP 设备**。

- **单元测试**：对每个有真实职责的模块独立断言具体结果与失败类别。
- **集成测试**：真实 UDP 回环 + 受控服务 + 可脚本化的损伤代理。
- **独立预言机**（`test/oracle`）：不导入被测核心，自行解析字节、自行重组、
  用硬编码 golden 哈希与另写的夹具公式判定。

## 运行命令

```bash
go test -race ./...                                   # 全部测试 + 竞态检测
go test -race -coverpkg=./internal/... -coverprofile=cov.out ./...
go tool cover -func=cov.out | tail -1                 # 总覆盖率
```

## 真实输出结论（本机 Go 1.22.2 实跑）

`go test -race ./...`：

```
ok  coaplab/internal/blocks
ok  coaplab/internal/config
ok  coaplab/internal/diag
ok  coaplab/internal/engine
ok  coaplab/internal/etag
ok  coaplab/internal/fixtures
ok  coaplab/internal/ids
ok  coaplab/internal/store
ok  coaplab/internal/transport
ok  coaplab/internal/wire
ok  coaplab/test/integration
```

- 测试函数总数：**103**；失败：**0**；`-race` 无数据竞争。
- 聚合语句覆盖率（集成测试计入内部包）：**total 86.4%**（≥80% 目标）。

## 断言了什么（不是“接口能调用”）

### 消息层
- 3 个相同 `(端点,MID)` CON → 3 个字节相同的响应，但处理器**恰好执行 1 次**。
- 丢首个 ACK → 客户端以**同一 MID** 重传；处理器仍只执行 1 次。
- 全部 ACK 丢弃 → 恰好 `MAX_RETRANSMIT+1` 份、MID 恒定、Token 恒定，随后超时。
- 背载响应 Token 不符 → 请求层拒绝；RST → `ErrRST` 且不重试。
- 畸形报文 → 静默丢弃并打 `parse_error`，服务仍能继续服务正常流量。

### Block1（上传）
- 300B/16B 严格连续上传，中间块 `2.31`、末块 `2.04/2.01`，**commit 恰好 1 次**。
- 块 0 后跳块 2、从块 5 起传：`4.08`，类别 `gap`/`request_incomplete`。
- 重复块字节相同 → `IGNORE` 幂等重放，不二次 commit；字节不同 →
  `4.09`，`duplicate_mismatch`。
- 末块重传（新 MID 通过消息层、同一端点）→ 重放最终码，版本不前进。
- 块大小协商：首块 1024(SZX6)、服务器反提议 32(SZX1)，后续 NUM 按字节偏移
  重新编号（下一块 NUM=4），字节仍完整。
- Content-Format 漂移拒绝；保留期过期 → `4.08 exchange_lifetime`；
  超体 → `4.13` 并带更小 SZX。
- RFC 7959 **图 7 / 图 9** 逐字节兼容向量（128B 原子 PUT；128→32 协商 PUT）。

### Block2（下载）
- 顺序/乱序（0、尾块、中间块）取回，独立预言机在有缺口时判定“未完成”，
  补齐后拼出与公式一致的字节（SHA-256 比对）。
- 块 0 绑定 ETag；中途表示更新（ETag 变）→ 客户端 `TransferError`
  类别 `etag_changed`，绝不跨版本拼接；预言机独立给出相同拒绝。
- SZX 在块 0 收敛后再变 → `bad_block_size`；重复失配 → `duplicate_mismatch`。

### UDP 损伤代理场景（真实网络栈）
| 场景 | 注入方式 | 断言终态 |
|---|---|---|
| 丢失确认 | 丢弃首个 S2C 报文 | 同 MID 重传、处理器执行 1 次、GET 成功 |
| 全部 ACK 丢失 | 脚本服务器静默 | 发送份数 = 重传+1，MID 恒定，超时 |
| 乱序块（Block1） | 代理延迟块 0、块 1 先到 | `4.08 gap`，不留半成品 |
| 块大小变更 | 服务器反提议 + RFC 图 9 | 重新编号后字节一致 |
| 表示更新 | 代理钩子在第 3 块请求时改写资源 | `etag_changed`，不拼接 |
| 网络复制 | 复制每个上行 PUT | 处理器按唯一块数执行、commit 1 次、终态正确 |

## 端到端字节一致（系统工具独立佐证）

- `Hello, CoAP!` 下载 SHA-256 =
  `936ba7a8d914df425cf608ea5095b9ece845df46ab3c61d55ef6debcad7845c3`，
  与 `printf 'Hello, CoAP!' | sha256sum` 一致，也与预言机内硬编码 golden 一致。
- 5000 字节文件经 Block1 上传（1024 协商）再 Block2 下载，
  `sha256sum 原文件` 与下载表示 SHA-256 完全相同。

## 为什么参考答案不是“自己考自己”

`test/oracle`：

1. 不 import `internal/blocks`、`internal/engine`、`internal/transport`；
2. 用自己的报文解析器（独立读首字节/扩展 delta/选项）；
3. 用自己的重组装器按 NUM 拼块、独立比对 ETag；
4. `cd/*` 的期望正文由包内**另写一份**的
   `SHA256("coaplab/<path>:<counter>")` 公式生成；
5. `hello` 期望值是用系统 `sha256sum` 预先算出后硬编码的常量。

被测核心与预言机同时出错却互相印证的概率因此被排除。
