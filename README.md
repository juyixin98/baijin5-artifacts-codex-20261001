# 矩形非重叠约束内核（带可选存在标记）

整数域上的矩形装箱/非重叠约束求解内核。矩形存在三态：

- `TRUE` 必存在：位置变量参与全部约束；
- `FALSE` 必不存在：从所有约束中移除；
- `UNKNOWN` 未决定：存在性本身是一个决策变量，**传播阶段绝不按“必存在”做强剪枝**。

仅使用 Java 21、Maven、标准库；测试依赖只有 JUnit 5（test 作用域）。无容器、无外部服务、无真实业务数据，全部夹具为本地合成。

## 1. 语义约定（整数域）

- 网格 `W x H`，矩形宽高 `w,h` 均为非负整数，锚点 `(x,y)` 为左下角整数坐标。
- 占据区间使用**半开区间**：`[x,x+w) × [y,y+h)`。因此两矩形在某方向**边界相接**（如 `x2 == x1+w1`）是合法的非重叠；只有内部面积严格相交才算重叠。
- 正面积矩形的锚点域：`0 ≤ x ≤ W-w`，`0 ≤ y ≤ H-h`，包含贴边点。
- **退化矩形**：`w=0` 或 `h=0` 的矩形面积为 0，在任何锚点都不与任何矩形重叠（多条退化矩形可以共点/共线）。退化维的锚点按整数边界点延拓：该维可取 `0..网格边界`（0 宽时 `0≤x≤W`，0 高时 `0≤y≤H`）。例如 3×1 网格上的 0×0 点有 `(W+1)*(H+1)=4*2=8` 个锚点，1×0 水平线段有 `W-1+1` 个 x、`H+1=2` 个 y。
- 未决定存在的矩形与必存在矩形之间**不产生弧修订**：把它设为 FALSE 始终是一个可行分支。内核在支持判定入口对 `UNKNOWN` 保留显式防线（误调用直接抛 `IllegalStateException`）。
- 决策顺序：先对“已必存在但仍有多个位置”的矩形按最小域优先做位置分支；没有位置变量后，再对 `UNKNOWN` 矩形按 `TRUE → FALSE` 分支。每个分支复制独立状态，回溯即丢弃副本。
- 传播：面向二元非重叠约束的 AC-3 弧一致性。`i` 相对 `j` 的候选值得到支持，当且仅当 `j` 必存在且其域中存在一个内部不相交（允许相接、退化矩形恒不相交）的位置。

## 2. 模块边界

| 包 | 职责 |
|---|---|
| `model` | 约束模型：`Existence` 三态、`RectDef`、`Instance`（静态校验）、`Placement`（整数锚点 long 打包）、`Geometry`（半开区间重叠判定） |
| `kernel` | 传播内核与搜索调度：`SearchState`、`Propagation`（AC-3）、`Solver`（DFS + 回溯 + 预算）、`Budget`/`Stats`/`Conflict`、密封结果 `KernelOutcome` |
| `api` | 对外契约与错误分类：`Service`（唯一服务入口）、`Status`、`FailureKind`、`SolveResult`、`Solution`/`PlacedRect`、`SearchStats`、`Trace`/`RunLog`（可重放运行日志） |
| `io` | 数据契约：`TextInstanceParser`（带行号的错误）、`GridText`（ASCII 渲染） |
| `cli` | 可运行服务入口 `Main`（`java -jar`） |

测试侧独立证据（不被被测代码生成答案）：

- `oracle/BruteOracle`：从零实现的暴力枚举器，不 import 任何 `kernel` 求解类，直接枚举存在性与全部整数锚点并独立判定重叠；
- `support/CaseFactory`：人工夹具 + 固定种子随机夹具（数据生成只用 Java）；
- `kernel/CrossCheckTest`：内核枚举集合与独立 oracle 做精确集合相等比对，并独立复核每个见证（贴界、网格边界、两两不重叠）。

## 3. 实例文本格式

```text
# 注释行
name <名称>
grid <W> <H>
rect <id> <w> <h> <TRUE|FALSE|UNKNOWN>
at   <id> <x> <y>        # 可选，可重复：单条=强制锚点，多条=限定候选锚点集合
```

存在性别名：`TRUE/T/REQUIRED/1`、`FALSE/F/ABSENT/0`、`UNKNOWN/U/OPTIONAL/?`。

`examples/` 下提供 `touch`、`unknown-safe`、`four-directions`、`required-conflict`、`all-diff`、`all-diff-unsat`、`zero-area`、`malformed`。

