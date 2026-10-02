# 有限简单图的最小顶点着色（含上下界证明）

Java 21 + Maven 工程。对有限**简单无向图**计算最小顶点着色数 χ（色数），
并同时维护：

- **下界**：团证书（两两相邻的顶点必须使用不同颜色，故 χ ≥ ω）。
- **上界**：一个可独立复核的正常着色（proper coloring）证书。

当二者相等时给出可证最优的 χ；若在预算内无法闭合，则只报告已证明的区间
`[lowerBound, upperBound]`，绝不臆断精确值。

## 模块职责（真实分层，非单文件脚本）

| 包 | 职责 |
| --- | --- |
| `graph` | 图契约：不可变简单图、构造期校验（自环/越界/空标签）、连通分量、诱导子图、本地合成夹具工厂与 `.gfc` 加载器。 |
| `cert` | 上界证书：`Coloring`，可用任意图独立验证 `isProper`。 |
| `bounds` | 界：DSATUR 贪心得可行上界；贪心团与带枢轴 Bron–Kerbosch 最大团得下界见证。 |
| `solver` | 核心搜索：按连通分量分解的分支限界；动态 DSATUR 选点；颜色重命名对称剪枝；预算停止；`ChromaticResult` 只在可证时给出精确值。 |
| `diag` | 诊断：请求标识、决策类型（接受/拒绝/无法判定）、机器可读理由、关键状态与脱敏渲染。 |
| `ref` | 穷举对照（与被测求解器**无共享搜索逻辑**）：暴力赋色计数、有序划分/Stirling 计数、删边-缩边色多项式（BigInteger）。 |
| `demo` | 可运行示例：一个最优案例 + 一个预算停止案例。 |

测试与配置独立组织在 `src/test`，夹具在 `src/test/resources/fixtures`。

## 行为约定与对应实现

1. **颜色重命名对称剪枝不删不同分区**：搜索对“尚未使用的新颜色”只放置规范标签
   `used`；大于 `used` 的未用标签视为同一规范分区的重命名而剪枝
   （理由 `SYMMETRY_NEW_COLOR_UNUSED`）。该规则的数学表述
   `properColorings(G,r) = Σ_k properPartitions(G,k)·(r)_k` 由独立测试对所有 n≤5
   图及命名图逐图核对，确保剪枝只去重标签、不丢分区。
2. **团下界与当前可行上界分别维护**：`CliqueCertificate` 与 `Coloring` 是两个独立、
   可分别复核的对象；搜索中二者独立更新。
3. **预算停止只给已证界**：超节点/时间预算时返回 `BOUNDS_PROVEN`，下界为团见证、
   上界为当前真实着色，区间包含真实 χ，并发出 `UNDETERMINED_BUDGET` 事件；
   `chromaticNumber()` 在未证最优时抛异常。

## 剪枝/接受理由（`diag.Reason`）

- 接受：`BRANCH_EXPLORED`、`FEASIBLE_COMPLETE`、`LOWER_BOUND_MATCHES_UPPER`。
- 拒绝：`ADJACENT_SAME_COLOR`（邻居同色冲突）、`LOWER_BOUND_NOT_IMPROVABLE`
  （已用不同颜色数不可能优于当前最优）、`SYMMETRY_NEW_COLOR_UNUSED`（重命名对称）。
- 无法判定：`NODE_BUDGET_EXHAUSTED`、`TIME_BUDGET_EXHAUSTED`。

每条 `SearchEvent` 都带 `requestId`、序列号、深度、已用颜色数、当前上界、已访问
节点数和团见证；渲染时可对敏感顶点标签做确定性不可逆指纹（`h#....`），不打印明文。

## 边界语义

- 空图（0 个顶点）：χ = 0，空着色为合法证书，最大团大小为 0。
- 无边图 E_n：χ = 1。
- 完全图 K_n：χ = n，进入搜索前即由“团下界 == 上界”关闭。
- 奇环 C_{2k+1}：χ = 3，团下界为 2（展示真正的回溯闭合）。
- 断连图：按连通分量分别求解；χ(不相交并) = max χ(分量)，分量间颜色标签可复用；
  仅当所有分量都证最优时整体才标记最优。
- 非法输入：负顶点数、自环、越界顶点、null 标签分别抛带 `Violation` 分类的
   `GraphContractException`，测试断言**具体失败类别**。

## 独立测试如何避免“参考答案自证”

- `BruteForceReference`：直接枚举每个赋色（无对称剪枝、无界），是显然正确的规格。
- `SetPartitionReference`：独立集合划分递归，χ 定义为存在独立集划分的最小 k。
- `ChromaticPolynomial`：删边-缩边 + BigInteger 系数与记忆化。

三者互相交叉验证，并与求解器对照；测试断言具体数值（如 K3 在 3 色调色板恰有 6 个
正常着色、C5 为 30、P=C5: (t−1)^5−(t−1)），不只是“接口可调用”。

## 运行 / 验证

本机原生 Linux + OpenJDK 21 + Maven，无需容器：

```bash
mvn -B test          # 全量 JUnit 5 测试
./verify.sh          # clean + test + 打包 + 运行 demo
java -cp target/classes com.example.coloring.demo.ColoringDemo
```

固定版本：JUnit Jupiter `5.10.2`（test 作用域）、maven-compiler-plugin `3.13.0`、
maven-surefire-plugin `3.2.5`。生产代码零外部依赖（仅 JDK + `java.math.BigInteger`）。

## 数据与夹具

全部为本地合成：`Graphs` 工厂（空/无边/完全/环/路/不相交并/Petersen/固定种子
G(n,p)）与手编 `src/test/resources/fixtures/graphs.gfc`。不访问任何真实业务数据。

## 未实现 / 未执行的检查（如实列出）

- 未实现大规模图（n ≫ 30）的高级下界（如独立集覆盖、分数松弛）与多线程并行搜索；
  当前为单线程精确搜索，定位在可穷举复核的小/中图规模。
- `verify.sh` 未在 Windows PowerShell 上实际执行（本机为 Linux）；脚本本身只用
  POSIX shell 与 Maven/Java，Windows 上可直接运行等效的 `mvn -B test` 与
  `java -cp target/classes ...` 命令。
- 时间预算（毫秒）分支由单元测试覆盖其“显式无法判定”语义，但墙钟时间不确定，
  未对其触发做严格时序断言；节点预算分支有确定性断言。
