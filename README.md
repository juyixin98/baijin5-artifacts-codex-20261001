# bk-pivot-clique

BronKerbosch maximal clique enumeration with pivoting.

Java 21 + Maven + BigInteger 位集实现。从空目录构建，全部数据为本地合成夹具，无外部服务依赖。

## 模块拆分

- 图契约 `clique.graph`（Graph / GraphBuilder / Bits）：无向简单图，BigInteger 邻接位集；构建期校验自环、越界顶点、非对称邻接，重复边幂等去重。
- 搜索算法 `clique.search`（BronKerbosch / DegeneracyOrder / CancellationToken / ResumeState / SearchResult / ResourceExhaustedException）：带枢轴 BK 递归 + 退化排序外层驱动；取消与预算截断；稳定续扫状态。
- 界与证书 `clique.bound`（BoundsCertificate / GreedyColoring / MaximalityCertificate / CliqueValidator）：omega 下界实例与贪心着色上界；极大性阻挡点证书；独立团/极大性判定。
- 穷举对照 `clique.reference`（BruteForceMaximalCliques）：独立的 2^n 子集穷举参考真值，不经过被测核心。
- 配置 `clique.config` + `src/main/resources/clique/default.properties`：步数预算、枢轴策略、日志详细度、穷举顶点数上限。
- 运行日志 `clique.run`（RunLog）：运行编号 + 序号 + 事件 + 中间状态 + 判断理由。
- 错误分类 `clique.error`（ErrorCategory / CliqueException）：INPUT_ERROR / STATE_CONFLICT / RESOURCE_EXHAUSTED / COMPUTATION_FAILED。
- 命令行 `clique.cli`（Main）：演示、随机图、边表文件、预算、续扫、穷举对拍。

## 关键语义

### 极大 != 最大
极大团只要求不可扩展（任何团外顶点都与团内某点不相邻），不要求规模最大。
`MaximalityCertificate` 对每个团外顶点给出一个"阻挡点"作为可独立复核的证据；
`BoundsCertificate` 用贪心着色数给出 omega 上界、用已找到的最大团给出下界。
测试 `BoundsCertificateTest.maximalDoesNotImplyMaximum` 固化了一个规模为 2 的极大团
与 omega=3 共存的情形。枚举完整时，全部极大团中的最大者即为最大团（lower == omega）。

### 退化排序不遗漏团
外层按退化序（反复摘最小度顶点）驱动：对序中第 i 个顶点 v，
递归 `BK({v}, N(v) 交后继, N(v) 交前驱)`。每个极大团 C 恰在
`min{j : order[j] in C}` 处被报告一次，因此输出天然不重不漏。
正确性由穷举对照实证：全部 64 张 4 点图、1024 张 5 点图、54 组种子随机图
与 2^n 子集穷举参考逐一相等（`CrossCheckTest`）。

### 取消与稳定续扫状态
取消（`CancellationToken`）不是错误：返回 `completed=false` 并携带 `ResumeState`。
提交粒度为外层下标：下标 i 的产出先缓冲、递归完整结束才提交；中途取消丢弃缓冲，
以 `nextOuterIndex=i` 形成续扫状态。由于不同外层下标产出的团集合两两不相交，
部分结果与续扫结果的并集恰好等于全量结果（测试断言既相等又不重叠）。
续扫状态合法范围：同一图（n、edgeCount、fingerprint 一致）且 `0 <= nextOuterIndex <= n`，
违反抛 `STATE_CONFLICT`。状态可编码为字符串（`BKRS1:n:m:fp:next:emitted`）跨进程传递。
步数预算耗尽抛 `RESOURCE_EXHAUSTED`，异常内携带同样可续扫的部分结果。

