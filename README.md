# RLC — 受限语言模块：公开接口指纹与失效传播

一个用 **Go 1.22 + 标准库** 从零实现的最小但职责完整的增量编译/失效传播工具链。
语言、IR、解释器、指纹、语义差分、构建编排与诊断均为真实实现，无任何外部运行时依赖
（`go.mod` 零 require）。不使用容器；验证脚本仅用 Shell。

## 它解决什么问题

模块的“调用者何时必须重新编译”应当只由**公开接口契约**决定：

- **私有实现体改变**（普通函数体 / 私有常量值）：接口指纹不变，调用者**不**失效，
  只重建拥有该符号的模块。
- **公开类型/签名改变**：硬错误（使旧调用者无法成立）或沿依赖传播。
- **内联公开常量改变**：常量值在使用点被烘焙，作为**显式接口依赖**记录，
  精确传播到所有（含跨模块）烘焙它的函数。
- **泛型体改变**：泛型模板体会被单态化进调用方实例，因此模板体哈希被**显式记录为
  接口依赖**（与普通函数体不同），实例化调用者必须失效。
- **编译语义版本闸门**：旧语义版本产出的指纹**不允许跨版本复用**，命中即全量重建。

## 目录结构与模块职责

| 路径 | 职责 |
| --- | --- |
| `internal/frontend/` | 词法分析 + 递归下降解析 + AST（语言前端） |
| `internal/ir/` | 栈式字节码 IR、指令/程序定义、JSON 编解码 |
| `internal/lower/` | 语义分析/类型检查、常量折叠、跨模块解析、依赖收集、AST→IR 降级、泛型单态化、链接（IR 变换） |
| `internal/interp/` | 字节码运行解释器（算术/比较/字符串/分支/调用/运行时错误） |
| `internal/fp/` | 确定性公开接口指纹（SHA-256）、语义版本、显式依赖定点展开 |
| `internal/semdiff/` | 语义差分：变更分类 + 基于外部探针夹具的行为差分 |
| `internal/build/` | 编排：解析→分析→指纹→降级→缓存→最小失效集；旧版本/损坏缓存拒绝 |
| `internal/config/` | 项目配置加载（JSON，纯数据） |
| `internal/diag/` | 结构化诊断：请求/记录标识、关键状态、接受/拒绝/无法判定、脱敏 |
| `cmd/rlc/` | 驱动 CLI：`build` / `run` / `fp` / `diff` / `probe` |
| `cmd/evsum/` | 读取构建报告并打印关键失效字段（供 Shell 验证脚本使用） |
| `tests/` | **独立**黑盒测试包 `rlc_test`：在临时目录建合成工程，断言具体数值、失败类别与失效集，并独立用 `crypto/sha256` 交叉校验指纹 |
| `config/rlc.json` | 示例工程配置 |
| `examples/shop/` | 本地合成多模块示例（pricing/report/app） |
| `testdata/probes.json` | 行为探针夹具，期望值**手工编写**，不由被测核心生成 |
| `scripts/verify.sh` | 一条命令跑全量构建 + 三类改动对照 + 探针，证据落 `evidence/` |

## 语言一览（受限语言）

```
module pricing;
import report;

pub const BULK_FACTOR: int = 2;     // 公开内联常量 => 显式接口依赖
const BASE: int = 50;               // 私有常量 => 仅实现

fn private_adjust(x: int) -> int { return x + BASE; }

pub fn unit_price(cents: int) -> int {
    if (cents < 1) { return BASE; } else { return private_adjust(cents); }
}

pub generic fn first(x: T) -> T { return x; }   // 泛型：体即接口依赖

pub fn quote() -> int { return pricing::bulk_price(10, 2) + report::K; }
```

类型：`int`（64 位）、`string`、`bool`；运算符 `+ - * / % < <= > >= == !=`；
泛型仅支持单个类型变量 `T`，在调用点按实参**单态化**（`report.first<int>`）。
跨模块用 `模块::符号`，未限定名先匹配本模块再在 `import` 中唯一定位公开符号。

## 快速开始

```bash
go test ./...                  # 全部单元 + 独立黑盒测试
./scripts/verify.sh            # 端到端证据（全量 vs 三种最小失效），输出到 evidence/

# 手动调用
go run ./cmd/rlc build -config config/rlc.json     # 增量构建，打印报告
go run ./cmd/rlc run   -config config/rlc.json -int 3      # 362
go run ./cmd/rlc fp    -config config/rlc.json -symbol pricing.BULK_FACTOR
go run ./cmd/rlc probe -config config/rlc.json -fixtures testdata/probes.json
```

两个指纹集合之间做语义差分：

```bash
go run ./cmd/rlc fp -config config/rlc.json > /tmp/before.json
# 修改某个源文件
go run ./cmd/rlc fp -config config/rlc.json > /tmp/after.json
go run ./cmd/rlc diff -before /tmp/before.json -after /tmp/after.json
```

## 诊断

诊断写入 stderr，每行携带：时间、判定（`accepted|rejected|undecidable`）、
`req=<请求标识>`、`mod=<模块>`、`sym=<符号>`、`cat=<失败类别>` 与关键状态。
失败类别包括 `lex_error / parse_error / type_error / fingerprint_changed /
stale_semantic_version / cache_corrupt / runtime_error` 等。
配置 `redact_literals:true` 后，探针输出的字符串字面量只打印脱敏值
（保留前 2 字符与长度），例如 `"SE"***(len=23)`。

## 缓存与语义版本

缓存位于配置的 `cache_dir/cache.json`，含语义版本、源码摘要、指纹集合与链接后 IR。
加载时：

- 语义版本不一致（`rlc-sem-N`）→ 拒绝旧指纹，`stale_semantic_version`，全量重建；
- JSON 损坏 → `cache_corrupt`，全量重建；
- 命中 → 比较新旧指纹，计算直接变更分类与经显式依赖传播后的最小失效集。

详见 `DESIGN.md`。
