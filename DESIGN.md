# 设计说明：接口指纹与失效传播

## 1. 数据通路

```
源码 ──frontend.Parse──> AST
AST ──lower.Analyze────> 符号表 / 常量折叠值 / 类型检查 / 每符号显式依赖(Dep)
AST+分析 ──lower.Compile> IR（含泛型 worklist 单态化 spec：mod.fn<int>）
IR  ──lower.LinkProgram─> ir.Program ──interp.Engine.Call──> 结果
分析 ──fp.Compute────────> FingerprintSet（InterfaceHash / ImplHash / Depends）
两次指纹 ──build.diffFingerprints──> 直接变更 + 传播失效集（反向边）
程序 + 外部探针 ──semdiff.RunProbes──> 通过/失败（期望值在夹具中手写）
```

## 2. 指纹组成（确定性，无时间戳）

- `ImplHash = sha256("body" | 规范化AST)`：私有实现体。
- `Signature`：
  - 函数：参数名:类型、返回类型、是否泛型；
  - **公开**常量：`const:<type>=<折叠值摘要>`；私有常量不含值。
- `Depends`：分析阶段收集的**显式**依赖（有序、去重）：
  - `inline-const` → 直接引用的公开常量；
  - `generic-body` → 调用的泛型模板；
  - `call` → 普通调用（目标为普通函数时，其体不在指纹内，故普通调用边
    不携带体哈希，仅在目标接口变化时才导致本符号重算）。
- `InterfaceHash = sha256(SemVersion, "iface", key, Signature,
        [泛型时附带 ImplHash], 各依赖 "kind:target@依赖的InterfaceHash")`。

依赖哈希按定点迭代展开，因此**传递性**地包含内联常量/泛型体变化。

关键性质：

1. 普通函数体只进 `ImplHash`，故改私有体：`InterfaceHash` 不动，
   反向传播时**不从该符号向外扩展**。
2. 泛型模板体额外并入自身接口，因此泛型体一改，`generic-body` 边指向的
   所有实例化方接口哈希随之变化。
3. 公开常量值并入签名，私有常量值不并入。

## 3. 失效传播算法

`build.diffFingerprints`：

1. 逐键比较新旧指纹，分类直接变更：
   - 仅 `ImplHash` 变 → `private_body_change`；
   - `InterfaceHash` 变且签名变 → `fingerprint_changed`（公开类型/内联常量值）；
   - `InterfaceHash` 变但签名未变 → `semantic_change`（依赖的内联常量/泛型体）；
   - 增删符号 → `symbol_added/removed`。
2. 用新指纹的 `Depends` 建**反向边**。
3. 只有**接口变化集合**作为 BFS 种子沿反向边传播；体变更只标记符号自身。
4. 传播到达集合即“最小必要失效集”；其拥有模块进入重建集，其余模块进入复用集。

因此内联常量 `pricing.BULK_FACTOR` 变化得到的精确集合是
`{BULK_FACTOR, bulk_price, report.quote, app.main}`，而未使用它的
`unit_price` 不失效。

## 4. 语义版本

`fp.SemVersion` 参与每个接口哈希，且缓存首字段单独保存。缓存加载先比版本：
不同版本直接拒绝（`stale_semantic_version`）并全量重建，保证“旧指纹不能跨
编译语义版本复用”。升级语义时只改该常量即可令所有旧缓存失效。

## 5. 泛型单态化

`lower.Compile` 先编译所有普通函数；代码生成时遇到泛型调用，按实参类型推断
`T`（当前支持单类型变量）生成 `模块.fn<int>` 规格并入队。队列编译过程中若
规格体再调用其他泛型则继续入队，直到不动点。规格在全程序命名空间中缓存
（键含类型实参），调用发射 `OpGenericCall`。

## 6. 独立证据原则

- `tests/` 是外部包，不共享内部测试辅助；合成工程在 `t.TempDir()` 中生成。
- 期望值为手算常量（如 `main(3)=362`、常量改后 `=543`），并断言失败类别
  （arity/type、division_by_zero 等），不是“能调用就算通过”。
- 探针 `testdata/probes.json` 的期望值独立编写；`semdiff` 只负责执行与比对。
- 指纹测试用 `crypto/sha256` 独立确认摘要是 16 位十六进制的真实 SHA-256 截断，
  并验证“体改接口不变 / 常量值改接口变 / 泛型体改接口变”三条契约。

## 7. 已知限制（如实说明）

- 单类型变量 `T`；泛型不支持多类型参数、泛型约束或数组/容器类型。
- 无可变全局状态、闭包、循环（提供 `if/else`；循环可用递归表达，深度上限 256）。
- 常量折叠仅支持整型/布尔/字符串字面量与整型算术（跨模块、含循环检测）。
- 缓存粒度为“重新分析全工程 + 按模块复用/重链”；IR 已可序列化但当前不做
  模块级字节码增量装载（最小失效集与模块复用集已按符号精确计算）。
- 字符串拼接在解释器中支持（`+`），但常量折叠不折叠字符串 `+`。
- 不做逃逸分析/优化；目标是接口指纹与失效传播的可验证语义，非优化编译器。