### 错误类别
- `INPUT_ERROR`：自环、越界顶点、非对称邻接、非法配置、非法续扫串、穷举超界
- `STATE_CONFLICT`：续扫状态与图不一致或下标越界
- `RESOURCE_EXHAUSTED`：递归步数预算耗尽（携带部分结果）
- `COMPUTATION_FAILED`：内部不变式破坏（保留类别；当前由证书 verify 返回 false 暴露不一致）

### 运行日志
每次运行有唯一 runId（`run-<序号>-<纳秒十六进制>`），事件包括
`RUN_START`（图规模、退化度、配置快照）、`BUFFER_COMMIT`（每个外层下标的提交数，verbose）、
`CANCEL_DETECTED`（位置与理由）、`BUDGET_EXHAUSTED`（步数与上限）、`RUN_COMPLETE`（计数）。
测试断言所有条目携带 runId 且关键事件的理由字段非空，可按 runId 重放定位。

## 构建与验证

```sh
mvn test          # 56 个单元/对照测试
mvn package       # 生成 target/bk-pivot-clique-1.0.0.jar
./verify.sh       # 一键：测试 + 打包 + CLI 冒烟（演示图穷举对拍、预算耗尽与续扫）
```

## 示例调用

```sh
# 内置重叠团演示图 + 穷举对拍
java -jar target/bk-pivot-clique-1.0.0.jar --demo --brute-check

# 随机图 G(60, 0.6) 种子 11，限制 2000 步（触发 RESOURCE_EXHAUSTED，退出码 3）
java -jar target/bk-pivot-clique-1.0.0.jar --random --n 60 --p 0.6 --seed 11 --max-steps 2000

# 用打印出的续扫状态继续（同一图参数）
java -jar target/bk-pivot-clique-1.0.0.jar --random --n 60 --p 0.6 --seed 11 \
  --resume 'BKRS1:60:1036:1334648639:5:863'

# 边表文件（"u v" 每行一条，可选 "n <count>" 头，# 注释）
java -jar target/bk-pivot-clique-1.0.0.jar --file edges.txt --brute-check
```

退出码：0 成功；2 输入错误；3 资源耗尽（已打印续扫状态）；1 其他失败。

## 配置项（default.properties，可用 --config 覆盖）

- `clique.maxSteps`（默认 100000000）：递归步数预算
- `clique.pivot`（默认 maxIntersection）：枢轴策略，可选 first
- `clique.verbosity`（默认 summary）：日志详细度，可选 verbose / debug
- `clique.bruteForceMaxN`（默认 24）：穷举对照允许的最大顶点数

## 依赖锁定

仅测试依赖 JUnit Jupiter 5.10.2（`pom.xml` 中 `<junit.version>` 固定）；
插件 maven-compiler-plugin 3.13.0、maven-surefire-plugin 3.2.5、maven-jar-plugin 3.4.1 均固定版本。
运行期零第三方依赖（BigInteger 为 JDK 自带）。

## 已验证结果（本机真实执行）

- `mvn test`：8 个测试类 56 个测试全部通过（含 64+1024 张小图穷举对照、取消-续扫链式一致性）。
- `--demo --brute-check`：5 个极大团与穷举参考一致，omega 界 [3,3] 收紧。
- `--random --n 60 --p 0.6 --seed 11 --max-steps 2000`：RESOURCE_EXHAUSTED，提交 863 个团；
  续扫得 3321 个，合计 4184，与不限步全量运行 4184 完全一致。

## 剩余限制

- 单线程；极大团数量本身可指数增长（Moon-Moser 上界 3^(n/3)），大图受 `maxSteps` 预算约束。
- 取消/预算的提交粒度是外层下标：单个下标内的产出未提交前会因取消被丢弃（续扫后重算，保证不重不漏）。
- 续扫状态与图指纹绑定，指纹为哈希（理论上存在碰撞可能，配合 n 与边数联合校验）。
- 穷举对照限 n <= 24（可配置到 30），仅用于小图验证。
- 空图（n=0）约定返回 0 个极大团（不报告空团），搜索与穷举参考一致采用该约定。
