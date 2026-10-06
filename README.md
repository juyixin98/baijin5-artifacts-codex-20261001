# qe-fol: 有限枚举域上的一阶公式量词消去与模型验证

在有限枚举域上，把一阶公式中的量词按域展开（`forall x. φ` 展开为各域元
素上 `φ[c/x]` 的合取，`exists x. φ` 展开为析取），并与直接递归求值的
模型检查交叉验证。纯 Rust 实现，数据全部来自本地合成夹具。

## 模块职责

| 模块 | 职责 |
| --- | --- |
| `src/syntax.rs` | 项/公式语法、JSON 表示、避免捕获的绑定变量替换（必要时把绑定变量改名为 `base#n`）、自由变量/量词统计 |
| `src/model.rs` | 有限枚举域模型、谓词解释（元组集合）、结构与策略校验 |
| `src/eval.rs` | 模型检查：闭公式的直接递归求值（参考实现，独立于消去核心） |
| `src/qe.rs` | 推理核心：按域展开的量词消去，带展开预算与深度上限 |
| `src/proof.rs` | 证明记录：运行编号（`run-<毫秒>-<序号>`）、展开/空域/预算耗尽三类步骤及判断理由 |
| `src/check.rs` | 独立检查：仅用 `eval` 与语法扫描验证消去结果（轨迹良构、状态一致、模型上语义等价） |
| `src/error.rs` | 统一错误契约：四类可区分失败类别 |
| `src/lib.rs` | `run_pipeline`：求值 → 消去 → 独立检查，产出完整报告 |
| `src/bin/qecli.rs` | 服务调用示例 CLI：文件进、JSON 报告出，按失败类别退出码 |

## 行为契约

1. 绑定变量替换避免捕获：`subst` 在变量换变量时，若内层绑定变量与
   被代入项的自由变量同名，先把绑定变量改名为全新名字（`a#1`…）再代入。
   常量与变量是分离命名空间，常量代入不需要改名。`tests/qe_cases.rs`
   同时断言两条路径。
2. 空域是否允许明确：模型显式声明 `allow_empty_domain`。空域且未声明
   允许则加载即报 `state_conflict`；允许时 `forall` 真空为真、`exists`
   为假，展开分别得到 `true`/`false` 并记录 `empty_domain_expansion`。
3. 展开预算不足保留未处理量词并标未知：预算按量词展开次数计（全局、
   从左到右消耗，缺省不限）。预算耗尽时未展开的量词原样保留在结果公式
   中，结果为 `status=partial`、`unknown=true`，轨迹记录
   `budget_exhausted`；`status=eliminated` 时结果必然无量词。

## 错误类别（可区分）

| 类别 | 含义 | CLI 退出码 |
| --- | --- | --- |
| `invalid_input` | JSON 解析失败、未知谓词/常量、元数不符、元组引用非域元素 | 2 |
| `state_conflict` | 空域违反策略、域元素/谓词重复、公式含未绑定自由变量 | 3 |
| `resource_exhausted` | 求值/消去递归深度超限 | 4 |
| `computation_failed` | 独立检查发现违例（如原式与展开式真值不一致） | 5 |

## 数据格式

模型（见 `fixtures/model_two.json`）：

```json
{
  "name": "two",
  "allow_empty_domain": false,
  "domain": ["a", "b"],
  "predicates": [
    { "name": "P", "arity": 1, "tuples": [["a"]] },
    { "name": "E", "arity": 1, "tuples": [] }
  ]
}
```

公式用 `op` 标签区分：`{"op":"forall","var":"x","body":…}`、
`{"op":"and","args":[…]}`、`{"op":"not","arg":…}`、
`{"op":"atom","pred":"R","args":[{"var":"x"},{"const":"a"}]}`、
`{"op":"eq","left":…,"right":…}`、`{"op":"true"}` 等。
`fixtures/formulas.json` 是公式束，每条带手写参考答案
`expect: {模型名: 真值}`，供交叉验证测试对照。

## 运行与验证

```bash
# 完整验证：构建 + 全部测试 + CLI 正常/异常示例，日志写入 out/
./scripts/verify.sh

# 或单独执行
cargo test --locked
cargo run --bin qecli -- --model fixtures/model_two.json \
    --formula fixtures/formulas.json --name alt_forall_exists --mode pipeline
```

CLI 三种模式：`eval`（仅模型检查）、`qe`（仅消去）、`pipeline`（消去 +
独立检查，默认）。报告含 `run_id`、真值、结果公式、证明轨迹与检查结
论；失败时 stderr 输出 `{"error":{"kind","message"}}` 并按上表退出。

## 测试与诊断

- `tests/eval_cases.rs`：手写答案的求值测试（交替量词、遮蔽变量、空关
  系、空域策略）。
- `tests/qe_cases.rs`：展开形状、预算契约、捕获避免、空域展开、运行编
  号贯穿轨迹。
- `tests/cross_check.rs`：对全部夹具公式，递归求值、展开公式、手写参
  考答案三方对照；预算扫描验证 partial 契约。
- `tests/errors.rs`：四类失败类别各自触发并断言精确类别。
- `tests/cli_smoke.rs`：真实进程调用，断言退出码与 JSON 契约。
- 每条证明轨迹步骤含 `run_id`、序号、深度、动作与判断理由；
  `out/*.log` 保留可复核的运行记录。

## 依赖与复现

依赖仅 `serde` 与 `serde_json`，版本锁定见 `Cargo.lock`（用 `--locked`
构建即按锁文件解析）。本机原生进程运行，无容器、无网络服务、无生产数
据。

## 限制（未实现项）

- 仅支持闭公式；自由变量会被拒绝（`state_conflict`）。
- 谓词元数 >= 1，不支持 0 元谓词（命题符号）。
- 展开按全称/存在直接展开，不做 CNF/DNF 重写或化简优化；域很大时展开
  规模随域大小与量词嵌套指数增长，只能靠预算截断。
- 消去/求值递归深度有硬上限（见 `src/qe.rs`、`src/eval.rs` 中常量），
  超限报 `resource_exhausted`。
