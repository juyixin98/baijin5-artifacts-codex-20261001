# rescheck — CNF 不可满足性消解证明的流式独立检查器

纯本地后端项目：给定一个 CNF 不可满足性的消解证明（文本流），独立逐步
重新推导并判定证明是否成立。不依赖任何生产账号或真实业务数据；所有测试
数据均为本仓库内的手写夹具或由 Rust 程序确定性生成的合成夹具。

## 模块关系

```
src/syntax.rs   逻辑语法：文字(Literal)、子句(Clause)，排序去重的规范化，
                重复文字作为语法错误拒绝
src/resolve.rs  推理核心：命题消解规则 resolve(左父, 右父, 枢轴) -> 归结式；
                校验枢轴异号出现、归结式非重言
src/proof.rs    证明记录：行文本格式（c 公理 / r 消解 / d 删除）的流式解析器
src/checker.rs  独立检查器：维护活子句表，逐步重推导并与声明归结式比对；
                资源限制；产出可解释的 CheckReport（serde JSON）
src/main.rs     rescheck CLI：文件 -> JSON 报告，退出码 0/1/2/3
src/bin/gen_fixtures.rs  确定性生成链式证明夹具（数据生成只用 Rust）
tests/checker_tests.rs   独立集成测试：断言具体判定与失败类别
fixtures/*.proof         手写有效/篡改/悬空引用/删除引用/重复文字等夹具
```

依赖（`Cargo.toml` 锁定，见 `Cargo.lock`）：`serde 1`（derive）、
`serde_json 1`，仅用于报告序列化；检查逻辑本身零依赖。工具链 Rust 1.98。

## 算法假设

- 子句为文字的集合：内部排序存储；证明作者声明的子句不得含重复变量
  （`x v x`、`x v ~x` 均按 `duplicate_literal` 拒绝）。
- 消解合法性：枢轴变量在两个父子句中异号出现；归结式不得为重言式
  （即枢轴必须是两父子句间唯一互补的变量）；声明归结式必须与检查器
  独立计算的归结式完全一致，且不得仍含枢轴变量。
- 删除语义：`d <id>` 立即释放该子句；之后任何引用按 `deleted_parent`
  拒绝，重复删除同样拒绝。证明在推出空子句时立即终止验证。
- 资源假设：超过 `max_steps` / `max_clause_lits` / `max_total_lits`
  任一限制时判定为 `unverified`（不确定），绝不判 `verified`；
  流结束仍未推出空子句同样为 `unverified`。

## 证明记录格式

```text
c <id> <lit>... 0                     公理（输入子句）
r <id> <左父> <右父> <枢轴> <lit>... 0  消解步，显式声明归结式
d <id>                                删除子句
# 注释；文字为 DIMACS 风格有符号整数，0 为子句结束符
```

## 运行与验证

```bash
cargo test                  # 10 单元测试 + 20 集成测试
cargo run --bin gen_fixtures  # 重新生成 fixtures/generated_chain.proof
./verify.sh                 # 一键构建 + 测试 + 全夹具判定核对

# 单个证明检查（退出码 0=verified 1=rejected 2=unverified 3=用法/IO 错误）
cargo run --bin rescheck -- fixtures/valid_simple.proof --request-id demo-1
cargo run --bin rescheck -- fixtures/generated_chain.proof --max-steps 100
```

预期判断方式：`verify.sh` 每行输出 `OK`；`valid_*` 与 `generated_chain`
判 `verified`（退出码 0），四类篡改夹具判 `rejected`（退出码 1）且报告
`failure.class` 分别为 `clause_mismatch` / `dangling_parent` /
`deleted_parent` / `duplicate_literal`，`no_empty_clause` 与资源受限运行
判 `unverified`（退出码 2），原因单列在 `uncertainties` 而非 `failure`。

## 报告可解释性

每份 JSON 报告包含：`request_id`（请求身份）、`checker_version`、
`steps_processed`、逐步日志 `log`（级别、步骤 id、输入位置）、
确定性失败 `failure`（类别 + 步骤 + 位置 + 原因）与不确定结论
`uncertainties`（资源耗尽、未推出空子句），两者严格分列。

## 测试状态

最近一次本地验证：`cargo test` 30/30 通过（10 单元 + 20 集成），
`verify.sh` 全部 OK。无未实现项、无被跳过的测试。
