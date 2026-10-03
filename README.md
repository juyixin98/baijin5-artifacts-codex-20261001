# 有限简单图最小顶点着色与上下界证明（Java 21 / Maven / BigInteger）

对有限简单无向图求色数 χ(G)：分别维护**已证下界**与**可行上界**，用带颜色重命名对称剪枝的
DSATUR 分支限界判定 k-可着色性，并为每个结论附带可独立复核的证书。

## 运行与验证（本机原生，无容器）

```bash
mvn -B clean test          # 编译 + 全部 JUnit 5 测试（含 n<=6 全图穷举对照）
bash scripts/verify.sh     # 一键：构建/测试/打包/CLI 冒烟（奇环、完全图、断连图、预算停止）
bash scripts/run.sh cycle:5            # 单个 CLI 运行
bash scripts/run.sh cycle:9 1          # 1 个节点预算：必须只报已证界
bash scripts/run.sh file:src/test/resources/fixtures/grotzsch.graph --diag
```

`scripts/run.sh`、`scripts/verify.sh` 仅使用 Shell 与 Maven/JDK，不引入其他语言运行时。

## 模块职责（均有真实职责，非空接口工程）

| 包 | 文件 | 职责 |
|---|---|---|
| `model` | `Graph`, `SimpleGraph`, `Graphs`, `GraphFormatException` | 图契约：顶点 `0..n-1`、邻接表不可变、拒绝自环/越界、重复边幂等、常用图族/不相交并 |
| `bound` | `MaxClique` | 精确最大团（Tomita 枢轴 Bron–Kerbosch），团本身是下界见证：χ ≥ ω |
| `bound` | `GreedyColoring` | DSATUR 可行着色启发式；结果恒为真着色，颜色数恒为可行上界 |
| `search` | `KDecision` | 精确 k-可着色决策：DSATUR 选点 + 两类剪枝（无可用色、颜色重命名对称） |
| `search` | `ColoringSolver`, `ColoringResult`, `ColoringStatus` | 上下界分别维护、向下逐 k 搜索、BigInteger 节点预算、`OPTIMAL`/`STOPPED` |
| `certificate` | `CertificateVerifier`, `CertificateError`, `CertificateVerdict` | 不信任求解器，独立重算：着色逐边检查、团两两相邻、界序与最优声明 |
| `oracle` | `PartitionOracle`, `LabeledColoringCounter`, `GraphEnumerator` | **独立**穷举：RGS 集合划分、k^n 直接递归（两套 BigInteger 计数互校）、全有标图枚举 |
| `diag` | `DiagnosticEvent`, `DiagnosticSink`, `CollectingDiagnostics`, `PrintingSink` | 带请求标识/节点标识/关键状态的接受-拒绝诊断；打印时顶点 ID 用按请求加盐哈希脱敏 |
| `io` | `GraphText` | 本地合成夹具的小型文本格式（`n`/`e` 指令、注释、行号错误） |
| `demo` | `ColoringDemo` | CLI：`family:size` 或 `file:x.graph`、BigInteger 预算、`--diag` |

## 三条行为约定的落地

1. **颜色重命名对称剪枝不删不同分区**。规范 restricted-growth 规则：顶点可复用任意“已使用”颜色，
   或只引入“恰好下一个”新色；跳到更高的未使用色只是同一分区的换名，记
   `SYMMETRY_CANONICAL_SKIP`。不同分区（复用哪个旧色、是否开新块）始终分别探索。
   `SymmetryPartitionTest` 用第三套独立划分枚举核对：无边图能到达全部 Bell(n) 个分区，
   n≤3 每一张图的可达真分区数与预言机一致。
2. **团下界与当前可行上界分别维护**。`ColoringResult.lowerBound()` 来自团见证（可被后续不可行
   证明继续抬高），`upperBound()` 永远附带一个真着色；二者相等才 `OPTIMAL`。
3. **预算停止仅给已证界**。节点预算为 `BigInteger`，每个展开节点扣费；耗尽即 `STOPPED`，
   状态为 `lowerBound ≤ χ ≤ upperBound`，`chromatic()` 为空，绝不声称精确值；团下界与贪心
   上界不花预算，停止时证书仍可独立验证（`BudgetTest.zeroBudget...`、CLI `cycle:9 1`）。

## 诊断语义

每个事件含 `requestId`、单调 `nodeId`、`PruningReason`（为何接受/拒绝/无法判定）、目标 k、
剩余预算与关键状态（未着色数、已用色数，团事件另含 `cliqueSize`）。
`PrintingSink` 不打印原始顶点号，只打印按请求加盐的 FNV-1a 哈希（`v#…`）；
`DiagnosticsTest` 断言跨请求掩码不同且输出含接受/拒绝理由。

剪枝理由均可独立断言：`CLIQUE_LOWER_BOUND`（k<ω 不进搜索）、`NO_FEASIBLE_COLOR`
（C5 在 k=2 必被证不可行）、`SYMMETRY_CANONICAL_SKIP`（按节点精确计数）、
`FOUND_FEASIBLE`、`PROVED_INFEASIBLE`/`BUDGET_EXHAUSTED`。

## 边界语义

- n=0 的空图：χ = 0（0 色），空团见证；n≥1 的无边图：χ = 1。
- 奇环 χ=3，偶环 χ=2；完全图 χ=n；断连图 χ = 各连通分量最大值（对 `K3∪K2`、`C5∪K4` 有夹具断言）。
- 图仅限有限简单无向图：无自环、无重边、无向；构建期非法输入直接抛异常。
- `GreedyColoring` 的颜色掩码为 `long`，参考启发式支持到 **63 色**；精确搜索本身无此限制。
  `GraphEnumerator` 全枚举上限 n=62（`2^(n choose 2)` 适合 long 位掩码）；实际穷举测试跑 n≤6。
- 预算是“决策树展开节点数”，不含团/贪心预处理；下界团与上界着色在预算外先行取得。
- 大计数（Bell/Stirling、有标 k-着色数）全部使用 `BigInteger`。

## 已执行检查（本机实际运行通过）

- `mvn clean test`：**64 个测试全部通过**，覆盖图契约、界见证、精确值、剪枝理由、
  预算停止、证书各失败类别、诊断脱敏、IO 错误行号。
- 穷举对照：n=0..6 共 40962 张有标简单图，求解器精确值逐张等于独立划分预言机；
  两套独立 BigInteger 计数在 n≤5 全部 1100 张图上逐 k 相等。
- 夹具（`src/test/resources/fixtures/*.graph`，手写固定答案）：C4/C5/C9、K6、
  K3∪K2、C5∪K4、W5、W6、Petersen、Grötzsch（无三角形 χ=4）。
- CLI：奇环、完全图、断连图、`cycle:9 1` 预算停止、Grötzsch，证书全部 ACCEPTED。
- 固定版本：JUnit BOM 5.11.4、maven-compiler 3.13.0、surefire 3.5.2、jar 3.4.2；仅依赖 JDK 21。

## 未执行 / 未实现项（如实列出，不计入“已通过”）

- 未做大规模性能基准（n≥100 图的耗时画像、DSATUR/分支排序的统计对比）。
- 未在离线/受限网络环境复验依赖下载；依赖解析依赖 Maven Central 可达（本机已成功解析）。
- 未实现证书的防篡改签名/校验和——验证器能检出篡改并给出失败类别，但不提供密码学来源证明。
- PowerShell 版启动脚本未提供（本机为 Linux；脚本为 POSIX Shell）。
