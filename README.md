# mwis-td-service

纯后端服务：给定**树分解**上的**加权最大独立集**（Weighted Maximum Independent Set）
精确求解器。Java 21 + Maven 多模块 + `BigInteger` 任意精度权重，无任何前端、
无容器、无外部业务系统；所有数据均为本地合成夹具。

## 模块结构

| 模块 | 职责 |
|---|---|
| `mwis-graph` | 图与树分解契约；分解合法性验证（边覆盖、顶点连通性、树结构） |
| `mwis-core` | nice 树分解转换 + 动态规划求解（join 去重、回溯、表项预算） |
| `mwis-bounds` | 上界（正权和、团覆盖界）与解证书独立验证 |
| `mwis-exhaustive` | 全子集穷举参照求解器（独立实现，作交叉校验神谕） |
| `mwis-service` | 服务编排：请求身份、分阶段日志、失败分类、证书与交叉验证 |
| `mwis-cli` | 命令行入口（JSON 请求/报告）与确定性夹具生成器 |

## 构建、测试、运行

```bash
./scripts/build.sh                 # 构建全部模块并产出可执行 jar
./scripts/test.sh                  # 运行全部测试（37 个）
./scripts/verify.sh                # 一键：构建 + 测试 + 生成夹具 + 跑全部示例
./scripts/run-example.sh examples/request-path.json   # 求解单个请求
./scripts/lock-deps.sh             # 重新生成 DEPENDENCIES.lock
```

要求：本机 OpenJDK 21 与 Maven（仅构建期），运行期只需 JRE。网络仅用于
Maven Central 依赖下载。

## 请求格式（JSON）

```json
{
  "requestId": "demo-path-4",
  "graph": { "vertexCount": 4, "edges": [[0,1],[1,2],[2,3]] },
  "weights": ["3", "2", "5", "1"],
  "decomposition": {
    "bags": [[0,1],[1,2],[2,3]],
    "treeEdges": [[0,1],[1,2]],
    "root": 0
  },
  "options": { "tableEntryBudget": 100000, "runExhaustiveCrossCheck": true }
}
```

- 顶点编号 `0..n-1`；权重为十进制字符串，按 `BigInteger` 解析，支持负权与超 long 范围。
- `tableEntryBudget` 可省略（不限制）；`runExhaustiveCrossCheck` 仅在 `n ≤ 24` 时执行。

运行：`java -jar mwis-cli/target/mwis-cli-1.0.0.jar solve <request.json>`。
人类可读日志走 stderr，机器可读 JSON 报告走 stdout。退出码：
`0` 成功（含 `CROSSCHECK_SKIPPED`）、`2` 请求被拒绝、`3` 预算/袋宽超限、
`4` 交叉验证不一致、`1` 其他内部错误。

重新生成示例夹具：`java -jar ... generate examples`（确定性种子，可复现）。

## 核心算法

1. **验证**（`mwis-graph`）：拒绝非法分解——袋邻接必须是树；每条图边必须被某个
   袋完整覆盖；每个顶点出现的袋集合必须构成连通子树（running intersection）。
2. **规范化**（`mwis-core`）：任意合法分解转为 nice 分解（LEAF / INTRODUCE /
   FORGET / JOIN，根袋为空）。
3. **DP**：每个节点维护「袋顶点选掩码 → 最优权和」稀疏表。
   - INTRODUCE：仅当所选邻居不冲突时允许选入新顶点；
   - FORGET：两分支取 max；
   - **JOIN：合并时减去交集已选顶点权重一次**，避免重复计权；
   - 空集恒可行，因此**负权顶点自然被弃选，最优值 ≥ 0**。
4. **回溯**：自根向下按表值重放决策，重建一个最优独立集，并内部复核
   「集合权重 == 表值」。
5. **证书与界**（`mwis-bounds`）：独立检查所返回集合确为独立集且权重一致；
   团覆盖上界与解值相等时给出 `PROVEN_OPTIMAL`，否则把 gap 列入
   `uncertainNotes`（不确定结论单列，不混入失败）。
6. **交叉验证**（可选）：与 `mwis-exhaustive` 的全子集穷举比对最优值。

## 失败分类

请求级失败以机器可读类别返回（`SolveReport.failure.category`）：

| 类别 | 含义 |
|---|---|
| `WEIGHT_LENGTH_MISMATCH` | 权重数量与顶点数不符 |
| `DECOMPOSITION_NOT_A_TREE` | 袋邻接不连通或成环 |
| `DECOMPOSITION_STRUCTURE_INVALID` | 根/边引用越界、重复树边 |
| `DUPLICATE_VERTEX_IN_BAG` | 袋内顶点重复 |
| `BAG_VERTEX_OUT_OF_RANGE` | 袋引用不存在的顶点 |
| `EDGE_NOT_COVERED` | 存在未被任何袋覆盖的图边 |
| `VERTEX_OCCURRENCES_DISCONNECTED` | 顶点出现的袋不构成连通子树 |
| `EMPTY_DECOMPOSITION` | 分解没有任何袋 |

运行期状态（`SolveReport.status`）：`OK_PROVEN` / `OK_BOUND_INCONCLUSIVE` /
`REJECTED` / `BUDGET_EXCEEDED` / `BAG_TOO_WIDE` / `CROSSCHECK_MISMATCH` /
`CROSSCHECK_SKIPPED` / `INTERNAL_ERROR`。

每条日志携带 `[requestId][阶段][#序号]`，阶段为
`VALIDATE → PREPARE → SOLVE → CERTIFY → CROSSCHECK`，报告含服务版本
`mwis-td-service/1.0.0` 与求解统计（表项数、各类节点数、预算）。

## 测试与复核要点

- 小图**手算**最优值（路径、三角形、完全图、全负权）；
- 25 组随机 2-树上 **DP 对照穷举**（穷举为独立实现，非被测核心生成）；
- 破坏分解（边未覆盖 / 顶点出现不连通 / 成环 / 袋内重复…）逐类断言拒绝类别；
- 回溯集合断言：确为独立集、权重与表值一致；
- 预算：表项计数不超过预算，超预算抛专用异常并映射为 `BUDGET_EXCEEDED`；
- 服务层：请求身份贯穿日志、版本戳、不确定结论单列。

## 关键取舍与已知限制

- **袋宽上限 62**：DP 表键为 `long` 位掩码；更宽的袋以 `BAG_TOO_WIDE` 拒绝。
  这是实现取舍而非算法限制。
- **表全量驻留内存**：为支持精确回溯，所有节点表保留至求解结束；
  大规模实例应设置 `tableEntryBudget` 兜底。
- **穷举对照限 24 顶点**：`2^n` 枚举的实际可行性边界；超限记
  `CROSSCHECK_SKIPPED` 并列入不确定项。
- **上界为可计算界**：团覆盖界不保证紧致，界不闭合时结论为
  `OK_BOUND_INCONCLUSIVE`（DP 本身仍精确）。
- 分解需由调用方提供；本服务**不**计算树分解，只验证并使用它。

## 依赖锁定

全部依赖版本集中于根 `pom.xml` 的 `<properties>` 并配合
`maven-enforcer-plugin`（Java 21、依赖收敛）强制；`DEPENDENCIES.lock`
记录实际解析结果（运行期仅 Jackson 2.17.2，测试期 JUnit 5.10.2），
由 `./scripts/lock-deps.sh` 再生成，diff 即可审计版本漂移。