## 4. 错误语义（四类必须可区分）

`Status` 与 CLI 退出码：

| 类别 | Status / FailureKind | 含义 | CLI 退出码 |
|---|---|---|---|
| 正常答案 | `SAT` | 找到见证（单解或枚举） | 0 |
| 正常答案 | `UNSAT` | 搜索证明无解（声明式不可满足，根节点 wipeout 也算） | 10 |
| 输入错误 | `INVALID_INPUT` / `INPUT` | 文件缺失/格式错（带行号）、尺寸为负、网格非正、id 重复/未知、越界预指派、非法参数（如 `maxNodes<1`） | 20 |
| 状态冲突 | `STATE_CONFLICT` / `STATE` | 外部强加状态本身矛盾（`at` 预指派导致根域为空或固定点立即重叠），没有任何需要搜索的选择 | 30 |
| 资源耗尽 | `RESOURCE_EXHAUSTED` / `RESOURCE` | 节点预算 `--max-nodes` 或二元检查预算 `--max-checks` 用尽，尚未证明 SAT/UNSAT | 40 |
| 计算失败 | `COMPUTATION_FAILED` / `COMPUTATION` | 非预期内部异常，或运行日志目录不可写 | 50 |

区分要点：`UNSAT` 是对声明模型的正常结论；`STATE_CONFLICT` 仅在输入携带外部强加部分状态（非空 `at`）且根状态已矛盾时出现。

## 5. 运行与复现

需要 JDK 21 与 Maven（`/usr/bin/mvn`），直接本机原生进程运行：

```bash
# 一键演示（构建 + 全部夹具，打印状态、统计、ASCII 网格、退出码）
./scripts/demo.sh

# 完整验证（43 个 JUnit 测试 + 退出码冒烟）
./scripts/verify.sh
# 等价手工命令
mvn test

# 单次运行
mvn -q -DskipTests package
java -jar target/rect-nonoverlap-kernel-1.0.0.jar examples/four-directions.txt --all 10
java -jar target/rect-nonoverlap-kernel-1.0.0.jar examples/all-diff-unsat.txt
java -jar target/rect-nonoverlap-kernel-1.0.0.jar examples/four-directions.txt --max-nodes 1
```

参数：`--all <cap>` 枚举至多 cap 个解；`--max-nodes N` 节点预算；`--max-checks N` 二元检查预算（≤0 不限）；`--log-dir DIR` 日志目录（默认 `logs/`）。

## 6. 可重放日志

每次运行生成唯一 `runId`（时间戳-nano-UUID），日志文件 `logs/.../run-<instance>-<runId>.log` 为 TSV：`时间戳  阶段  理由  关键中间状态`，包含：

- `run/start`：runId、实例名、日志路径；
- `instance/loaded`：网格、每个矩形的尺寸与存在性；
- `propagate/queue-seeded|fixpoint`、`revise/pruned`（被删值数、剩余域大小、针对谁）；
- `branch/try-placement|try-existence`（候选编号/总数、存在性决定）；
- `conflict/empty-required-domain|domain-wipeout`、`backtrack/dead-end`（理由）；
- `limit/budget-exhausted`、`verify/witness`（网格渲染）、`result/*`。

用 `runId` 与其中的分支编号即可重放任意失败；测试 `ServiceTest` 会断言日志包含 runId、分支中间状态与结论。

## 7. 关键边界条件的测试对应

- 整数域、四方向相接合法、严格内部相交：`GeometryTest`、`InstanceTest`；
- 四方向全部被排除导致 wipeout：`PropagationTest.fourDirectionExclusionWipesTheDomain`；
- 退化矩形（0×0、1×0）不重叠且锚点语义明确：`GeometryTest.zeroArea...`、`SolverTest.zeroPoint...`、交叉校验中的 `zero-width-line`/`zero-point`；
- 未决定存在不得强剪枝：`PropagationTest.unknownRectangleNeverStronglyPrunes`（断言无 prune 事件且域不变）、`SolverTest.undecidedRectangle...`、`unknown-safe` 夹具；
- 回溯：`SolverTest.backtracksThroughBranchesAndProvesUnsat`、`ExampleFixtureTest` 的 all-diff / four-directions（断言 `backtracks>0` 与具体解数）；
- 四类失败可区分：`ServiceTest`、`TextInstanceParserTest`、`CliTest`（独立 JVM 子进程断言退出码）；
- 参考答案独立性：`CrossCheckTest`（curated + 60 个种子夹具，与 `BruteOracle` 精确集合相等）。
