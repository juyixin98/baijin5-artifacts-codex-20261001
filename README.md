# rlmod — 受限语言模块的公开接口指纹与失效传播

一个纯 Go（Go 1.22，仅标准库，Go Modules）实现的小型受限语言工具链，用来可重复地
回答两个问题：

1. 当实现/公开类型/内联常量/泛型体发生变化时，**谁必须重编，谁可以复用**？
2. 旧构建的指纹在什么条件下**不能跨编译语义版本复用**？

所有输入都是仓库内的本地合成夹具，无生产账号、无业务数据、无网络依赖（除 Go
模块代理；本项目实际上没有第三方依赖）。原生 Linux 进程运行，不使用容器。

## 快速开始

```bash
# 编译 + 全量测试 + 全部端到端证据（一条命令，约 1 秒）
bash scripts/verify.sh

# 或分步
go test ./...
go run ./cmd/rlverify verify --config config/rlverify.json
```

需要 Go 1.22+（本仓库在 go1.22.2 linux/amd64 验证）。无第三方依赖，
`go mod tidy` 不会引入额外模块。

## 示例调用

```bash
# 1) 查看一个构建的公开接口指纹
go run ./cmd/rlverify compile --dir testdata/scenarios/inline-const_old

# 2) 执行入口 Run，得到具体结果（常量在编译期折叠并内联）
go run ./cmd/rlverify run --dir testdata/scenarios/inline-const_old   # 405
go run ./cmd/rlverify run --dir testdata/scenarios/inline-const_new   # 445

# 3) 对比两次构建：变化分类 + 最小必要失效集
go run ./cmd/rlverify diff \
  --old testdata/scenarios/inline-const_old \
  --new testdata/scenarios/inline-const_new

# 4) 附 JSON 行诊断（带请求/记录标识与 accept/reject 原因）
go run ./cmd/rlverify diff \
  --old testdata/scenarios/inline-const_old \
  --new testdata/scenarios/inline-const_new \
  --diag --request-id demo-001

# 5) 语义版本门控：主版本升级 -> 旧指纹全部不得复用
go run ./cmd/rlverify diff \
  --old testdata/scenarios/version-old --new testdata/scenarios/version-new \
  --semver-old 1.9.0 --semver-new 2.0.0
```

一段最小 RL 程序（`testdata/smoke`）：

```text
package core
type Age int
const Base Age = 10
fn Add[A](x: A, y: A): A { return x + y }
fn Bump(n: int): int { if n > 0 { return Add[int](n, Base) } else { return n } }

package app
import core
fn Run(): int { return core::Bump(32) }   // => 42
```

## 四类受控变更的实际结论

| 场景 | 改动 | 变化分类 | 最小必要失效集 |
| --- | --- | --- | --- |
| private-body | 私有函数 `secret` 体 `+1` → `+2` | `private_body_only` | 仅 `core.secret`；`core.Calc`、`app.Run` 复用 |
| inline-const | 内联常量 `Offset` 100 → 110 | `inline_const` + 调用方 `public_body` | `core.Offset`、`core.Calc`、`app.Run`（沿内联链传递） |
| generic-body | 泛型体 `a*b` → `a*b+0` | `generic_body` | `core.Scale[int]`、`core.Calc`、`app.Run`（仅被使用的实例） |
| public-type | 公开类型/签名变更 | `removed`/`added`/`public_signature` 等 | 所有签名/类型相关单元 |
| version | 源码相同，semver 1.9.0 → 2.0.0 | 0 变化但门控拒绝 | 全部单元（`reused 0`，inconclusive） |

## 目录

```text
cmd/rlverify/          CLI: compile / run / diff / verify
internal/frontend/     词法 + 语法 + AST（语言前端）
internal/ir/           名称解析、常量折叠、类型检查、泛型单态化、IR 生成
internal/runtime/      栈式虚拟机（运行解释）
internal/fingerprint/  公开接口指纹（接口哈希 vs 函数体哈希）
internal/semdiff/      变化分类 + 依赖图 + 最小失效集 + 版本门控（语义差分）
internal/diag/         结构化诊断（请求/记录标识、脱敏）
internal/pipeline/     加载/编译/指纹/差分/执行编排 + 端到端测试
internal/config/       JSON 配置加载
config/rlverify.json   场景与夹具配置
testdata/scenarios/    受控变更夹具（old/new）与错误/敏感夹具
testdata/golden/       独立手写金样（期望值不由被测实现生成）
testdata/smoke/        最小冒烟程序
scripts/verify.sh      原生 Shell 验证脚本
docs/                  LANGUAGE.md（语言规范）、ARCHITECTURE.md（设计与失效模型）
```

## 测试与证据如何独立

- 逐模块单测断言**具体结果与失败类别**：前端位置信息、IR 的 `const_cycle`/
  `unknown_symbol`/`arity_mismatch`/`typearg_count` 等、解释器 `division_by_zero`、
  指纹稳定性/敏感性、semdiff 的精确失效集。
- 端到端测试加载真实夹具目录并对照 `testdata/golden/*.json`。金样是**手工编写**的
  期望（运行结果、变化类别、失效/复用集合、版本门控结论、错误类别、脱敏要求），
  不是由被测核心实现生成。
- `scripts/verify.sh` 通过 CLI 黑盒复验同样的断言（33 项），包括“输出不得包含
  敏感明文”“诊断必须带 request/record 标识”。

## 已锁定的关键点

- 无第三方依赖；`go.mod` 仅声明模块与 `go 1.22`。
- 指纹算法为 SHA-256 + 确定性规范化文本；哈希输入（接口字段、内联依赖、泛型体）
  在 `docs/ARCHITECTURE.md` 与 `internal/fingerprint` 包注释中显式记录。
- 指纹模式 `fp-v1` 与编译语义版本一起门控复用。

## 剩余限制（如实列出）

- 语言是教学用严格子集：只有 `int`/`str` 与名义别名；无浮点、布尔、集合、闭包、
  结构体、方法、循环；递归有 256 层调用深度上限。
- 泛型按“已使用实例”单态化；未被调用的泛型实例不会生成，跨实例不共享代码。
- 普通函数按名链接，跨模块增量构建只在本工具的“两次快照对比”模型内表达，没有产出
  目标文件或真实增量构建缓存。
- 没有持久化指纹仓库/时间戳；指纹来自当次全量编译的确定性计算。
