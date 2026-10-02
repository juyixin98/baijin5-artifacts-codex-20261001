# 设计说明

## 1. 总体数据流

```
request.txt --parse_request--> grammar checks ──validate_job──> contract OK
                                      │                            │
                          request-level Failures            core::evaluate_job
                                                                   │
                                            BatchingPlanner (memory slots)
                                                                   │
                                       per batch: ProductTree.build
                                                  + remainder descent
                                                                   │
                                       Horner cross-check (independent)
                                                                   │
                                          JobReport(results/unc/fail)
                                                                   │
                                     render_text / render_json (+steps)
```

## 2. 乘积树与余式树

- 叶子是首一一次式 `Lᵢ(x)=x-rᵢ`（低次到高次系数 `[-r,1]`）；奇数个节点时
  右子树缺省，孤节点原样上提。
- 自底向上 Karatsuba 卷积得到内部节点与根 `M(x)=∏Lᵢ`。
- 求值时自顶向下：`g ← f`；根处 `g ← f mod M`；每个节点用该节点多项式对父
  余式取余。叶子余式 `f mod (x-r)` 的常数项即 `f(r)`。
- 重复点只产生一个叶子因子，却对应多个输出槽，因此除数始终首一、且不会对
  `(x-r)` 除两次；这从构造上消除“重复点除零”。

## 3. 快速单子除法

除数首一时使用反转级数求逆：令 `m=deg f, d=deg g, n=m-d+1`，

```
q^R = f^R · (g^R)^{-1} (mod x^n),   r = f − q·g  (截断到 deg d−1)
```

`g^R` 常数项为 1，Newton 倍乘 `r ← r(2−g^R r)` 给出模 `x^{2k}` 的逆。整数与
域共用同一模板代码，数位运算全部由 `Ops` 注入，保证两套域算法路径一致但
类型不混用。

## 4. 内存预算与分批

某层节点系数总数约为 `b+1`；`L=⌈log2 b⌉+1` 层乘积树、根、被除式副本与下降
余式合计约 `(L+3)(b+1)` 个逻辑槽，再计入 vector 扩容与 `memory_fudge`。
规划器二分搜索满足估算 ≤ `memory_limit` 的最大批大小 `cap`，把点序列切成
若干段；输出按全局索引写回，保证“怎么分批结果都一样”。

## 5. 数值契约与失败隔离

素性用 uint64 确定性 Miller–Rabin 见证集；模上界 `2^63` 保证任意两数位之积
在 `__uint128` 内安全。语法错误（请求层）与语义/值错误（作业层）分别上报；
作业失败不阻止同请求内其他作业执行。

## 6. 可解释性

每条轨迹含 request/job、模块、`file:line`、步骤键与参数。报告三段分离：
确定 `RESULT`、终结 `FAILURE`、非终结 `UNCERTAIN`。交叉校验与基准拟合这类
“无法保证等于理论值”的结论只进 `UNCERTAIN`，不污染确定结果。

## 7. 独立性

测试参考 `tests/harness/reference.h` 与内核 `core::reference` 是两份独立
Horner；测试再持有第三份参考用于断言，且所有期望数值由 Horner 直接产生，
不读取被测树代码。基准只使用进程内合成数据，无外部参与者。
