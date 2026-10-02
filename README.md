# simdc — 标量条件循环 → 受限 SIMD 掩码 IR

纯 Go 1.22 + 标准库实现的后端工程：把“带标量条件的计数循环”前端程序
lower 成**每条内存访问和除法都带显式谓词**的受限 SIMD 掩码 IR，分别用
SIMD 解释器与**独立标量参考执行器**运行，再做逐通道语义差分。

## 模块职责（真实分层，非单文件脚本）

| 包 | 职责 |
| --- | --- |
| `internal/frontend` | 词法/语法/语义检查，产出 AST 与声明信息 |
| `internal/ir` | 受限掩码 IR 的指令定义与**掩码安全结构校验** |
| `internal/transform` | 循环界求解、if-转换、谓词派生、lowering；三态判定 |
| `internal/runtime` | 掩码 IR 解释器：屏蔽陷阱、尾掩码、故障前缀提交、升序归约 |
| `internal/reference` | **独立**标量参考：直接遍历 AST，短路求值；不共享 SIMD 执行代码 |
| `internal/sem` | 两端共享的纯结果/故障类型 |
| `internal/diag` | 带 request id 与关键状态的诊断、逐通道差分、脱敏 |
| `internal/pipeline` | 请求级编排（前端→变换→双路执行→差分） |
| `cmd/simdc` | CLI：读取请求 JSON，输出判定/IR 摘要/双路结果/差分 |
| `cmd/genfixtures` | 本地合成夹具生成器（期望值由内嵌独立手算 oracle 给） |
| `tests` | 集成/黄金/故障类别/脱敏/随机差分测试 |

测试与夹具独立组织于 `tests/`、`testdata/generated/`、`requests/`。

## 依赖与版本

- Go 1.22（本机 `go1.22.2 linux/amd64`），`go.mod` 声明 `go 1.22`。
- **零第三方依赖**，仅标准库；`go.sum` 因此为空/无需外部模块下载。
- 无容器、无额外语言运行时；脚本仅 Shell。

## 从干净目录复现

```sh
# 1) （可选）重新生成本地合成夹具；仓库已提交生成结果
./scripts/gen_fixtures.sh

# 2) 构建 + vet + 全部测试（单元 + 集成）
./scripts/test.sh

# 3) 运行一个请求样例（含被屏蔽的除零与非整批尾批）
./scripts/run_example.sh
# 或： go run ./cmd/simdc -request requests/example_masked_div.json
```

CLI 退出码：`0` 接受且差分一致；`1` REJECT；`2` 接受但差分不一致；
`3` UNDETERMINED；`4` 请求/IO 错误。

## 请求样例

见 `requests/example_masked_div.json`。字段：`request_id`、`source`、
`simd_width`、`scalars`、`arrays`。样例中长度 5、宽度 4：
- 第 1 通道 `d[1]=0` 但 `t[1]=0` → 条件为假，**被屏蔽，不除零**；
- 最后一批只有 1 个有效通道 → 由 `VMASKTAIL` 显式屏蔽；
- 期望输出 `[10,0,3,4,2]`，无故障，`diff.match=true`。

## 归约顺序（明确声明）

`LEFT_FOLD_ASCENDING_INDEX`：body 全部成功后，按索引升序左折叠。
顺序敏感原语 `concat=` 对 `[1,2,3,4,5]` 得 `12345`（反向会得 `54321`），
测试据此断言顺序；`+=`→15，`*=`→120。

## 判定三态与失败类别

- `ACCEPT`：成功 lowering 且结构校验通过（掩码纪律成立）。
- `REJECT`：`FRONTEND_SYNTAX`、`FRONTEND_SEMANTIC`、`UNSUPPORTED_SHAPE`、
  `BAD_SIMD_WIDTH`、`IR_MASK_UNSAFE`。
- `UNDETERMINED`：`BOUND_UNRESOLVED`（如上界是请求才给的标量）。
- 运行故障类别：`DIV_BY_ZERO`、`INDEX_OUT_OF_BOUNDS`（读/归约）、
  `OUTPUT_INDEX_OUT_OF_BOUNDS`（写），带全局索引、语句序与阶段
  （`BODY`/`REDUCE`）。

## 诊断与脱敏

每个事件带 `request_id`、`stage`、判定与 `key_state`（宽度、**仅数组长度**、
标量键名、trip count、故障摘要、不一致通道数），说明为何接受/拒绝/无法
判定。数组载荷值**绝不**进入诊断输出（`diag.RedactArrays` 只保留长度）；
测试 `TestDiagnosticsAreCorrelatedAndRedacted` 用秘密载荷断言不泄漏。

## 测试断言了什么（非“接口能调用”）

- 逐元素具体结果与独立参考一致（尾批、屏蔽除零、屏蔽越界 gather/写）。
- 活动通道陷阱的**具体类别 + 全局索引 + 阶段**及故障前缀提交。
- 归约具体数值（含顺序敏感 concat）与升序规则。
- 短路 `&&` 下被屏蔽的除零不发生；宽度 1..8 扫描验证尾掩码。
- REJECT/UNDETERMINED 的具体 issue code；提供标量后可转为 ACCEPT。
- 固定种子 60 组随机差分；诊断 request id 关联与载荷脱敏。
- 参考独立性：`internal/reference` 不导入 `internal/runtime`/`internal/ir`；
  黄金夹具期望来自 `cmd/genfixtures` 内嵌的独立手算 oracle，
  而非被测 SIMD 核心生成。
